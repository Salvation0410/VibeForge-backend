from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Any
import weakref

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, RetrievedChunk
from ai_service.models.reranker import (
    DisabledReranker,
    LocalCrossEncoderReranker,
    RerankerError,
    _SafeFlagRerankerAdapter,
)


def chunk(index: int, *, score: float = 0.5, content: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{index}",
        document_id="doc-1",
        document_version=1,
        chunk_index=index,
        content=content or f"content-{index}",
        file_name="guide.md",
        file_type="md",
        source_locator="oss://private/guide.md",
        content_hash=f"hash-{index}",
        distance=1.0 - score,
        score=score,
    )


class FakeCrossEncoder:
    def __init__(self, scores: list[float] | float):
        self.scores = scores
        self.calls: list[list[list[str]]] = []
        self.closed = False

    def compute_score(self, pairs: list[list[str]]) -> Any:
        self.calls.append(pairs)
        if isinstance(self.scores, list):
            start = sum(len(call) for call in self.calls[:-1])
            values = self.scores[start : start + len(pairs)]
            return values[0] if len(values) == 1 else values
        return self.scores

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_disabled_reranker_preserves_order_and_source_scores():
    chunks = [chunk(0, score=0.8), chunk(1, score=0.4)]

    result = await DisabledReranker().rerank("deploy app", chunks, top_n=1)

    assert result[0].chunk is chunks[0]
    assert result[0].score == 0.8


@pytest.mark.asyncio
async def test_reranker_batches_pairs_normalizes_scores_and_returns_top_n():
    model = FakeCrossEncoder([0.0, 2.0, -2.0])
    chunks = [chunk(index) for index in range(3)]
    reranker = LocalCrossEncoderReranker(
        model=model, batch_size=2, timeout_seconds=1,
    )

    result = await reranker.rerank("deploy app", chunks, top_n=2)

    assert model.calls == [
        [["deploy app", "content-0"], ["deploy app", "content-1"]],
        [["deploy app", "content-2"]],
    ]
    assert [item.chunk.chunk_id for item in result] == ["chunk-1", "chunk-0"]
    assert result[0].score == pytest.approx(1 / (1 + math.exp(-2)))
    assert result[1].score == pytest.approx(0.5)
    assert chunks[1].score == 0.5
    await reranker.close()


@pytest.mark.asyncio
async def test_reranker_keeps_original_order_for_equal_scores_and_scalar_batch():
    model = FakeCrossEncoder(0.0)
    reranker = LocalCrossEncoderReranker(
        model=model, batch_size=1, timeout_seconds=1,
    )

    result = await reranker.rerank(
        "question", [chunk(0), chunk(1), chunk(2)], top_n=2,
    )

    assert [item.chunk.chunk_id for item in result] == ["chunk-0", "chunk-1"]
    await reranker.close()


@pytest.mark.asyncio
async def test_reranker_accepts_numpy_scalar_and_one_dimensional_array():
    class NumpyModel:
        calls = 0

        def compute_score(self, _pairs):
            self.calls += 1
            return np.float32(0.0) if self.calls == 1 else np.array([1.0, -1.0])

    reranker = LocalCrossEncoderReranker(
        model=NumpyModel(), batch_size=1, timeout_seconds=1,
    )
    first = await reranker.rerank("question", [chunk(0)], top_n=1)
    reranker._batch_size = 2
    second = await reranker.rerank("question", [chunk(0), chunk(1)], top_n=2)

    assert first[0].score == pytest.approx(0.5)
    assert [item.chunk.chunk_id for item in second] == ["chunk-0", "chunk-1"]
    await reranker.close()


@pytest.mark.parametrize(
    ("scores", "code"),
    [
        ([float("nan"), float("nan")], "CUSTOMER_SERVICE_RERANKER_INVALID_OUTPUT"),
        ([0.1], "CUSTOMER_SERVICE_RERANKER_COUNT_MISMATCH"),
    ],
)
@pytest.mark.asyncio
async def test_reranker_rejects_invalid_provider_output(scores, code):
    reranker = LocalCrossEncoderReranker(
        model=FakeCrossEncoder(scores), batch_size=2, timeout_seconds=1,
    )

    with pytest.raises(RerankerError) as captured:
        await reranker.rerank("question", [chunk(0), chunk(1)], top_n=1)

    assert captured.value.code == code
    assert str(captured.value) == code
    await reranker.close()


