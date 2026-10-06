from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from langchain.embeddings import init_embeddings
import httpx

from ai_service.config import Settings


class EmbeddingOutputError(RuntimeError):
    """Stable embedding error that never exposes provider details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class EmbeddingProvider(Protocol):
    async def embed_documents(
        self, texts: list[str], *, max_elements: int | None = None,
    ) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class CloseAIEmbeddingProvider:
    """Process-scoped OpenAI-compatible embeddings client for CloseAI."""

    @staticmethod
    def _close_async_client_sync(client: httpx.AsyncClient) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(client.aclose())
            return
        errors: list[BaseException] = []

        def close() -> None:
            try:
                asyncio.run(client.aclose())
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=close)
        thread.start()
        thread.join()
        if errors:
            raise errors[0]

    def __init__(
        self,
        settings: Settings,
        *,
        embedding_factory: Callable[..., Any] = init_embeddings,
        http_client_factory: Callable[..., httpx.Client] = httpx.Client,
        http_async_client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ) -> None:
        self._batch_size = settings.rag_embedding_batch_size
        self._dimension = settings.rag_embedding_dimension
        self._http_client: httpx.Client | None = None
        self._http_async_client: httpx.AsyncClient | None = None
        try:
            self._http_client = http_client_factory(trust_env=False)
            self._http_async_client = http_async_client_factory(trust_env=False)
            self._client = embedding_factory(
                settings.rag_embedding_model,
                api_key=settings.closeai_api_key,
                base_url=settings.closeai_base_url,
                http_client=self._http_client,
                http_async_client=self._http_async_client,
            )
        except Exception:
            if self._http_client is not None:
                try:
                    self._http_client.close()
                except BaseException:
                    pass
            if self._http_async_client is not None:
                try:
                    self._close_async_client_sync(self._http_async_client)
                except BaseException:
                    pass
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

    async def embed_documents(
        self, texts: list[str], *, max_elements: int | None = None,
    ) -> list[list[float]]:
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_INPUT")
        if (
            max_elements is not None
            and (not isinstance(max_elements, int) or max_elements < 1)
        ):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_INPUT")
        if not texts:
            return []
        if (
            max_elements is not None
            and len(texts) * self._dimension > max_elements
        ):
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED")
        output: list[list[float]] = []
        dimension: int | None = None
        total_elements = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            try:
                raw = await self._client.aembed_documents(batch)
            except EmbeddingOutputError:
                raise
            except Exception:
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None
            vectors = self.validate(raw, expected_count=len(batch))
            if any(len(vector) != self._dimension for vector in vectors):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
            if dimension is None:
                dimension = len(vectors[0])
                if (
                    max_elements is not None
                    and len(texts) * dimension > max_elements
                ):
                    raise EmbeddingOutputError(
                        "KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED"
                    )
            elif any(len(vector) != dimension for vector in vectors):
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
            total_elements += sum(len(vector) for vector in vectors)
            if max_elements is not None and total_elements > max_elements:
                raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED")
            output.extend(vectors)
        return output

    def health_ready(self) -> bool:
        """Check owned client lifecycle without calling the embedding provider."""

        return bool(
            self._http_client is not None
            and self._http_async_client is not None
            and not self._http_client.is_closed
            and not self._http_async_client.is_closed
            and getattr(self, "_client", None) is not None
        )

    async def embed_query(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_INVALID_INPUT")
        try:
            raw = await self._client.aembed_query(text)
        except EmbeddingOutputError:
            raise
        except Exception:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None
        vector = self.validate([raw], expected_count=1)[0]
        if len(vector) != self._dimension:
            raise EmbeddingOutputError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        return vector

    async def close(self) -> None:
        """Close the HTTP clients explicitly owned by this provider."""

        first_error: BaseException | None = None
        if self._http_async_client is not None:
            try:
                await self._http_async_client.aclose()
            except BaseException as error:
                first_error = error
        if self._http_client is not None:
            try:
                await asyncio.to_thread(self._http_client.close)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
