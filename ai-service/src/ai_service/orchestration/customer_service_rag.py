from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import KnowledgeStore, RetrievedChunk
from ai_service.models.base import CustomerServiceContext, GenerationModel
from ai_service.models.embeddings import EmbeddingProvider
from ai_service.models.reranker import RerankedChunk, RerankerProvider
from ai_service.prompts.customer_service import fit_customer_service_contexts


MAX_QUESTION_CHARS = 4_000
MAX_CONTEXTS = 3
MAX_CONTEXT_CHARS = 1_200
MAX_TOTAL_CONTEXT_CHARS = 3_000
MAX_EXCERPT_CHARS = 400
MAX_LOCATOR_CHARS = 500
NO_ANSWER_TEXT = "暂未找到足够依据回答该问题。"
SYSTEM_FALLBACK_TEXT = "系统暂时无法生成可靠回答，请稍后重试。"


class CustomerServiceRagError(RuntimeError):
    """Stable RAG error that never carries provider details or user content."""

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
    """Single-turn retrieval and grounded-answer orchestration without persistence."""

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
        """Validate the local answer pipeline shape without provider calls."""

        return all((
            callable(getattr(self._embeddings, "embed_query", None)),
            callable(getattr(self._store, "search", None)),
            callable(getattr(self._reranker, "rerank", None)),
            callable(getattr(self._model, "answer_customer_service", None)),
        ))

    async def answer(self, question: str) -> CustomerServiceAnswer:
        normalized_question = question.strip() if isinstance(question, str) else ""
        if not normalized_question or len(normalized_question) > MAX_QUESTION_CHARS:
            raise CustomerServiceRagError("CUSTOMER_SERVICE_INVALID_REQUEST")

        try:
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
            raise CustomerServiceRagError(
                "CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE"
            ) from None
        try:
            retrieved = await self._store.search(
                [float(value) for value in vector], self._retrieval_top_k,
            )
        except Exception:
            raise CustomerServiceRagError(
                "CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE"
            ) from None

        candidates = _filter_and_dedupe(retrieved)
        if not candidates:
            return _no_answer(degraded=False)

        degraded = False
        try:
            ranked = await self._reranker.rerank(
                normalized_question, candidates, top_n=self._final_top_k,
            )
            ranked = _validate_ranked(ranked, candidates, self._final_top_k)
        except Exception:
            ranked = [
                RerankedChunk(item, float(item.score))
                for item in candidates[:self._final_top_k]
            ]
            # Disabled is an intentional operating mode, not a runtime degradation incident.
            degraded = not self._reranker_disabled

        if not ranked:
            return _no_answer(degraded=degraded)
        if self._min_rerank_score is not None:
            # A rerank threshold cannot be safely compared with Milvus scores on fallback.
            if degraded:
                return _no_answer(degraded=True)
            ranked = [item for item in ranked if item.score >= self._min_rerank_score]
            if not ranked:
                return _no_answer(degraded=False)

        contexts, chunks_by_id = _bounded_contexts(ranked)
        if not contexts:
            return _no_answer(degraded=degraded)
        fitted = fit_customer_service_contexts(
            normalized_question, contexts, max_bytes=self._prompt_max_bytes,
        )
        if fitted is None:
            return _no_answer(degraded=degraded)
        contexts = [
            CustomerServiceContext(chunk_id, content)
            for chunk_id, content in fitted if content
        ]
        chunks_by_id = {
            item.chunk_id: chunks_by_id[item.chunk_id] for item in contexts
        }
        if not contexts:
            return _no_answer(degraded=degraded)
        try:
            model_answer = await self._model.answer_customer_service(
                normalized_question, contexts,
            )
            citations = model_answer.cited_chunk_ids
            if model_answer.answered:
                if (
                    not model_answer.answer.strip()
                    or not citations
                    or len(citations) > MAX_CONTEXTS
                    or len(citations) != len(set(citations))
                    or any(chunk_id not in chunks_by_id for chunk_id in citations)
                ):
                    raise ValueError
                sources = tuple(_source(chunks_by_id[chunk_id]) for chunk_id in citations)
                return CustomerServiceAnswer(
                    answered=True,
                    answer=model_answer.answer[:4_000],
                    sources=sources,
                    degraded=degraded,
                )
            if model_answer.answer != "" or citations:
                raise ValueError
            return _no_answer(degraded=degraded)
        except Exception:
            return CustomerServiceAnswer(False, SYSTEM_FALLBACK_TEXT, (), degraded)


def _filter_and_dedupe(chunks: object) -> list[RetrievedChunk]:
    if not isinstance(chunks, Sequence) or isinstance(chunks, (str, bytes)):
        return []
    valid: list[RetrievedChunk] = []
    for item in chunks:
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
            continue
        valid.append(item)

    result: list[RetrievedChunk] = []
    chunk_ids: set[str] = set()
    document_ids: set[str] = set()
    for item in valid:
        if (
            item.chunk_id in chunk_ids
            or item.document_id in document_ids
        ):
            continue
        chunk_ids.add(item.chunk_id)
        document_ids.add(item.document_id)
        result.append(item)
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
