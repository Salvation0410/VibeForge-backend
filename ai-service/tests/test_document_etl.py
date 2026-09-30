from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
import threading
import time
import zipfile

import httpx
import pytest
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

import ai_service.orchestration.document_etl as document_etl

from ai_service.orchestration.document_etl import (
    DocumentETLError,
    KnowledgeChunk,
    KnowledgeEtlService,
    ParsedSection,
    parse_document,
    split_sections,
    parse_and_split_download,
)
from ai_service.infrastructure.milvus_knowledge import KnowledgeMutationLease
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.infrastructure.spring_knowledge_lease import (
    SpringKnowledgeMutationCoordinator,
)
from ai_service.models.embeddings import EmbeddingOutputError


FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def test_markdown_heading_paths_and_immutable_sections():
    sections = parse_document(FIXTURES / "sample.md", "md")
    assert any(s.source_locator == "产品指南 / 退款" and "七天" in s.content for s in sections)
    assert any(s.source_locator == "产品指南 / 退款 / 时限" for s in sections)
    with pytest.raises(FrozenInstanceError):
        sections[0].content = "changed"


def test_markdown_heading_levels_handle_missing_parents_and_siblings(tmp_path):
    path = tmp_path / "levels.md"
    path.write_text(
        "## Start\none\n### Deep\ntwo\n## Peer\nthree\n"
        "#### Skip\nfour\n# Root\nfive\n### Child\nsix\n### Sibling\nseven",
        encoding="utf-8",
    )
    assert [section.source_locator for section in parse_document(path, "md")] == [
        "Start", "Start / Deep", "Peer", "Peer / Skip", "Root",
        "Root / Child", "Root / Sibling",
    ]


def test_txt_line_ranges_and_unicode_cleanup(tmp_path):
    sections = parse_document(FIXTURES / "sample.txt", "txt")
    assert sections[0].source_locator.startswith("lines 1-")
    assert sections[-1].source_locator == "lines 4-4"
    assert "第四行" in " ".join(s.content for s in sections)
    p = tmp_path / "unicode.txt"
    p.write_text("ＡＢＣ\u200b  内容\r\n第二行", encoding="utf-8")
    cleaned = parse_document(p, "txt")
    assert "ＡＢＣ 内容" in cleaned[0].content
    assert "\u200b" not in cleaned[0].content


@pytest.mark.parametrize("file_type", ["md", "txt"])
def test_empty_text_document_rejected(tmp_path, file_type):
    p = tmp_path / f"empty.{file_type}"
    p.write_text(" \n", encoding="utf-8")
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_EMPTY"):
        parse_document(p, file_type)


def test_signed_link_in_heading_is_redacted_from_locator(tmp_path):
    p = tmp_path / "link.md"
    p.write_text("# https://oss.example.test/doc?Signature=secret\n\nContent", encoding="utf-8")
    sections = parse_document(p, "md")
    assert sections[0].source_locator == "[link]"


def test_docx_title_heading_and_paragraph_locator(tmp_path):
    p = tmp_path / "test.docx"
    doc = Document()
    doc.add_paragraph("售后政策", style="Title")
    doc.add_heading("退款", level=1)
    doc.add_paragraph("订单七天内可退款。")
    doc.save(p)
    sections = parse_document(p, "docx")
    assert any(s.source_locator == "售后政策 / 退款 / paragraph 3" for s in sections)


def test_docx_heading_levels_without_title_and_with_title(tmp_path):
    path = tmp_path / "levels.docx"
    doc = Document()
    for title, level, body in [
        ("Start", 2, "one"), ("Deep", 3, "two"), ("Peer", 2, "three"),
        ("Root", 1, "four"), ("Child", 3, "five"), ("Other", 1, "six"),
    ]:
        doc.add_heading(title, level=level)
        doc.add_paragraph(body)
    doc.add_paragraph("Manual", style="Title")
    doc.add_heading("First", level=1)
    doc.add_paragraph("seven")
    doc.add_heading("Second", level=1)
    doc.add_paragraph("eight")
    doc.save(path)
    assert [section.source_locator.rsplit(" / ", 1)[0] for section in parse_document(path, "docx")] == [
        "Start", "Start / Deep", "Peer", "Root", "Root / Child", "Other",
        "Manual / First", "Manual / Second",
    ]