@pytest.mark.asyncio
async def test_timeout_returns_promptly_but_holds_permit_until_gpu_work_finishes():
    first_started = threading.Event()
    release_first = threading.Event()
    active = 0
    max_active = 0
    lock = threading.Lock()

    class BlockingModel:
        calls = 0

        def compute_score(self, pairs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            self.calls += 1
            try:
                if self.calls == 1:
                    first_started.set()
                    release_first.wait(1)
                return [0.0] * len(pairs)
            finally:
                with lock:
                    active -= 1

    model = BlockingModel()
    reranker = LocalCrossEncoderReranker(
        model=model, batch_size=2, timeout_seconds=0.03,
        max_concurrency=1, workers=1,
    )
    first = asyncio.create_task(reranker.rerank("q", [chunk(0)], top_n=1))
    assert await asyncio.to_thread(first_started.wait, 0.3)
    with pytest.raises(RerankerError) as first_error:
        await asyncio.wait_for(first, 0.15)
    assert first_error.value.code == "CUSTOMER_SERVICE_RERANKER_TIMEOUT"
    second = asyncio.create_task(reranker.rerank("q", [chunk(1)], top_n=1))
    with pytest.raises(RerankerError) as second_error:
        await asyncio.wait_for(second, 0.15)
    assert second_error.value.code == "CUSTOMER_SERVICE_RERANKER_TIMEOUT"
    assert active == 1
    assert max_active == 1
    assert model.calls == 1
    release_first.set()
    for _ in range(50):
        if reranker._semaphore._value == 1:
            break
        await asyncio.sleep(0.01)
    recovered = await reranker.rerank("q", [chunk(2)], top_n=1)
    assert recovered[0].chunk.chunk_id == "chunk-2"
    assert model.calls == 2
    await reranker.close()


@pytest.mark.asyncio
async def test_close_waits_for_tracked_inference_drain():
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        closed = False

        def compute_score(self, pairs):
            started.set()
            release.wait(1)
            return [0.0] * len(pairs)

        def close(self):
            self.closed = True

    model = BlockingModel()
    reranker = LocalCrossEncoderReranker(
        model=model, timeout_seconds=0.02, max_concurrency=1, workers=1,
    )
    with pytest.raises(RerankerError, match="CUSTOMER_SERVICE_RERANKER_TIMEOUT"):
        await reranker.rerank("q", [chunk(0)], top_n=1)
    closing = asyncio.create_task(reranker.close())
    await asyncio.sleep(0.02)
    assert not closing.done()
    assert not model.closed
    release.set()
    await asyncio.wait_for(closing, 0.5)
    assert model.closed


@pytest.mark.asyncio
async def test_close_waits_for_normally_active_submitted_inference():
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        closed = False

        def compute_score(self, pairs):
            started.set()
            release.wait(1)
            return [0.0] * len(pairs)

        def close(self):
            self.closed = True

    model = BlockingModel()
    reranker = LocalCrossEncoderReranker(model=model, timeout_seconds=1)
    inference = asyncio.create_task(reranker.rerank("q", [chunk(0)], top_n=1))
    assert await asyncio.to_thread(started.wait, 0.3)
    closing = asyncio.create_task(reranker.close())
    await asyncio.sleep(0.02)
    assert not closing.done()
    assert not model.closed
    release.set()
    await asyncio.wait_for(inference, 0.5)
    await asyncio.wait_for(closing, 0.5)
    assert model.closed


@pytest.mark.asyncio
async def test_close_timeout_is_bounded_and_later_releases_model(
    monkeypatch, caplog,
):
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        closed = False

        def compute_score(self, pairs):
            started.set()
            release.wait(1)
            return [0.0] * len(pairs)

        def close(self):
            self.closed = True

    model = BlockingModel()
    reranker = LocalCrossEncoderReranker(model=model, timeout_seconds=0.01)
    monkeypatch.setattr(
        "ai_service.models.reranker.SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.02,
    )
    with pytest.raises(RerankerError, match="CUSTOMER_SERVICE_RERANKER_TIMEOUT"):
        await reranker.rerank("q", [chunk(0)], top_n=1)

    await asyncio.wait_for(reranker.close(), 0.2)

    assert "shutdown timed out" in caplog.text
    assert not model.closed
    release.set()
    assert reranker._late_cleanup is not None
    await asyncio.wait_for(reranker._late_cleanup, 0.5)
    assert model.closed


@pytest.mark.asyncio
async def test_shutdown_timeout_does_not_release_normally_active_model(
    monkeypatch, caplog,
):
    started = threading.Event()
    release = threading.Event()

    class BlockingModel:
        closed = False

        def compute_score(self, pairs):
            started.set()
            release.wait(1)
            assert not self.closed
            return [0.0] * len(pairs)

        def close(self):
            self.closed = True

    model = BlockingModel()
    reranker = LocalCrossEncoderReranker(model=model, timeout_seconds=1)
    monkeypatch.setattr(
        "ai_service.models.reranker.SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.02,
    )
    inference = asyncio.create_task(reranker.rerank("q", [chunk(0)], top_n=1))
    assert await asyncio.to_thread(started.wait, 0.3)

    await asyncio.wait_for(reranker.close(), 0.2)

    assert "shutdown timed out" in caplog.text
    assert not model.closed
    assert not inference.done()
    release.set()
    await asyncio.wait_for(inference, 0.5)
    assert reranker._late_cleanup is not None
    await asyncio.wait_for(reranker._late_cleanup, 0.5)
    assert model.closed
    assert not reranker._active_inferences


def test_safe_flag_adapter_stops_at_batch_one_oom_without_looping():
    class Model:
        def to(self, _device):
            return self

        def eval(self):
            return None

        def __call__(self, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    class FlagShape:
        batch_size = 4
        query_max_length = None
        max_length = 512
        normalize = False
        use_fp16 = False
        target_devices = ["cuda"]
        model = Model()
        tokenizer = object()

        def get_detailed_inputs(self, pairs):
            return pairs

    attempts = []

    def score_batch(_flag, pairs, batch_size, **_kwargs):
        attempts.append((len(pairs), batch_size))
        raise RuntimeError("CUDA out of memory")

    adapter = _SafeFlagRerankerAdapter(FlagShape(), score_batch=score_batch)
    with pytest.raises(RuntimeError, match="out of memory"):
        adapter.compute_score([["q", "a"], ["q", "b"]])
    assert attempts == [(2, 2), (1, 1)]


def test_safe_flag_adapter_retries_smaller_batches_and_preserves_count():
    class FlagShape:
        batch_size = 4
        normalize = False

        def get_detailed_inputs(self, pairs):
            return pairs

    attempts = []

    def score_batch(_flag, pairs, batch_size, **_kwargs):
        attempts.append((len(pairs), batch_size))
        if batch_size > 1:
            raise RuntimeError("CUDA out of memory")
        return [float(index) for index, _ in enumerate(pairs)]

    adapter = _SafeFlagRerankerAdapter(FlagShape(), score_batch=score_batch)
    output = adapter.compute_score([["q", "a"], ["q", "b"]])
    assert output == [0.0, 0.0]
    assert attempts == [(2, 2), (1, 1), (1, 1)]


@pytest.mark.asyncio
async def test_oom_and_vendor_errors_are_mapped_without_details():
    class FailingModel:
        def __init__(self, error):
            self.error = error

        def compute_score(self, _pairs):
            raise self.error

    for error in (
        RuntimeError("CUDA out of memory. device 0 allocation details"),
        RuntimeError("vendor secret endpoint and stack details"),
    ):
        reranker = LocalCrossEncoderReranker(
            model=FailingModel(error), batch_size=2, timeout_seconds=1,
        )
        with pytest.raises(RerankerError) as captured:
            await reranker.rerank("question", [chunk(0)], top_n=1)
        assert captured.value.code == "CUSTOMER_SERVICE_RERANKER_UNAVAILABLE"
        assert "CUDA" not in str(captured.value)
        assert "vendor" not in str(captured.value)
        await reranker.close()


@pytest.mark.asyncio
async def test_async_factory_loads_once_with_cuda_and_closes_model():
    calls = []
    model = FakeCrossEncoder(0.0)

    def factory(model_name, **kwargs):
        calls.append((model_name, kwargs))
        return model

    settings = rag_settings()
    reranker = await LocalCrossEncoderReranker.create(
        settings, model_factory=factory,
    )
    await reranker.rerank("question", [chunk(0)], top_n=1)
    await reranker.rerank("question", [chunk(0)], top_n=1)

    assert calls == [(
        "BAAI/bge-reranker-v2-m3",
        {"use_fp16": True, "devices": ["cuda"], "normalize": False},
    )]
    await reranker.close()
    assert model.closed


@pytest.mark.asyncio
async def test_factory_cancellation_drains_load_and_releases_model():
    started = threading.Event()
    release = threading.Event()
    model = FakeCrossEncoder([0.0])

    def factory(*_args, **_kwargs):
        started.set()
        release.wait(1)
        return model

    loading = asyncio.create_task(LocalCrossEncoderReranker.create(
        rag_settings(), model_factory=factory,
    ))
    assert await asyncio.to_thread(started.wait, 0.3)
    loading.cancel()
    await asyncio.sleep(0.02)
    assert not loading.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await loading
    assert model.closed


@pytest.mark.asyncio
async def test_close_drops_last_model_reference_before_emptying_cuda_cache(monkeypatch):
    events = []

    class Model:
        pass

    model = Model()
    reference = weakref.ref(model, lambda _ref: events.append("finalized"))
    reranker = LocalCrossEncoderReranker(model=model, timeout_seconds=1)
    del model
    monkeypatch.setattr(
        "ai_service.models.reranker._empty_cuda_cache",
        lambda: events.append(("cache", reference() is None)),
    )

    await reranker.close()

    assert events == ["finalized", ("cache", True)]


def rag_settings(**overrides) -> Settings:
    values = {
        "internal_bearer_token": "internal-token",
        "spring_gateway_base_url": "http://spring.test",
        "spring_gateway_bearer_token": "gateway-token",
        "checkpoint_enabled": False,
        "customer_service_rag_enabled": True,
        "closeai_api_key": "closeai-key",
        "closeai_base_url": "https://closeai.test/v1",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"rag_reranker_workers": 0}, "rag_reranker_workers"),
        ({"rag_reranker_max_concurrency": 0}, "rag_reranker_max_concurrency"),
    ],
)
def test_reranker_rejects_invalid_executor_bounds(overrides, field):
    with pytest.raises(ValidationError, match=field):
        rag_settings(**overrides)


