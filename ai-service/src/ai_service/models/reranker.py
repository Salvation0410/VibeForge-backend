from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import gc
import hashlib
from importlib.metadata import version
import inspect
import logging
import math
import multiprocessing
import os
from pathlib import Path
import stat
from typing import Any, Protocol
import uuid

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk


MAX_QUESTION_CHARS = 4_000
MAX_CHUNKS = 100
MAX_CHUNK_CHARS = 100_000
MAX_TOTAL_CONTENT_CHARS = 1_000_000
SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 30.0
WORKER_STARTUP_TIMEOUT_SECONDS = 300.0

logger = logging.getLogger(__name__)


class _GpuOwnerLock:
    def __init__(self, handle: Any, *, windows: bool):
        self._handle = handle
        self._windows = windows

    @classmethod
    def acquire(cls, model: str, device: str) -> _GpuOwnerLock:
        identity = hashlib.sha256(f"{model}\0{device}".encode()).hexdigest()
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p,
            ]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_bool
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            handle = kernel32.CreateMutexW(None, True, f"Local\\yu-ai-reranker-{identity}")
            if not handle:
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            if ctypes.get_last_error() == 183:
                kernel32.CloseHandle(handle)
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            return cls((kernel32, handle), windows=True)

        runtime = os.environ.get("XDG_RUNTIME_DIR")
        private = Path.home() / ".cache" / "yu-ai-code-mother" / "locks"
        directory: Path | None = None
        for candidate in ([Path(runtime)] if runtime else []) + [private]:
            try:
                candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
                candidate_stat = candidate.lstat()
                if (
                    stat.S_ISDIR(candidate_stat.st_mode)
                    and candidate_stat.st_uid == os.getuid()
                    and not candidate_stat.st_mode & 0o077
                ):
                    directory = candidate
                    break
            except OSError:
                continue
        if directory is None:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
        try:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(directory / f"reranker-{identity}.lock", flags, 0o600)
            file_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.getuid()
                or file_stat.st_mode & 0o077
            ):
                raise OSError
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return cls(descriptor, windows=False)
        except BaseException:
            if "descriptor" in locals():
                os.close(descriptor)
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        if self._windows:
            kernel32, mutex = handle
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)
            return
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


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

    def health_ready(self) -> bool:
        return True


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
    if version("FlagEmbedding") != "1.4.2":
        raise RuntimeError("unsupported FlagEmbedding runtime")
    from FlagEmbedding import FlagReranker
    from FlagEmbedding.utils import tokenizer_compat

    if not callable(getattr(FlagReranker, "compute_score", None)):
        raise RuntimeError("unsupported FlagEmbedding reranker surface")
    for name in ("pad_with_compat", "prepare_for_model_compat"):
        if not callable(getattr(tokenizer_compat, name, None)):
            raise RuntimeError("unsupported FlagEmbedding tokenizer surface")

    reranker = FlagReranker(model_name, **kwargs)
    for name in (
        "get_detailed_inputs", "batch_size", "tokenizer", "model",
        "target_devices", "max_length", "query_max_length", "use_fp16",
    ):
        if not hasattr(reranker, name):
            raise RuntimeError("unsupported FlagEmbedding instance surface")
    return _SafeFlagRerankerAdapter(reranker)


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


def _reranker_worker_entry(connection: Any, config: dict[str, Any]) -> None:
    """Spawn-safe worker entry; model construction and ownership stay in this process."""

    owner: _ModelOwner | None = None
    owner_lock: _GpuOwnerLock | None = None
    try:
        owner_lock = _GpuOwnerLock.acquire(config["model_name"], config["device"])
        model = _load_flag_reranker(
            config["model_name"], use_fp16=True,
            devices=[config["device"]], normalize=False,
        )
        owner = _ModelOwner(model)
        connection.send({"type": "ready"})
        while True:
            message = connection.recv()
            if not isinstance(message, dict):
                raise ValueError
            if message.get("type") == "shutdown":
                break
            request_id = message.get("request_id")
            pairs = message.get("pairs")
            batch_size = message.get("batch_size")
            if (
                message.get("type") != "score"
                or not isinstance(request_id, str)
                or not isinstance(pairs, list)
                or not isinstance(batch_size, int)
                or not 1 <= batch_size <= 128
            ):
                raise ValueError
            try:
                scores: list[Any] = []
                for start in range(0, len(pairs), batch_size):
                    raw = model.compute_score(pairs[start : start + batch_size])
                    values = raw.tolist() if hasattr(raw, "tolist") else raw
                    if isinstance(values, (int, float)):
                        values = [values]
                    scores.extend(values)
                connection.send({
                    "type": "result", "request_id": request_id, "scores": scores,
                })
            except BaseException:
                connection.send({
                    "type": "error", "request_id": request_id,
                    "code": "CUSTOMER_SERVICE_RERANKER_UNAVAILABLE",
                })
    except BaseException:
        try:
            connection.send({
                "type": "load_error", "code": "CUSTOMER_SERVICE_RERANKER_UNAVAILABLE",
            })
        except BaseException:
            pass
    finally:
        if owner is not None:
            owner.release()
        if owner_lock is not None:
            owner_lock.release()
        try:
            connection.close()
        except BaseException:
            pass


