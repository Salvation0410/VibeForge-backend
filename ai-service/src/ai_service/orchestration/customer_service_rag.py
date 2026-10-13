from __future__ import annotations

import math
import asyncio
import re
import unicodedata
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Sequence

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import KnowledgeStore, RetrievedChunk
from ai_service.models.base import CustomerServiceContext, GenerationModel
from ai_service.models.embeddings import EmbeddingProvider
from ai_service.models.reranker import RerankedChunk, RerankerProvider
from ai_service.prompts.customer_service import fit_customer_service_contexts
from ai_service.models.openai_compatible import CustomerServiceModelOutputError
from ai_service.orchestration.customer_service_trace import CustomerServiceRagTrace


MAX_QUESTION_CHARS = 4_000
MAX_CONTEXTS = 3
MAX_CONTEXT_CHARS = 1_200
MAX_TOTAL_CONTEXT_CHARS = 3_000
MAX_EXCERPT_CHARS = 400
MAX_LOCATOR_CHARS = 500
NO_ANSWER_TEXT = "暂未找到足够依据回答该问题。"
SYSTEM_FALLBACK_TEXT = "系统暂时无法生成可靠回答，请稍后重试。"


class CustomerServiceRagError(RuntimeError):
    """稳定错误码，禁止携带供应商详情或用户内容。"""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CustomerServiceSource:
    document_id: str
    document_name: str
    document_version: int
    chunk_id: str
    locator: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class CustomerServiceAnswer:
    answered: bool
    answer: str
    sources: tuple[CustomerServiceSource, ...]
    degraded: bool