def test_docx_table_content_follows_document_order(tmp_path):
    path = tmp_path / "table.docx"
    doc = Document()
    doc.add_heading("Policy", level=1)
    doc.add_paragraph("before")
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    table.cell(1, 0).merge(table.cell(1, 1)).text = "Merged"
    table.cell(1, 2).text = "D"
    doc.add_paragraph("after")
    doc.save(path)
    sections = parse_document(path, "docx")
    assert [section.source_locator for section in sections] == [
        "Policy / paragraph 2", "Policy / 表格 1 / 行 1",
        "Policy / 表格 1 / 行 2", "Policy / paragraph 3",
    ]
    assert [section.content for section in sections] == [
        "before", "A | B", "Merged | D", "after",
    ]


def test_docx_nested_table_text_appears_once(tmp_path):
    path = tmp_path / "nested.docx"
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Outer"
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Nested"
    table.cell(0, 1).text = "Peer"
    doc.save(path)
    sections = parse_document(path, "docx")
    assert [section.content for section in sections] == ["Outer", "Nested", "Peer"]
    assert sections[0].source_locator == "表格 1 / 行 1"
    assert sections[1].source_locator == "表格 1 / 行 1 / 列 1 / 嵌套表格 1 / 行 1"
    assert sections[2].source_locator == "表格 1 / 行 1"


def test_docx_nested_table_preserves_surrounding_paragraph_order(tmp_path):
    path = tmp_path / "ordered-nested.docx"
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "before"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "nested"
    cell.add_paragraph("after")
    doc.save(path)
    sections = parse_document(path, "docx")
    assert [section.content for section in sections] == ["before", "nested", "after"]
    assert [section.source_locator for section in sections] == [
        "表格 1 / 行 1",
        "表格 1 / 行 1 / 列 1 / 嵌套表格 1 / 行 1",
        "表格 1 / 行 1",
    ]


def test_docx_nested_table_keeps_cross_column_order(tmp_path):
    path = tmp_path / "cross-column.docx"
    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    table.cell(0, 0).text = "left"
    middle = table.cell(0, 1)
    middle.text = "before"
    middle.add_table(rows=1, cols=1).cell(0, 0).text = "nested"
    middle.add_paragraph("after")
    table.cell(0, 2).text = "right"
    doc.save(path)
    sections = parse_document(path, "docx")
    assert [section.content for section in sections] == [
        "left | before", "nested", "after | right",
    ]


def test_pdf_page_locator_and_encryption(tmp_path):
    p = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with p.open("wb") as out:
        writer.write(out)
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_EMPTY"):
        parse_document(p, "pdf")
    writer.encrypt("secret")
    with p.open("wb") as out:
        writer.write(out)
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_ENCRYPTED"):
        parse_document(p, "pdf")


def test_pdf_extracted_text_keeps_page_number(tmp_path):
    p = tmp_path / "text.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Refund policy) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with p.open("wb") as out:
        writer.write(out)
    sections = parse_document(p, "pdf")
    assert sections[0].source_locator == "page 1"
    assert "Refund policy" in sections[0].content