class _ProcessReranker:
    def __init__(
        self, connection: Any, process: Any, *, batch_size: int,
        timeout_seconds: float, shutdown_seconds: float = SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        self._connection = connection
        self._process = process
        self._batch_size = batch_size
        self._timeout = timeout_seconds
        self._shutdown_seconds = shutdown_seconds
        self._lock = asyncio.Lock()
        self._state = "open"
        self._drains: set[asyncio.Task[None]] = set()
        self._sends: set[asyncio.Task[None]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._connection_closed = False
        self._process_closed = False

    @classmethod
    async def start(
        cls, settings: Settings, *, worker_target: Callable[..., None] = _reranker_worker_entry,
        context: Any | None = None,
        shutdown_seconds: float = SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
    ) -> _ProcessReranker:
        ctx = context or multiprocessing.get_context("spawn")
        parent = child = process = None
        started = False
        try:
            parent, child = ctx.Pipe(duplex=True)
            process = ctx.Process(
                target=worker_target,
                args=(child, {
                    "model_name": settings.rag_reranker_model,
                    "device": settings.rag_reranker_device,
                }),
                name="customer-service-reranker", daemon=True,
            )
            process.start()
            started = True
            child.close()
            backend = cls(
                parent, process, batch_size=settings.rag_reranker_batch_size,
                timeout_seconds=settings.rag_reranker_timeout_seconds,
                shutdown_seconds=shutdown_seconds,
            )
            response = await backend._receive(WORKER_STARTUP_TIMEOUT_SECONDS)
            if response != {"type": "ready"}:
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            return backend
        except BaseException as error:
            for connection in (child, parent):
                try:
                    if connection is not None:
                        connection.close()
                except BaseException:
                    pass
            if started and process is not None and process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(1)
            try:
                if started and process is not None:
                    process.close()
            except BaseException:
                pass
            if isinstance(error, asyncio.CancelledError):
                raise
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None

    async def _receive(self, timeout: float | None = None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            try:
                if self._connection.poll(0):
                    value = self._connection.recv()
                    if not isinstance(value, dict):
                        raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
                    return value
            except (EOFError, OSError):
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None
            if not self._is_alive():
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            if deadline is not None and loop.time() >= deadline:
                raise TimeoutError
            await asyncio.sleep(0.005)

    def _close_connection(self) -> None:
        if self._connection_closed:
            return
        self._connection_closed = True
        try:
            self._connection.close()
        except BaseException:
            pass

    def _is_alive(self) -> bool:
        if self._process_closed:
            return False
        try:
            return self._process.is_alive()
        except (ValueError, AssertionError):
            return False

    def health_ready(self) -> bool:
        return (
            self._state == "open"
            and not self._connection_closed
            and self._is_alive()
        )

    def _finalize_process_handle(self) -> None:
        if self._process_closed or self._is_alive():
            return
        try:
            self._process.join(0)
            self._process.close()
        finally:
            self._process_closed = True

    async def _abort_process(self) -> None:
        if self._is_alive():
            self._process.terminate()
        self._close_connection()
        if not await self._wait_exit(1.0) and hasattr(self._process, "kill"):
            self._process.kill()
            await self._wait_exit(1.0)
        self._finalize_process_handle()

    async def _send(self, message: dict[str, Any], deadline: float) -> None:
        send = asyncio.create_task(asyncio.to_thread(self._connection.send, message))
        self._sends.add(send)
        send.add_done_callback(self._sends.discard)
        try:
            await asyncio.wait_for(
                asyncio.shield(send), max(0.0, deadline - asyncio.get_running_loop().time()),
            )
        except (TimeoutError, asyncio.CancelledError) as error:
            cleanup = asyncio.create_task(self._abort_process())
            try:
                await _drain_task(cleanup)
                await _drain_task(send)
            except BaseException:
                pass
            if isinstance(error, asyncio.CancelledError):
                raise
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
        except BaseException:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None

    async def _score(self, pairs: list[list[str]]) -> list[float]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        try:
            await asyncio.wait_for(
                self._lock.acquire(), max(0.0, deadline - loop.time()),
            )
        except TimeoutError:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
        owns_lock = True
        request_id = uuid.uuid4().hex
        try:
            await self._send({
                "type": "score", "request_id": request_id,
                "pairs": pairs, "batch_size": self._batch_size,
            }, deadline)
            receive = asyncio.create_task(self._receive())
            def transfer_to_drain() -> None:
                nonlocal owns_lock

                async def drain() -> None:
                    try:
                        await receive
                    except BaseException:
                        pass
                    finally:
                        self._lock.release()

                task = asyncio.create_task(drain())
                self._drains.add(task)
                task.add_done_callback(self._drains.discard)
                owns_lock = False

            try:
                response = await asyncio.wait_for(
                    asyncio.shield(receive), max(0.0, deadline - loop.time()),
                )
            except TimeoutError:
                transfer_to_drain()
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_TIMEOUT") from None
            except asyncio.CancelledError:
                transfer_to_drain()
                raise
            if response.get("request_id") != request_id:
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            if response.get("type") != "result":
                raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE")
            return LocalCrossEncoderReranker._normalize_scores(
                response.get("scores"), expected_count=len(pairs),
            )
        except RerankerError:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise RerankerError("CUSTOMER_SERVICE_RERANKER_UNAVAILABLE") from None
        finally:
            if owns_lock:
                self._lock.release()

    async def rerank(
        self, question: str, chunks: Sequence[RetrievedChunk], *, top_n: int,
    ) -> list[RerankedChunk]:
        validated = _validate_request(question, chunks, top_n)
        if not validated:
            return []
        scores = await self._score([[question, item.content] for item in validated])
        ranked = [
            (index, RerankedChunk(chunk=item, score=scores[index]))
            for index, item in enumerate(validated)
        ]
        ranked.sort(key=lambda value: (-value[1].score, value[0]))
        return [item for _, item in ranked[:top_n]]

    async def _wait_exit(self, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while self._is_alive() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        if not self._is_alive():
            self._finalize_process_handle()
            return True
        return False

    async def _close_once(self) -> None:
        self._state = "closing"
        try:
            try:
                await asyncio.wait_for(self._lock.acquire(), self._shutdown_seconds)
            except TimeoutError:
                await self._abort_process()
            else:
                try:
                    deadline = asyncio.get_running_loop().time() + self._shutdown_seconds
                    try:
                        await self._send({"type": "shutdown"}, deadline)
                    except RerankerError:
                        pass
                finally:
                    self._lock.release()
                if not await self._wait_exit(self._shutdown_seconds):
                    await self._abort_process()
        finally:
            if self._is_alive():
                await self._abort_process()
            self._close_connection()
            self._finalize_process_handle()
            self._state = "closed"

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            raise


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
        self._active_inferences: set[asyncio.Future[Any]] = set()
        self._inference_drains: set[asyncio.Task[None]] = set()
        self._late_cleanup: asyncio.Task[None] | None = None

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] = _load_flag_reranker,
    ) -> RerankerProvider:
        if model_factory is _load_flag_reranker:
            return await _ProcessReranker.start(settings)
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

    def _track_inference(
        self, future: asyncio.Future[Any], loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._active_inferences.add(future)

        def discard(completed: asyncio.Future[Any]) -> None:
            try:
                loop.call_soon_threadsafe(
                    self._active_inferences.discard, completed,
                )
            except RuntimeError:
                pass

        future.add_done_callback(discard)

    async def _await_active_inferences(self) -> None:
        while self._active_inferences:
            active = tuple(self._active_inferences)
            await asyncio.gather(
                *(asyncio.shield(future) for future in active),
                return_exceptions=True,
            )
            self._active_inferences.difference_update(
                future for future in active if future.done()
            )

    async def _await_inference_drains(self) -> None:
        while self._inference_drains:
            drains = tuple(self._inference_drains)
            await asyncio.gather(
                *(asyncio.shield(task) for task in drains),
                return_exceptions=True,
            )
            self._inference_drains.difference_update(
                task for task in drains if task.done()
            )

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
            self._track_inference(future, loop)
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

    def health_ready(self) -> bool:
        return not self._closed and self._owner.get() is not None

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
                    await self._await_active_inferences()
                    await self._await_inference_drains()
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
                    await self._await_inference_drains()
                    await self._await_active_inferences()
                    await asyncio.to_thread(self._owner.release)
                    await asyncio.to_thread(
                        self._executor.shutdown, wait=True, cancel_futures=True,
                    )

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
