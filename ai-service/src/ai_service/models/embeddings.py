from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from langchain.embeddings import init_embeddings

from ai_service.config import Settings


class EmbeddingOutputError(RuntimeError):
    """Stable embedding error that never exposes provider details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class CloseAIEmbeddingProvider:
    """Process-scoped OpenAI-compatible embeddings client for CloseAI."""

    def __init__(
        self,
        settings: Settings,
        *,
        embedding_factory: Callable[..., Any] = init_embeddings,
    ) -> None:
        self._batch_size = settings.rag_embedding_batch_size
        try:
            self._client = embedding_factory(
                settings.rag_embedding_model,
                api_key=settings.closeai_api_key,
                base_url=settings.closeai_base_url,
            )
        except Exception:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None

    @staticmethod
    def validate(vectors: object, *, expected_count: int) -> list[list[float]]:
        if not isinstance(vectors, Sequence) or isinstance(vectors, (str, bytes)):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_OUTPUT")
        if len(vectors) != expected_count:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_COUNT_MISMATCH")
        validated: list[list[float]] = []
        dimension: int | None = None
        for vector in vectors:
            if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_OUTPUT")
            if not vector:
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR")
            try:
                normalized = [float(value) for value in vector]
            except (TypeError, ValueError, OverflowError):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR") from None
            if not all(math.isfinite(value) for value in normalized):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR")
            if dimension is None:
                dimension = len(normalized)
            elif len(normalized) != dimension:
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
            validated.append(normalized)
        return validated

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_INPUT")
        if not texts:
            return []
        output: list[list[float]] = []
        dimension: int | None = None
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            try:
                raw = await self._client.aembed_documents(batch)
            except EmbeddingOutputError:
                raise
            except Exception:
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None
            vectors = self.validate(raw, expected_count=len(batch))
            if dimension is None:
                dimension = len(vectors[0])
            elif any(len(vector) != dimension for vector in vectors):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
            output.extend(vectors)
        return output

    async def embed_query(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_INPUT")
        try:
            raw = await self._client.aembed_query(text)
        except EmbeddingOutputError:
            raise
        except Exception:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None
        return self.validate([raw], expected_count=1)[0]

    async def close(self) -> None:
        """Close OpenAI-compatible sync and async HTTP clients when exposed."""

        seen: set[int] = set()
        for name in ("async_client", "client"):
            resource = getattr(self._client, name, None)
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
            if close is None:
                continue
            if inspect.iscoroutinefunction(close):
                await close()
            else:
                result = await asyncio.to_thread(close)
                if inspect.isawaitable(result):
                    await result