def test_corrupt_unsupported_and_zip_bomb_rejected(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a pdf")
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_INVALID") as caught:
        parse_document(p, "pdf")
    assert str(p) not in str(caught.value)
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_UNSUPPORTED"):
        parse_document(p, "exe")
    docx = tmp_path / "bad.docx"
    docx.write_bytes(b"not a docx")
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_INVALID"):
        parse_document(docx, "docx")


def test_docx_macro_and_expanded_zip_limit_rejected(tmp_path):
    p = tmp_path / "macro.docx"
    Document().save(p)
    with zipfile.ZipFile(p, "a") as archive:
        archive.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_INVALID"):
        parse_document(p, "docx")
    p = tmp_path / "expanded.docx"
    Document().save(p)
    with zipfile.ZipFile(p, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("extra.bin", b"0" * (16 * 1024 * 1024 + 1))
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_INVALID"):
        parse_document(p, "docx")


def test_splitter_chunk_ids_overlap_and_metadata(settings, tmp_path):
    settings.rag_chunk_size = 1000
    settings.rag_chunk_overlap = 150
    p = tmp_path / "long.txt"
    p.write_text("知识" * 600, encoding="utf-8")
    sections = parse_document(p, "txt")
    chunks = split_sections(
        sections, document_id="42", document_version=3,
        source_name="manual.txt", settings=settings,
    )
    assert len(chunks) > 1
    assert [c.chunk_id for c in chunks] == [f"42:3:{i}" for i in range(len(chunks))]
    assert all(0 < len(c.content) <= 1000 for c in chunks)
    assert chunks[0].content[-150:] == chunks[1].content[:150]
    assert all(c.source_name == "manual.txt" and c.source_locator for c in chunks)
    with pytest.raises(FrozenInstanceError):
        chunks[0].content = "changed"


def test_invalid_overlap_and_empty_document_fail(settings):
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_EMPTY"):
        split_sections((), document_id="42", document_version=1, source_name="x", settings=settings)
    settings.rag_chunk_overlap = settings.rag_chunk_size
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_SPLIT_INVALID"):
        split_sections(parse_document(FIXTURES / "sample.md", "md"), document_id="42", document_version=1, source_name="x", settings=settings)


def test_splitter_redacts_caller_supplied_locator_and_rejects_signed_source_name(settings):
    sections = (ParsedSection("Content", "https://oss.example.test/file?Signature=secret"),)
    chunks = split_sections(
        sections, document_id="42", document_version=1,
        source_name="manual.txt", settings=settings,
    )
    assert chunks[0].source_locator == "[link]"
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_SPLIT_INVALID"):
        split_sections(
            sections, document_id="42", document_version=1,
            source_name="https://oss.example.test/file?Signature=secret", settings=settings,
        )


def test_chunk_capacity_counts_actual_splitter_output(settings, monkeypatch):
    settings.rag_chunk_size = 5
    settings.rag_chunk_overlap = 4
    monkeypatch.setattr(document_etl, "MAX_CHUNKS", 2)
    sections = (
        ParsedSection("hello", "first"),
        ParsedSection("world", "second"),
    )
    chunks = split_sections(
        sections, document_id="42", document_version=1,
        source_name="manual.txt", settings=settings,
    )
    assert [chunk.chunk_id for chunk in chunks] == ["42:1:0", "42:1:1"]
    with pytest.raises(DocumentETLError, match="KNOWLEDGE_DOCUMENT_TOO_LARGE"):
        split_sections(
            (*sections, ParsedSection("third", "third")),
            document_id="42", document_version=1,
            source_name="manual.txt", settings=settings,
        )


@pytest.mark.asyncio
async def test_pipeline_cleans_download_on_success_and_parse_failure(settings, tmp_path):
    class Downloaded:
        def __init__(self, path):
            self.path = path
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            self.path.unlink(missing_ok=True)

    good = tmp_path / "good.txt"
    good.write_text("客服内容", encoding="utf-8")
    chunks = await parse_and_split_download(
        Downloaded(good), file_type="txt", document_id="42",
        document_version=1, source_name="help.txt", settings=settings,
    )
    assert chunks and not good.exists()
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"bad")
    with pytest.raises(DocumentETLError):
        await parse_and_split_download(
            Downloaded(bad), file_type="pdf", document_id="42",
            document_version=1, source_name="help.pdf", settings=settings,
        )
    assert not bad.exists()


@pytest.mark.asyncio
async def test_pipeline_parse_does_not_block_event_loop(settings, tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    timer = threading.Timer(0.5, release.set)
    timer.start()

    def blocking_parse(path, file_type):
        started.set()
        release.wait()
        return (ParsedSection("content", "document"),)

    class Downloaded:
        def __init__(self, path):
            self.path = path
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            self.path.unlink(missing_ok=True)

    monkeypatch.setattr(document_etl, "parse_document", blocking_parse)
    path = tmp_path / "document.txt"
    path.write_text("content", encoding="utf-8")
    task = asyncio.create_task(parse_and_split_download(
        Downloaded(path), file_type="txt", document_id="42",
        document_version=1, source_name="document.txt", settings=settings,
    ))
    try:
        assert await asyncio.to_thread(started.wait, 0.4)
        assert not release.is_set()
    finally:
        release.set()
        timer.cancel()
        await task
    assert not path.exists()


@pytest.mark.asyncio
async def test_cancel_waits_for_parser_before_temp_cleanup(settings, tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    timer = threading.Timer(0.5, release.set)
    timer.start()
    cleaned = False

    def blocking_parse(path, file_type):
        started.set()
        release.wait()
        assert path.exists()
        return (ParsedSection("content", "document"),)

    class Downloaded:
        def __init__(self, path):
            self.path = path
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            nonlocal cleaned
            cleaned = True
            self.path.unlink(missing_ok=True)

    monkeypatch.setattr(document_etl, "parse_document", blocking_parse)
    path = tmp_path / "document.txt"
    path.write_text("content", encoding="utf-8")
    task = asyncio.create_task(parse_and_split_download(
        Downloaded(path), file_type="txt", document_id="42",
        document_version=1, source_name="document.txt", settings=settings,
    ))
    try:
        assert await asyncio.to_thread(started.wait, 0.4)
        task.cancel()
        await asyncio.sleep(0.02)
        assert path.exists()
        assert not cleaned
    finally:
        release.set()
        timer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned and not path.exists()


@pytest.mark.asyncio
async def test_etl_service_indexes_embedded_document_with_original_lease(settings, tmp_path):
    path = tmp_path / "knowledge.txt"
    content = b"refund policy"
    path.write_bytes(content)

    class Downloaded:
        def __init__(self):
            self.path = path
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            self.path.unlink(missing_ok=True)

    class Downloader:
        async def download(self, url, *, expected_sha256, max_bytes):
            assert url == "https://oss.example.test/file?Signature=secret"
            assert expected_sha256 == hashlib.sha256(content).hexdigest()
            assert max_bytes == settings.rag_download_max_bytes
            return Downloaded()

    class Embeddings:
        async def embed_documents(self, texts, **_kwargs):
            assert texts == ["refund policy"]
            return [[0.25, 0.75]]

    class Store:
        async def upsert_document_version(self, document, *, lease):
            self.document = document
            self.lease = lease
            return type("Result", (), {
                "document_id": document.document_id,
                "document_version": document.document_version,
                "chunk_count": len(document.chunks),
                "idempotent": False,
            })()

    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=9,
        expires_at=4102444800.0, proof="secret-proof",
    )
    store = Store()
    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), store)
    result = await service.index(
        document_id="doc-1", document_version=2, file_name="knowledge.txt",
        file_type="TXT", signed_url="https://oss.example.test/file?Signature=secret",
        sha256=hashlib.sha256(content).hexdigest(), etl_version="etl-v1", lease=lease,
    )

    assert result.chunk_count == 1
    assert store.lease is lease
    assert store.document.embedding_model_version == settings.rag_embedding_model
    assert store.document.content_hash == hashlib.sha256(content).hexdigest()
    assert store.document.chunks[0].content_hash == hashlib.sha256(
        b"refund policy"
    ).hexdigest()
    assert not path.exists()