def test_reranker_executor_defaults_are_serial_and_bounded():
    settings = rag_settings()

    assert settings.rag_reranker_workers == 1
    assert settings.rag_reranker_max_concurrency == 1


def test_app_loads_local_reranker_once_and_closes_it(app_factory, settings):
    settings.customer_service_rag_enabled = True
    settings.closeai_api_key = "closeai-key"
    settings.closeai_base_url = "https://closeai.test/v1"
    model = FakeCrossEncoder([0.0])
    calls = []

    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return model

    app = app_factory(
        knowledge_etl_service=object(), mutation_coordinator=object(),
        reranker=None, reranker_model_factory=factory,
    )
    with TestClient(app):
        assert app.state.reranker is not None
    assert len(calls) == 1
    assert model.closed


@pytest.mark.asyncio
async def test_app_startup_cancellation_drains_reranker_load_and_closes_model(
    app_factory, settings,
):
    settings.customer_service_rag_enabled = True
    settings.closeai_api_key = "closeai-key"
    settings.closeai_base_url = "https://closeai.test/v1"
    started = threading.Event()
    release = threading.Event()
    model = FakeCrossEncoder([0.0])

    def factory(*_args, **_kwargs):
        started.set()
        release.wait(1)
        return model

    app = app_factory(
        knowledge_etl_service=object(), mutation_coordinator=object(),
        reranker=None, reranker_model_factory=factory,
    )
    context = app.router.lifespan_context(app)
    entering = asyncio.create_task(context.__aenter__())
    assert await asyncio.to_thread(started.wait, 0.3)
    entering.cancel()
    await asyncio.sleep(0.02)
    assert not entering.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await entering
    assert model.closed