class CustomerServiceRagService:
    """无持久化的单轮检索与有据回答编排。"""

    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingProvider,
        store: KnowledgeStore,
        reranker: RerankerProvider,
        model: GenerationModel,
    ) -> None:
        self._embedding_dimension = settings.rag_embedding_dimension
        self._retrieval_top_k = settings.rag_retrieval_top_k
        self._final_top_k = min(settings.rag_final_top_k, MAX_CONTEXTS)
        self._min_rerank_score = settings.rag_min_rerank_score
        self._reranker_disabled = settings.rag_reranker_provider == "disabled"
        self._prompt_max_bytes = settings.rag_prompt_max_bytes
        self._embeddings = embeddings
        self._store = store
        self._reranker = reranker
        self._model = model

    def health_ready(self) -> bool:
        """仅检查本地回答链路形状，不调用供应商。"""

        return all((
            callable(getattr(self._embeddings, "embed_query", None)),
            callable(getattr(self._store, "search", None)),
            callable(getattr(self._reranker, "rerank", None)),
            callable(getattr(self._model, "answer_customer_service", None)),
        ))

    async def answer(
        self, question: str, *, trace: CustomerServiceRagTrace | None = None,
    ) -> CustomerServiceAnswer:
        try:
            return await self._answer(question, trace=trace)
        except asyncio.CancelledError:
            if trace:
                trace.finish('cancelled', 'CANCELLED')
            raise

    async def _answer(
        self, question: str, *, trace: CustomerServiceRagTrace | None,
    ) -> CustomerServiceAnswer:
        def stage(name):
            return trace.stage(name) if trace else nullcontext()

        def refused(reason, degraded=False):
            if trace:
                trace.finish('refused', reason, degraded=degraded)
            return _no_answer(degraded=degraded)

        normalized_question = question.strip() if isinstance(question, str) else ""
        # 依次执行向量检索、去重、重排、上下文裁剪和引用校验，防止越界内容进入回答。
        if not normalized_question or len(normalized_question) > MAX_QUESTION_CHARS:
            if trace:
                trace.finish('service_error', 'CUSTOMER_SERVICE_INVALID_REQUEST')
            raise CustomerServiceRagError("CUSTOMER_SERVICE_INVALID_REQUEST")

        try:
            with stage('embedding'):
                vector = await self._embeddings.embed_query(normalized_question)
            if (
                not isinstance(vector, list)
                or len(vector) != self._embedding_dimension
                or not all(
                    isinstance(value, (int, float)) and math.isfinite(float(value))
                    for value in vector
                )
            ):
                raise ValueError
        except Exception:
            if trace:
                trace.finish('service_error', 'CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE')
            raise CustomerServiceRagError(
                "CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE"
            ) from None
        try:
            with stage('retrieval'):
                retrieved = await self._store.search(
                    [float(value) for value in vector], self._retrieval_top_k,
                )
        except Exception:
            if trace:
                trace.finish('service_error', 'CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE')
            raise CustomerServiceRagError(
                "CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE"
            ) from None

        if trace:
            trace.record('retrieved', retrieved if isinstance(retrieved, Sequence) and not isinstance(retrieved, (str, bytes)) else [])
        with stage('filtering'):
            candidates = _filter_and_dedupe(retrieved, trace=trace)
        if not candidates:
            return refused('FILTERED_EMPTY' if retrieved else 'RAW_RETRIEVAL_EMPTY')

        degraded = False
        score_source = 'retrieval_fallback' if self._reranker_disabled else 'reranker'
        try:
            with stage('reranking'):
                ranked = await self._reranker.rerank(
                    normalized_question, candidates, top_n=self._final_top_k,
                )
                ranked = _validate_ranked(ranked, candidates, self._final_top_k)
        except Exception:
            ranked = [
                RerankedChunk(item, float(item.score))
                for item in candidates[:self._final_top_k]
            ]
            # disabled 是明确的运维模式，不属于运行时降级故障。
            degraded = not self._reranker_disabled
            score_source = 'retrieval_fallback'

        if trace:
            trace.set_degraded(degraded)
            trace.record('reranked', ranked, score_source=score_source)
        if not ranked:
            return refused('RERANK_EMPTY', degraded)
        before_threshold = ranked
        if self._min_rerank_score is not None:
            # 降级时不能把重排阈值与 Milvus 原始分数直接比较。
            if degraded:
                if trace:
                    with stage('thresholding'):
                        trace.record('thresholded', ranked, score_source=score_source, kept_ids=set(), drop_reason='rerank_unavailable_with_threshold')
                return refused('RERANK_UNAVAILABLE_WITH_THRESHOLD', True)
            with stage('thresholding'):
                ranked = [item for item in ranked if item.score >= self._min_rerank_score]
            if trace:
                trace.record('thresholded', before_threshold, score_source=score_source, kept_ids={item.chunk.chunk_id for item in ranked}, drop_reason='below_threshold')
            if not ranked:
                return refused('THRESHOLD_EMPTY')

        if trace:
            with stage('thresholding'):
                trace.record('thresholded', before_threshold, score_source=score_source, kept_ids={item.chunk.chunk_id for item in ranked}, drop_reason='below_threshold')
            with stage('selection'):
                trace.record('selected', ranked, score_source=score_source, kept_ids={item.chunk.chunk_id for item in ranked[:MAX_CONTEXTS]}, drop_reason='top_k')
        before_character = [CustomerServiceContext(item.chunk.chunk_id, item.chunk.content) for item in ranked]
        all_chunks = {item.chunk.chunk_id: item.chunk for item in ranked}
        with stage('characterBudget'):
            contexts, chunks_by_id = _bounded_contexts(ranked)
        if trace:
            trace.contexts('characterBudget', before_character, contexts, all_chunks)
        if not contexts:
            return refused('CHAR_CONTEXT_EMPTY', degraded)
        with stage('byteBudget'):
            fitted = fit_customer_service_contexts(
                normalized_question, contexts, max_bytes=self._prompt_max_bytes,
            )
        if fitted is None:
            if trace:
                trace.contexts('byteBudget', contexts, [], chunks_by_id)
            return refused('PROMPT_BUDGET_EXHAUSTED', degraded)
        before_byte = contexts
        contexts = [
            CustomerServiceContext(chunk_id, content)
            for chunk_id, content in fitted if content
        ]
        chunks_by_id = {
            item.chunk_id: chunks_by_id[item.chunk_id] for item in contexts
        }
        if trace:
            trace.contexts('byteBudget', before_byte, contexts, all_chunks)
        if not contexts:
            return refused('BYTE_CONTEXT_EMPTY', degraded)
        reason = 'MODEL_CALL_FAILED'
        try:
            if trace:
                trace.model_called()
            with stage('model'):
                model_answer = await self._model.answer_customer_service(
                    normalized_question, contexts,
                )
            if trace:
                trace.model_answered(model_answer.answered)
            citations = model_answer.cited_chunk_ids
            reason = 'CITATION_INVALID'
            if model_answer.answered:
                if (
                    not model_answer.answer.strip()
                    or not citations
                    or len(citations) > MAX_CONTEXTS
                    or len(citations) != len(set(citations))
                    or any(chunk_id not in chunks_by_id for chunk_id in citations)
                ):
                    if trace:
                        trace.citations(False, citations)
                    raise ValueError
                sources = tuple(_source(chunks_by_id[chunk_id]) for chunk_id in citations)
                if trace:
                    trace.citations(True, citations)
                    trace.finish('answered', 'ANSWERED', degraded=degraded)
                return CustomerServiceAnswer(
                    answered=True,
                    answer=model_answer.answer[:4_000],
                    sources=sources,
                    degraded=degraded,
                )
            if model_answer.answer != "" or citations:
                if trace:
                    trace.citations(False, citations)
                raise ValueError
            return refused('MODEL_REFUSED', degraded)
        except Exception as error:
            if trace:
                trace.finish('service_error', 'MODEL_PARSE_FAILED' if isinstance(error, CustomerServiceModelOutputError) else reason, degraded=degraded)
            return CustomerServiceAnswer(False, SYSTEM_FALLBACK_TEXT, (), degraded)