@pytest.mark.asyncio
async def test_etl_service_delete_passes_original_lease(settings):
    class Store:
        async def delete_document(self, document_id, document_version, *, lease):
            self.call = (document_id, document_version, lease)

    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-2", operation="DELETE", fence=10,
        expires_at=4102444800.0, proof="secret-proof",
    )
    store = Store()
    service = KnowledgeEtlService(settings, object(), object(), store)
    await service.delete(document_id="doc-1", document_version=2, lease=lease)
    assert store.call == ("doc-1", 2, lease)


@pytest.mark.asyncio
async def test_etl_service_propagates_download_and_embedding_failures(settings, tmp_path):
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=1,
        expires_at=time.time() + 60, proof="proof",
    )

    class FailingDownloader:
        async def download(self, *_args, **_kwargs):
            from ai_service.infrastructure.knowledge_download import KnowledgeDownloadError
            raise KnowledgeDownloadError("KNOWLEDGE_DOWNLOAD_TIMEOUT")

    service = KnowledgeEtlService(settings, FailingDownloader(), object(), object())
    with pytest.raises(RuntimeError, match="KNOWLEDGE_DOWNLOAD_TIMEOUT"):
        await service.index(
            document_id="doc-1", document_version=1, file_name="x.txt",
            file_type="TXT", signed_url="https://oss.test/x", sha256="a" * 64,
            etl_version="etl-v1", lease=lease,
        )

    path = tmp_path / "x.txt"
    path.write_text("content", encoding="utf-8")

    class Downloaded:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            path.unlink(missing_ok=True)
        @property
        def path(self):
            return path

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return Downloaded()

    class FailingEmbeddings:
        async def embed_documents(self, _texts, **_kwargs):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE")

    service = KnowledgeEtlService(settings, Downloader(), FailingEmbeddings(), object())
    with pytest.raises(EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_UNAVAILABLE"):
        await service.index(
            document_id="doc-1", document_version=1, file_name="x.txt",
            file_type="TXT", signed_url="https://oss.test/x", sha256="a" * 64,
            etl_version="etl-v1", lease=lease,
        )
    assert not path.exists()


@pytest.mark.asyncio
async def test_etl_service_propagates_store_failure(settings, tmp_path):
    path = tmp_path / "store.txt"
    path.write_text("content", encoding="utf-8")
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX",
        fence=1, expires_at=time.time() + 60, proof="proof",
    )

    class Downloaded:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            path.unlink(missing_ok=True)
        @property
        def path(self):
            return path

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return Downloaded()

    class Embeddings:
        async def embed_documents(self, _texts, **_kwargs):
            return [[0.1, 0.2]]

    class Store:
        async def upsert_document_version(self, _document, *, lease):
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE")

    service = KnowledgeEtlService(settings, Downloader(), Embeddings(), Store())
    with pytest.raises(
        MilvusKnowledgeError, match="KNOWLEDGE_VECTOR_STORE_UNAVAILABLE"
    ):
        await service.index(
            document_id="doc-1", document_version=1, file_name="store.txt",
            file_type="TXT", signed_url="https://oss.test/x", sha256="a" * 64,
            etl_version="etl-v1", lease=lease,
        )
    assert not path.exists()


