from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import gc
import inspect
import logging
import math
from typing import Any, Protocol

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk


MAX_QUESTION_CHARS = 4_000
MAX_CHUNKS = 100
MAX_CHUNK_CHARS = 100_000
MAX_TOTAL_CONTENT_CHARS = 1_000_000
SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 30.0

logger = logging.getLogger(__name__)


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


class _SafeFlagRerankerAdapter:
    """Avoid FlagEmbedding 1.4.2's zero-batch infinite OOM retry loop."""

    def __init__(self, reranker: Any, *, score_batch: Callable[..., Any] | None = None):
        self._reranker = reranker
        self._score_batch = score_batch or _score_flag_batch

    def compute_score(self, sentence_pairs: list[list[str]]) -> list[float]:
        detailed = self._reranker.get_detailed_inputs(sentence_pairs)
        batch_size = min(max(1, int(self._reranker.batch_size)), len(detailed))
        output: list[float] = []
        offset = 0
        while offset < len(detailed):
            current = detailed[offset : offset + batch_size]
            try:
                raw = self._score_batch(
                    self._reranker, current, batch_size, normalize=False,
                )
            except BaseException as error:
                if not _is_cuda_oom(error) or batch_size == 1:
                    raise
                _empty_cuda_cache()
                batch_size = max(1, batch_size * 3 // 4)
                continue
            values = raw.tolist() if hasattr(raw, "tolist") else raw
            if isinstance(values, (int, float)):
                values = [values]
            output.extend(values)
            offset += len(current)
        return output

    def close(self) -> None:
        close = getattr(self._reranker, "close", None)
        if callable(close):
            close()
        else:
            stop = getattr(self._reranker, "stop_self_pool", None)
            if callable(stop):
                stop()


def _score_flag_batch(
    reranker: Any, sentence_pairs: list[list[str]], batch_size: int, **_: Any,
) -> list[float]:
    """Run one fixed batch using FlagReranker's supported tokenizer/model surface."""

    import torch
    from FlagEmbedding.utils.tokenizer_compat import (
        pad_with_compat,
        prepare_for_model_compat,
    )

    query_max_length = reranker.query_max_length or reranker.max_length * 3 // 4
    queries = [pair[0] for pair in sentence_pairs]
    passages = [pair[1] for pair in sentence_pairs]
    query_inputs = reranker.tokenizer(
        queries, return_tensors=None, add_special_tokens=False,
        max_length=query_max_length, truncation=True,
    )["input_ids"]
    passage_inputs = reranker.tokenizer(
        passages, return_tensors=None, add_special_tokens=False,
        max_length=reranker.max_length, truncation=True,
    )["input_ids"]
    prepared = [
        prepare_for_model_compat(
            reranker.tokenizer, query, passage, truncation="only_second",
            max_length=reranker.max_length, padding=False,
        )
        for query, passage in zip(query_inputs, passage_inputs, strict=True)
    ]
    device = reranker.target_devices[0]
    if reranker.use_fp16:
        reranker.model.half()
    reranker.model.to(device)
    reranker.model.eval()
    with torch.no_grad():
        inputs = pad_with_compat(
            reranker.tokenizer, prepared, padding=True, return_tensors="pt",
        ).to(device)
        scores = reranker.model(
            **inputs, return_dict=True,
        ).logits.view(-1).float().cpu().numpy().tolist()
    return scores


def _load_flag_reranker(model_name: str, **kwargs: Any) -> Any:
    # Keep importing FlagEmbedding out of disabled startup and module import paths.
    from FlagEmbedding import FlagReranker

    return _SafeFlagRerankerAdapter(FlagReranker(model_name, **kwargs))


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


class _ModelOwner:
    def __init__(self, model: Any):
        self.model: Any | None = model

    def get(self) -> Any | None:
        return self.model

    def release(self) -> None:
        model = self.model
        self.model = None
        try:
            close = getattr(model, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    asyncio.run(result)
        except BaseException:
            pass
        finally:
            del model
            gc.collect()
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
        self._owner = _ModelOwner(model)
        self._batch_size = batch_size
        self._timeout_seconds = float(timeout_seconds)
        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="customer-service-reranker",
        )
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._inference_drains: set[asyncio.Task[None]] = set()
        self._late_cleanup: asyncio.Task[None] | None = None

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
            normalize=False,
        ))
        try:
            model = await asyncio.shield(load_task)
        except asyncio.CancelledError:
            try:
                model = await _drain_task(load_task)
            except BaseException:
                pass
            else:
                owner = _ModelOwner(model)
                del model
                cleanup = asyncio.create_task(asyncio.to_thread(owner.release))
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
            owner = _ModelOwner(model)
            del model
            await asyncio.to_thread(owner.release)
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
        model = self._owner.get()
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

    def _track_drain(self, future: asyncio.Future[Any]) -> None:
        async def drain() -> None:
            try:
                await _drain_task(future)
            except BaseException:
                pass
            finally:
                self._semaphore.release()

        task = asyncio.create_task(drain())
        self._inference_drains.add(task)
        task.add_done_callback(self._inference_drains.discard)

    async def rerank(
        self, question: str, chunks: Sequence[RetrievedChunk], *, top_n: int,
    ) -> list[RerankedChunk]:
        validated = _validate_request(question, chunks, top_n)
        if not validated:
            return []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        owns_permit = True
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
                self._track_drain(future)
                owns_permit = False
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
            except asyncio.CancelledError:
                self._track_drain(future)
                owns_permit = False
                raise
            except BaseException as error:
                raise self._provider_error(error) from None
        finally:
            if owns_permit:
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
                async with asyncio.timeout(SHUTDOWN_DRAIN_TIMEOUT_SECONDS):
                    for _ in range(self._max_concurrency):
                        await self._semaphore.acquire()
                        acquired += 1
                    await asyncio.get_running_loop().run_in_executor(
                        self._executor, self._owner.release,
                    )
                    await asyncio.to_thread(
                        self._executor.shutdown, wait=True, cancel_futures=False,
                    )
            except TimeoutError:
                logger.error(
                    "Reranker shutdown timed out while inference remained blocked"
                )
                self._executor.shutdown(wait=False, cancel_futures=True)
                async def cleanup_when_inference_finishes() -> None:
                    if self._inference_drains:
                        await asyncio.gather(
                            *tuple(self._inference_drains), return_exceptions=True,
                        )
                    await asyncio.to_thread(self._owner.release)

                self._late_cleanup = asyncio.create_task(
                    cleanup_when_inference_finishes()
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