def _filter_and_dedupe(
    chunks: object, *, trace: CustomerServiceRagTrace | None = None,
) -> list[RetrievedChunk]:
    if not isinstance(chunks, Sequence) or isinstance(chunks, (str, bytes)):
        return []
    valid: list[tuple[int, RetrievedChunk]] = []
    records: list[dict] = []
    for rank, item in enumerate(chunks, 1):
        try:
            score_is_finite = math.isfinite(float(item.score))
        except (AttributeError, TypeError, ValueError, OverflowError):
            score_is_finite = False
        if (
            not isinstance(item, RetrievedChunk)
            or not item.is_active
            or item.document_version < 1
            or item.current_document_version != item.document_version
            or not item.chunk_id
            or not item.document_id
            or not item.content
            or not item.file_name
            or not score_is_finite
        ):
            if trace and len(records) < 100:
                reason = 'invalid_metadata'
                if isinstance(item, RetrievedChunk):
                    if not item.is_active:
                        reason = 'inactive'
                    elif item.current_document_version != item.document_version:
                        reason = 'stale_version'
                records.append(trace.metadata(item, rank, score=getattr(item, 'score', None), drop_reason=reason))
            continue
        valid.append((rank, item))

    result: list[RetrievedChunk] = []
    chunk_ids: set[str] = set()
    document_ids: set[str] = set()
    for rank, item in valid:
        if (
            item.chunk_id in chunk_ids
            or item.document_id in document_ids
        ):
            if trace and len(records) < 100:
                records.append(trace.metadata(item, rank, score=item.score, drop_reason='duplicate_chunk' if item.chunk_id in chunk_ids else 'duplicate_document'))
            continue
        chunk_ids.add(item.chunk_id)
        document_ids.add(item.document_id)
        result.append(item)
        if trace and len(records) < 100:
            records.append(trace.metadata(item, rank, score=item.score))
    if trace:
        trace.filter_records(sorted(records, key=lambda item: item['rank']))
    return result


def _validate_ranked(
    ranked: object,
    candidates: list[RetrievedChunk],
    limit: int,
) -> list[RerankedChunk]:
    if not isinstance(ranked, list) or len(ranked) > limit:
        raise ValueError
    allowed = {item.chunk_id: item for item in candidates}
    seen: set[str] = set()
    validated: list[RerankedChunk] = []
    for item in ranked:
        if (
            not isinstance(item, RerankedChunk)
            or item.chunk.chunk_id not in allowed
            or item.chunk.chunk_id in seen
            or not math.isfinite(float(item.score))
        ):
            raise ValueError
        seen.add(item.chunk.chunk_id)
        validated.append(item)
    return validated


def _bounded_contexts(
    ranked: list[RerankedChunk],
) -> tuple[list[CustomerServiceContext], dict[str, RetrievedChunk]]:
    contexts: list[CustomerServiceContext] = []
    chunks: dict[str, RetrievedChunk] = {}
    remaining = MAX_TOTAL_CONTEXT_CHARS
    for item in ranked[:MAX_CONTEXTS]:
        content = _clean_text(item.chunk.content, min(MAX_CONTEXT_CHARS, remaining))
        if not content:
            continue
        contexts.append(CustomerServiceContext(item.chunk.chunk_id, content))
        chunks[item.chunk.chunk_id] = item.chunk
        remaining -= len(content)
        if remaining <= 0:
            break
    return contexts, chunks


def _source(chunk: RetrievedChunk) -> CustomerServiceSource:
    return CustomerServiceSource(
        document_id=chunk.document_id,
        document_name=_clean_text(chunk.file_name, 255),
        document_version=chunk.document_version,
        chunk_id=chunk.chunk_id,
        locator=_clean_text(chunk.source_locator, MAX_LOCATOR_CHARS),
        excerpt=_clean_text(chunk.content, MAX_EXCERPT_CHARS),
    )


def _clean_text(value: str, limit: int) -> str:
    visible = "".join(
        character if not unicodedata.category(character).startswith("C") else " "
        for character in str(value)
    )
    return re.sub(r"\s+", " ", visible).strip()[:limit]


def _no_answer(*, degraded: bool) -> CustomerServiceAnswer:
    return CustomerServiceAnswer(False, NO_ANSWER_TEXT, (), degraded)