@pytest.mark.asyncio
async def test_etl_service_budget_exceeded_does_not_call_store(settings, tmp_path):
    settings.rag_max_embedding_elements = 2
    path = tmp_path / "budget.txt"
    path.write_text("content", encoding="utf-8")

    class Downloaded:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            path.unlink(missing_ok=True)
        @property
        def path(self):
            return path

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return Downloaded()

    class Embeddings:
        calls = 0
        async def embed_documents(self, _texts, **_kwargs):
            self.calls += 1
            return [[0.1, 0.2, 0.3]]

    class Store:
        called = False
        async def upsert_document_version(self, *_args, **_kwargs):
            self.called = True

    embeddings, store = Embeddings(), Store()
    service = KnowledgeEtlService(settings, Downloader(), embeddings, store)
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX",
        fence=1, expires_at=time.time() + 60, proof="proof",
    )
    with pytest.raises(
        EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED"
    ):
        await service.index(
            document_id="doc-1", document_version=1, file_name="budget.txt",
            file_type="TXT", signed_url="https://oss.test/x", sha256="a" * 64,
            etl_version="etl-v1", lease=lease,
        )
    assert embeddings.calls == 1
    assert not store.called


@pytest.mark.asyncio
async def test_etl_semaphore_limits_concurrency_and_releases_on_cancel(
    settings, monkeypatch,
):
    entered: list[int] = []
    first_entered = asyncio.Event()
    block = asyncio.Event()

    async def parse(*_args, **kwargs):
        version = kwargs["document_version"]
        return (KnowledgeChunk(
            chunk_id=f"doc-1:{version}:0", document_id="doc-1",
            document_version=version, chunk_index=0, content="content",
            source_name="x.txt", source_locator="lines 1-1",
        ),)

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return object()

    class Embeddings:
        async def embed_documents(self, _texts, **_kwargs):
            entered.append(len(entered) + 1)
            if len(entered) == 1:
                first_entered.set()
                await block.wait()
            return [[0.1, 0.2]]

    class Store:
        async def upsert_document_version(self, document, *, lease):
            return type("Result", (), {
                "document_id": document.document_id,
                "document_version": document.document_version,
                "chunk_count": 1, "idempotent": False,
            })()

    monkeypatch.setattr(document_etl, "parse_and_split_download", parse)
    service = KnowledgeEtlService(
        settings, Downloader(), Embeddings(), Store(),
        semaphore=asyncio.Semaphore(1),
    )
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX",
        fence=1, expires_at=time.time() + 60, proof="proof",
    )

    async def index(version):
        return await service.index(
            document_id="doc-1", document_version=version, file_name="x.txt",
            file_type="TXT", signed_url="https://oss.test/x", sha256="a" * 64,
            etl_version="etl-v1", lease=lease,
        )

    first = asyncio.create_task(index(1))
    await first_entered.wait()
    second = asyncio.create_task(index(2))
    await asyncio.sleep(0.02)
    assert entered == [1]
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 0.5)
    assert entered == [1, 2]