@pytest.mark.asyncio
async def test_partial_startup_failure_closes_loaded_reranker(app_factory, settings):
    settings.customer_service_rag_enabled = True
    settings.closeai_api_key = "closeai-key"
    settings.closeai_base_url = "https://closeai.test/v1"
    model = FakeCrossEncoder([0.0])

    def failing_milvus(**_kwargs):
        raise RuntimeError("vendor connection details")

    app = app_factory(
        knowledge_downloader=object(), embedding_provider=object(),
        mutation_coordinator=object(), milvus_client_factory=failing_milvus,
        reranker=None, reranker_model_factory=lambda *_args, **_kwargs: model,
    )
    context = app.router.lifespan_context(app)
    with pytest.raises(MilvusKnowledgeError) as captured:
        await context.__aenter__()
    assert str(captured.value) == "KNOWLEDGE_VECTOR_STORE_UNAVAILABLE"
    assert model.closed


def test_disabled_app_does_not_load_reranker(app_factory, settings):
    settings.customer_service_rag_enabled = False
    calls = []

    with TestClient(app_factory(
        reranker_model_factory=lambda *_args, **_kwargs: calls.append("load"),
    )) as client:
        assert isinstance(client.app.state.reranker, DisabledReranker)
    assert calls == []


def test_enabled_disabled_mode_does_not_load_reranker(app_factory, settings):
    settings.customer_service_rag_enabled = True
    settings.rag_reranker_provider = "disabled"
    calls = []

    with TestClient(app_factory(
        knowledge_etl_service=object(), mutation_coordinator=object(),
        reranker_model_factory=lambda *_args, **_kwargs: calls.append("load"),
    )) as client:
        assert isinstance(client.app.state.reranker, DisabledReranker)
    assert calls == []
