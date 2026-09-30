from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import httpx
import pytest

from ai_service.infrastructure.knowledge_download import KnowledgeDownloadError, KnowledgeDownloader


URL = "https://oss.example.test/object?Signature=very-secret-value"
BODY = b"knowledge file"
HASH = hashlib.sha256(BODY).hexdigest()


class StreamingBody(httpx.AsyncByteStream):
    def __init__(self, *parts, error=None):
        self.parts = parts
        self.error = error

    async def __aiter__(self):
        for part in self.parts:
            yield part
        if self.error:
            raise self.error


async def public_resolver(host: str) -> list[str]:
    return ["93.184.215.14"]


def downloader(settings, handler, *, resolver=public_resolver):
    settings.rag_oss_allowed_hosts = "oss.example.test"
    return KnowledgeDownloader(settings, transport=httpx.MockTransport(handler), resolver=resolver)


@pytest.mark.asyncio
async def test_download_streams_and_caller_owns_cleanup(settings, caplog):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=BODY)

    import logging
    with caplog.at_level(logging.DEBUG):
        result = await downloader(settings, handler).download(URL, expected_sha256=HASH.upper(), max_bytes=100)
    assert result.path.read_bytes() == BODY
    assert result.sha256 == HASH
    assert result.size == len(BODY)
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.215.14"
    assert seen[0].headers["Host"] == "oss.example.test"
    assert seen[0].extensions["sni_hostname"] == "oss.example.test"
    assert seen[0].url.query == b"Signature=very-secret-value"
    assert "very-secret-value" not in caplog.text
    assert "very-secret-value" not in repr(result)
    async with result:
        assert result.path.exists()
    assert not result.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://oss.example.test/file", "https://user:pass@oss.example.test/file",
    "https://oss.example.test:8443/file", "https://other.example.test/file",
    "https://127.0.0.1/file", "https://oss.example.test./file",
])
async def test_rejects_unsafe_url_before_request(settings, url):
    calls = []
    dl = downloader(settings, lambda req: calls.append(req) or httpx.Response(200, content=BODY))
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await dl.download(url, expected_sha256=HASH, max_bytes=100)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0",
    "240.0.0.1", "::1", "fc00::1", "fe80::1", "2001:db8::1",
])
async def test_rejects_non_global_resolved_addresses(settings, address):
    async def resolver(_):
        return [address]
    dl = downloader(settings, lambda _: httpx.Response(200, content=BODY), resolver=resolver)
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)


@pytest.mark.asyncio
async def test_dns_failure_has_stable_safe_error(settings):
    async def resolver(_):
        raise OSError(f"failed resolving {URL}")
    dl = downloader(settings, lambda _: httpx.Response(200, content=BODY), resolver=resolver)
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_DNS_FAILED") as caught:
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)
    assert "very-secret-value" not in str(caught.value)
    assert "very-secret-value" not in repr(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", [
    "http://127.0.0.1/meta", "https://other.example.test/file",
    "https://oss.example.test:444/file",
])
async def test_rejects_redirect_targets(settings, location):
    dl = downloader(settings, lambda _: httpx.Response(302, headers={"Location": location}))
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)


@pytest.mark.asyncio
async def test_revalidates_redirect_dns(settings):
    calls = 0
    async def resolver(_):
        nonlocal calls
        calls += 1
        return ["93.184.215.14"] if calls == 1 else ["10.0.0.2"]
    dl = downloader(settings, lambda _: httpx.Response(302, headers={"Location": "/next"}), resolver=resolver)
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)
    assert calls == 2


@pytest.mark.asyncio
async def test_public_redirect_revalidates_and_succeeds(settings):
    responses = iter([
        httpx.Response(302, headers={"Location": "/new?Signature=second-secret"}),
        httpx.Response(200, content=BODY),
    ])
    dl = downloader(settings, lambda _: next(responses))
    result = await dl.download(URL, expected_sha256=HASH, max_bytes=100)
    result.cleanup()


@pytest.mark.asyncio
async def test_mixed_public_private_dns_answer_rejected(settings):
    async def resolver(_):
        return ["93.184.215.14", "10.0.0.1"]
    dl = downloader(settings, lambda _: httpx.Response(200, content=BODY), resolver=resolver)
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)


@pytest.mark.asyncio
async def test_redirect_limit(settings):
    dl = downloader(settings, lambda _: httpx.Response(302, headers={"Location": "/again"}))
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_REDIRECT_LIMIT"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,body", [
    ({"Content-Length": "101"}, BODY), ({}, b"x" * 101),
])
async def test_rejects_declared_and_streamed_oversize(settings, headers, body, tmp_path, monkeypatch):
    original = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kwargs: original(dir=tmp_path, **kwargs))
    dl = downloader(settings, lambda _: httpx.Response(200, headers=headers, content=body))
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TOO_LARGE"):
        await dl.download(URL, expected_sha256=HASH, max_bytes=100)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_streamed_overflow_and_late_timeout_clean_temp_file(settings, tmp_path, monkeypatch):
    original = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kwargs: original(dir=tmp_path, **kwargs))
    for stream, code in [
        (StreamingBody(b"a" * 60, b"b" * 41), "KNOWLEDGE_DOWNLOAD_TOO_LARGE"),
        (StreamingBody(b"a" * 20, error=httpx.ReadTimeout(URL)), "KNOWLEDGE_DOWNLOAD_TIMEOUT"),
    ]:
        dl = downloader(settings, lambda _: httpx.Response(200, stream=stream))
        with pytest.raises(KnowledgeDownloadError, match=code) as caught:
            await dl.download(URL, expected_sha256=HASH, max_bytes=100)
        assert "very-secret-value" not in repr(caught.value)
        assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_timeout_network_hash_and_input_errors_are_safe(settings, tmp_path, monkeypatch):
    original = tempfile.mkstemp
    monkeypatch.setattr(tempfile, "mkstemp", lambda **kwargs: original(dir=tmp_path, **kwargs))
    for raised, code in [
        (httpx.ReadTimeout(URL), "KNOWLEDGE_DOWNLOAD_TIMEOUT"),
        (httpx.ConnectError(URL), "KNOWLEDGE_DOWNLOAD_NETWORK_ERROR"),
    ]:
        def handler(_):
            raise raised
        with pytest.raises(KnowledgeDownloadError, match=code) as caught:
            await downloader(settings, handler).download(URL, expected_sha256=HASH, max_bytes=100)
        assert "very-secret-value" not in repr(caught.value)
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_HASH_MISMATCH"):
        await downloader(settings, lambda _: httpx.Response(200, content=BODY)).download(
            URL, expected_sha256="0" * 64, max_bytes=100
        )
    assert list(tmp_path.iterdir()) == []
    for bad_hash in ("a" * 63, "g" * 64, " a" * 32):
        with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_INVALID_REQUEST"):
            await downloader(settings, lambda _: httpx.Response(200, content=BODY)).download(
                URL, expected_sha256=bad_hash, max_bytes=100
            )