def spring_lease_response(lease, **overrides):
    data = {
        "verified": True,
        "current": True,
        "scope": lease.scope,
        "operationId": lease.operation_id,
        "operation": lease.operation,
        "fence": lease.fence,
        "expiresAt": lease.expires_at,
    }
    data.update(overrides)
    return {"code": 0, "data": data, "message": "ok"}


class LeaseResponseStream(httpx.AsyncByteStream):
    def __init__(self, *parts: bytes):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


@pytest.mark.asyncio
async def test_spring_coordinator_validates_on_hold_and_every_assertion():
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=7,
        expires_at=time.time() + 60, proof="never-log-this-proof",
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=spring_lease_response(lease))

    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(handler),
    )
    async with coordinator.hold(
        lease, scope="document:doc-1", operation="upsert"
    ) as permit:
        await permit.assert_current()
    await coordinator.close()

    assert len(requests) == 2
    assert requests[0] == {
        "scope": "document:doc-1", "operationId": "op-1",
        "operation": "INDEX", "fence": 7,
        "expiresAt": lease.expires_at, "proof": "never-log-this-proof",
    }


@pytest.mark.asyncio
async def test_spring_coordinator_health_uses_read_only_endpoint():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "code": 0, "data": {"ready": True}, "message": "ok",
        })

    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(handler),
    )
    assert await coordinator.ping()
    await coordinator.close()
    assert requests[0].method == "GET"
    assert requests[0].url.path == (
        "/api/internal/customer-service/knowledge-mutation-leases/health"
    )
    assert requests[0].content == b""


