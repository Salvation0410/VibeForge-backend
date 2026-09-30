from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import math
from typing import Any, Protocol

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk


MAX_QUESTION_CHARS = 4_000
MAX_CHUNKS = 100
MAX_CHUNK_CHARS = 100_000
MAX_TOTAL_CONTENT_CHARS = 1_000_000


class RerankerError(RuntimeError):
    """Stable reranker error that does not expose provider or device details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RerankedChunk:
    chunk: RetrievedChunk
    score: float


class RerankerProvider(Protocol):
    async def rerank(
        self, question: str, chunks: Sequence[RetrievedChunk], *, top_n: int,
    ) -> list[RerankedChunk]: ...

    async def close(self) -> None: ...


def _validate_request(
    question: str, chunks: Sequence[RetrievedChunk], top_n: int,
) -> list[RetrievedChunk]:
    if (
        not isinstance(question, str)
        or not question.strip()
        or len(question) > MAX_QUESTION_CHARS
        or isinstance(chunks, (str, bytes))
        or not isinstance(chunks, Sequence)
        or len(chunks) > MAX_CHUNKS
        or not isinstance(top_n, int)
        or isinstance(top_n, bool)
        or not 1 <= top_n <= MAX_CHUNKS
    ):
        raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_INPUT")
    validated = list(chunks)
    total_chars = 0
    for item in validated:
        if not isinstance(item, RetrievedChunk) or not isinstance(item.content, str):
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_INPUT")
        if not item.content or len(item.content) > MAX_CHUNK_CHARS:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_INPUT")
        total_chars += len(item.content)
        if total_chars > MAX_TOTAL_CONTENT_CHARS:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_INPUT")
    return validated


class DisabledReranker:
    """No-op provider that preserves retrieval order and source scores."""

    async def rerank(
        self, question: str, chunks: Sequence[RetrievedChunk], *, top_n: int,
    ) -> list[RerankedChunk]:
        validated = _validate_request(question, chunks, top_n)
        return [
            RerankedChunk(chunk=item, score=float(item.score))
            for item in validated[:top_n]
        ]

    async def close(self) -> None:
        return None


def _load_flag_reranker(model_name: str, **kwargs: Any) -> Any:
    # Keep importing FlagEmbedding out of disabled startup and module import paths.
    from FlagEmbedding import FlagReranker

    return FlagReranker(model_name, **kwargs)


def _is_cuda_oom(error: BaseException) -> bool:
    error_type = type(error)
    if error_type.__name__ == "OutOfMemoryError" and error_type.__module__.startswith("torch"):
        return True
    message = str(error).lower()
    return "cuda out of memory" in message or "cuda error: out of memory" in message


def _empty_cuda_cache() -> None:
    try:
        import torch

        cuda = getattr(torch, "cuda", None)
        if cuda is not None and callable(getattr(cuda, "empty_cache", None)):
            cuda.empty_cache()
    except BaseException:
        pass


def _release_model(model: Any) -> None:
    try:
        close = getattr(model, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                asyncio.run(result)
    except BaseException:
        pass
    finally:
        _empty_cuda_cache()


async def _drain_task(task: asyncio.Future[Any] | asyncio.Task[Any]) -> Any:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    return task.result()


class LocalCrossEncoderReranker:
    """Process-scoped bounded adapter for blocking local cross-encoder inference."""

    def __init__(
        self,
        *,
        model: Any,
        batch_size: int = 4,
        timeout_seconds: float = 5.0,
        workers: int = 1,
        max_concurrency: int = 1,
    ) -> None:
        if (
            not isinstance(batch_size, int) or not 1 <= batch_size <= 128
            or not isinstance(workers, int) or not 1 <= workers <= 8
            or not isinstance(max_concurrency, int) or not 1 <= max_concurrency <= 8
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_CONFIG")
        self._model: Any | None = model
        self._batch_size = batch_size
        self._timeout_seconds = float(timeout_seconds)
        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="customer-service-reranker",
        )
        self._closed = False
        self._close_lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] = _load_flag_reranker,
    ) -> LocalCrossEncoderReranker:
        load_task = asyncio.create_task(asyncio.to_thread(
            model_factory,
            settings.rag_reranker_model,
            use_fp16=True,
            devices=[settings.rag_reranker_device],
        ))
        try:
            model = await asyncio.shield(load_task)
        except asyncio.CancelledError:
            try:
                model = await _drain_task(load_task)
            except BaseException:
                pass
            else:
                cleanup = asyncio.create_task(asyncio.to_thread(_release_model, model))
                try:
                    await _drain_task(cleanup)
                except BaseException:
                    pass
            raise
        except Exception:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None
        try:
            return cls(
                model=model,
                batch_size=settings.rag_reranker_batch_size,
                timeout_seconds=settings.rag_reranker_timeout_seconds,
                workers=settings.rag_reranker_workers,
                max_concurrency=settings.rag_reranker_max_concurrency,
            )
        except BaseException:
            await asyncio.to_thread(_release_model, model)
            raise

    @staticmethod
    def _normalize_scores(raw: Any, *, expected_count: int) -> list[float]:
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values = list(raw)
        else:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_OUTPUT")
        if len(values) != expected_count:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_COUNT_MISMATCH")
        normalized: list[float] = []
        for value in values:
            try:
                logit = float(value)
            except (TypeError, ValueError, OverflowError):
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_OUTPUT") from None
            if not math.isfinite(logit):
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_INVALID_OUTPUT")
            if logit >= 0:
                score = 1.0 / (1.0 + math.exp(-logit))
            else:
                exp_score = math.exp(logit)
                score = exp_score / (1.0 + exp_score)
            normalized.append(score)
        return normalized

    def _score_batches(self, question: str, chunks: list[RetrievedChunk]) -> list[float]:
        model = self._model
        if model is None:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
        scores: list[float] = []
        try:
            for start in range(0, len(chunks), self._batch_size):
                batch = chunks[start : start + self._batch_size]
                pairs = [[question, item.content] for item in batch]
                raw = model.compute_score(pairs)
                scores.extend(self._normalize_scores(raw, expected_count=len(batch)))
            return scores
        except RerankerError:
            raise
        except BaseException as error:
            if _is_cuda_oom(error):
                _empty_cuda_cache()
            raise

    @staticmethod
    def _provider_error(error: BaseException) -> RerankerError:
        if isinstance(error, RerankerError):
            return error
        return RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")

    async def rerank(
        self, question: str, chunks: Sequence[RetrievedChunk], *, top_n: int,
    ) -> list[RerankedChunk]:
        validated = _validate_request(question, chunks, top_n)
        if not validated:
            return []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=max(0.0, deadline - loop.time()),
            )
        except TimeoutError:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
        try:
            if self._closed:
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            future = loop.run_in_executor(
                self._executor, self._score_batches, question, validated,
            )
            try:
                scores = await asyncio.wait_for(
                    asyncio.shield(future), timeout=max(0.0, deadline - loop.time()),
                )
            except TimeoutError:
                try:
                    await _drain_task(future)
                except BaseException:
                    pass
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
            except asyncio.CancelledError:
                try:
                    await _drain_task(future)
                except BaseException:
                    pass
                raise
            except BaseException as error:
                raise self._provider_error(error) from None
        finally:
            self._semaphore.release()
        ranked = [
            (index, RerankedChunk(chunk=item, score=scores[index]))
            for index, item in enumerate(validated)
        ]
        ranked.sort(key=lambda value: (-value[1].score, value[0]))
        return [item for _, item in ranked[:top_n]]

    async def _close_once(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            acquired = 0
            try:
                for _ in range(self._max_concurrency):
                    await self._semaphore.acquire()
                    acquired += 1
                model = self._model
                self._model = None
                if model is not None:
                    await asyncio.get_running_loop().run_in_executor(
                        self._executor, _release_model, model,
                    )
                await asyncio.to_thread(
                    self._executor.shutdown, wait=True, cancel_futures=False,
                )
            finally:
                for _ in range(acquired):
                    self._semaphore.release()

    async def close(self) -> None:
        close_task = asyncio.create_task(self._close_once())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            try:
                await _drain_task(close_task)
            except BaseException:
                pass
            raise


__all__ = [
    "DisabledReranker",
    "LocalCrossEncoderReranker",
    "RerankedChunk",
    "RerankerError",
    "RerankerProvider",
]