@pytest.mark.asyncio
async def test_spring_coordinator_rejects_internal_action_mismatch_before_network():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="DELETE", fence=7,
        expires_at=time.time() + 60, proof="proof",
    )
    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_MUTATION_LEASE_INVALID"):
        async with coordinator.hold(
            lease, scope="document:doc-1", operation="upsert"
        ):
            pass
    await coordinator.close()
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(404),
    httpx.Response(200, json={"code": 0, "data": {"verified": False}}),
])
async def test_spring_coordinator_fails_closed_without_leaking_response(response):
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=7,
        expires_at=time.time() + 60, proof="never-log-this-proof",
    )
    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(lambda _request: response),
    )
    with pytest.raises(MilvusKnowledgeError) as caught:
        async with coordinator.hold(
            lease, scope="document:doc-1", operation="upsert"
        ):
            pass
    await coordinator.close()
    assert str(caught.value) == "KNOWLEDGE_MUTATION_LEASE_INVALID"
    assert "never-log-this-proof" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"verified": False},
    {"current": False},
    {"scope": "document:other"},
    {"operationId": "other-op"},
    {"operation": "DELETE"},
    {"fence": 8},
    {"expiresAt": 1.0},
])
async def test_spring_coordinator_rejects_each_response_field_mismatch(overrides):
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=7,
        expires_at=time.time() + 60, proof="proof",
    )
    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json=spring_lease_response(lease, **overrides)
            )
        ),
    )
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_MUTATION_LEASE_INVALID"):
        async with coordinator.hold(
            lease, scope="document:doc-1", operation="upsert"
        ):
            pass
    await coordinator.close()


@pytest.mark.asyncio
async def test_spring_coordinator_rejects_non_json_and_logs_no_secrets(caplog):
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=7,
        expires_at=time.time() + 60, proof="proof-secret-value",
    )
    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-token-secret",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, content=b"vendor body secret", headers={"content-type": "text/plain"}
            )
        ),
    )
    with caplog.at_level("DEBUG"):
        with pytest.raises(MilvusKnowledgeError):
            async with coordinator.hold(
                lease, scope="document:doc-1", operation="upsert"
            ):
                pass
    await coordinator.close()
    for secret in (
        "proof-secret-value", "spring-token-secret", "vendor body secret",
    ):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_spring_coordinator_timeout_is_fail_closed():
    lease = KnowledgeMutationLease(
        scope="document:doc-1", operation_id="op-1", operation="INDEX", fence=7,
        expires_at=time.time() + 60, proof="proof",
    )

    def timeout(request):
        raise httpx.ReadTimeout("vendor body and secret proof", request=request)

    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(MilvusKnowledgeError) as caught:
        async with coordinator.hold(
            lease, scope="document:doc-1", operation="upsert"
        ):
            pass
    await coordinator.close()
    assert str(caught.value) == "KNOWLEDGE_MUTATION_LEASE_INVALID"
    assert "vendor body" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["validate", "health"])
@pytest.mark.parametrize("framing", ["content_length", "chunked"])
async def test_spring_coordinator_rejects_oversized_streamed_responses(
    target, framing,
):
    from ai_service.infrastructure.spring_knowledge_lease import (
        MAX_LEASE_RESPONSE_BYTES,
    )

    def handler(_request):
        if framing == "content_length":
            return httpx.Response(
                200,
                headers={"Content-Length": str(MAX_LEASE_RESPONSE_BYTES + 1)},
                stream=LeaseResponseStream(b"{}"),
            )
        return httpx.Response(
            200,
            stream=LeaseResponseStream(
                b"x" * MAX_LEASE_RESPONSE_BYTES, b"x"
            ),
        )

    coordinator = SpringKnowledgeMutationCoordinator(
        gateway_base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="spring-secret",
        transport=httpx.MockTransport(handler),
    )
    if target == "health":
        assert not await coordinator.ping()
    else:
        lease = KnowledgeMutationLease(
            scope="document:doc-1", operation_id="op-1", operation="INDEX",
            fence=7, expires_at=time.time() + 60, proof="proof",
        )
        with pytest.raises(
            MilvusKnowledgeError, match="KNOWLEDGE_MUTATION_LEASE_INVALID"
        ):
            async with coordinator.hold(
                lease, scope="document:doc-1", operation="upsert"
            ):
                pass
    await coordinator.close()
