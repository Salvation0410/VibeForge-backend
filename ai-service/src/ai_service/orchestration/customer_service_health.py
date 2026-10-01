from __future__ import annotations

import asyncio
import inspect
import threading
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES = (
    "answerModel",
    "answerService",
    "embedding",
    "etl",
    "leaseValidator",
    "milvus",
    "reranker",
)

_UNSET = object()
_SYNC_PROBE_POLL_SECONDS = 0.005
_SYNC_PROBE_COMPLETED_CACHE_LIMIT = len(REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES)


class CustomerServiceHealthProvider(Protocol):
    async def probe(self) -> Mapping[str, bool]: ...


@dataclass(frozen=True, slots=True)
class CustomerServiceHealthSummary:
    enabled: bool
    status: str
    reason: str
    ready: bool
    degraded: bool
    dependencies: dict[str, bool]


class CustomerServiceDependencyHealth:
    """Probe injected dependencies without exposing provider details."""

    def __init__(self, dependencies: Mapping[str, Any]) -> None:
        self._dependencies = {
            name: dependencies.get(name)
            for name in REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES
        }
        self._sync_runner = _DaemonSingleFlightProbe()
        self._async_runner = _AsyncSingleFlightProbe()
        self._provider_async_runner = _AsyncSingleFlightProbe()

    def failure_summary(self) -> dict[str, bool]:
        return _unavailable_dependencies()

    async def probe(self) -> dict[str, bool]:
        results: list[bool] = []
        for name in REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES:
            dependency = self._dependencies[name]
            if isinstance(dependency, CustomerServiceDependencyReference):
                dependency = dependency.resolve()
            results.append(await _probe_dependency(
                dependency,
                self._sync_runner,
                self._async_runner,
                key=(name, id(dependency)),
            ))
        return dict(zip(
            REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES, results, strict=True,
        ))

    async def close_health_probes(self) -> None:
        await self._provider_async_runner.cancel()
        await self._async_runner.cancel()
        for dependency in self._dependencies.values():
            if isinstance(dependency, CustomerServiceDependencyReference):
                await dependency.close_health_probes()


class CustomerServiceDependencyReference:
    """Resolve mutable application state at probe time."""

    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter
        self._sync_runner = _DaemonSingleFlightProbe()
        self._async_runner = _AsyncSingleFlightProbe()

    def resolve(self) -> Any:
        return self._getter()

    async def health_ready(self) -> bool:
        dependency = self.resolve()
        return await _probe_dependency(
            dependency,
            self._sync_runner,
            self._async_runner,
            key=id(dependency),
        )

    async def close_health_probes(self) -> None:
        await self._async_runner.cancel()


@dataclass(slots=True)
class _AsyncProbeRun:
    key: object
    task: asyncio.Task[Any]


class _AsyncProbeBusyError(RuntimeError):
    pass


def _consume_async_task(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _await_probe(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


class _AsyncSingleFlightProbe:
    """Share one awaitable probe task without propagating waiter cancellation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run: _AsyncProbeRun | None = None

    async def invoke(
        self,
        probe: Callable[[], Awaitable[Any]],
        *,
        key: object = None,
    ) -> Any:
        loop = asyncio.get_running_loop()
        with self._lock:
            current = self._run
            if current is not None:
                if current.key != key or current.task.get_loop() is not loop:
                    previous_loop = current.task.get_loop()
                    if current.task.done() or not previous_loop.is_running():
                        _consume_async_task(current.task)
                        self._run = None
                        current = None
                    else:
                        raise _AsyncProbeBusyError(
                            "customer service async health probe busy"
                        )
            if current is None:
                awaitable = probe()
                if not inspect.isawaitable(awaitable):
                    raise TypeError("async health probe must return an awaitable")
                task = loop.create_task(_await_probe(awaitable))
                task.add_done_callback(_consume_async_task)
                current = _AsyncProbeRun(key=key, task=task)
                self._run = current

        try:
            return await asyncio.shield(current.task)
        finally:
            if current.task.done():
                with self._lock:
                    if self._run is current:
                        self._run = None

    async def invoke_awaitable(self, awaitable: Awaitable[Any], *, key: object) -> Any:
        used = False

        def probe() -> Awaitable[Any]:
            nonlocal used
            used = True
            return awaitable

        try:
            return await self.invoke(probe, key=key)
        finally:
            if not used:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()

    async def cancel(self, *, key: object = _UNSET) -> None:
        with self._lock:
            current = self._run
            if current is None or (key is not _UNSET and current.key != key):
                return
            self._run = None
        task = current.task
        loop = task.get_loop()
        if task.done():
            _consume_async_task(task)
            return
        running_loop = asyncio.get_running_loop()
        if loop is running_loop:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.1)
            for completed in done:
                _consume_async_task(completed)
        elif loop.is_running():
            loop.call_soon_threadsafe(task.cancel)
        else:
            try:
                task.cancel()
            except RuntimeError:
                pass


@dataclass(slots=True)
class _SyncProbeRun:
    key: object
    done: threading.Event = field(default_factory=threading.Event)
    value: Any = _UNSET
    error: Exception | None = None


class _SyncProbeBusyError(RuntimeError):
    pass


class _DaemonSingleFlightProbe:
    """Run at most one blocking sync probe without using the default executor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run: _SyncProbeRun | None = None
        self._completed: dict[object, _SyncProbeRun] = {}

    async def invoke(self, probe: Callable[[], Any], *, key: object = None) -> Any:
        with self._lock:
            current = self._completed.pop(key, None)
            if current is None:
                current = self._run
            if current is not None and current.key != key:
                if current.done.is_set():
                    if len(self._completed) >= _SYNC_PROBE_COMPLETED_CACHE_LIMIT:
                        self._completed.pop(next(iter(self._completed)))
                    self._completed[current.key] = current
                    self._run = None
                    current = None
                else:
                    raise _SyncProbeBusyError(
                        "customer service sync health probe busy"
                    )
            if current is None:
                current = _SyncProbeRun(key=key)
                self._run = current
                threading.Thread(
                    target=self._execute,
                    args=(current, probe),
                    name="customer-service-health-probe",
                    daemon=True,
                ).start()

        while not current.done.is_set():
            await asyncio.sleep(_SYNC_PROBE_POLL_SECONDS)

        with self._lock:
            if self._run is current:
                self._run = None
        if current.error is not None:
            raise current.error
        if current.value is _UNSET:
            raise RuntimeError("customer service sync health probe failed")
        return current.value

    @staticmethod
    def _execute(current: _SyncProbeRun, probe: Callable[[], Any]) -> None:
        try:
            current.value = probe()
        except Exception as error:
            current.error = error
        except BaseException:
            current.error = RuntimeError(
                "customer service sync health probe failed"
            )
        finally:
            current.done.set()


_provider_runner_lock = threading.Lock()
_provider_runners: dict[
    int, tuple[weakref.ReferenceType[Any], _DaemonSingleFlightProbe]
] = {}
_fallback_provider_runner = _DaemonSingleFlightProbe()
_provider_async_runner_lock = threading.Lock()
_provider_async_runners: dict[
    int, tuple[weakref.ReferenceType[Any], _AsyncSingleFlightProbe]
] = {}
_fallback_provider_async_runner = _AsyncSingleFlightProbe()


def _discard_provider_runner(
    provider_id: int,
    reference: weakref.ReferenceType[Any],
) -> None:
    with _provider_runner_lock:
        current = _provider_runners.get(provider_id)
        if current is not None and current[0] is reference:
            _provider_runners.pop(provider_id, None)


def _discard_provider_async_runner(
    provider_id: int,
    reference: weakref.ReferenceType[Any],
) -> None:
    with _provider_async_runner_lock:
        current = _provider_async_runners.get(provider_id)
        if current is not None and current[0] is reference:
            _provider_async_runners.pop(provider_id, None)


def _provider_sync_runner(
    provider: Any,
) -> tuple[_DaemonSingleFlightProbe, object]:
    provider_id = id(provider)
    try:
        reference = weakref.ref(
            provider,
            lambda value: _discard_provider_runner(provider_id, value),
        )
    except TypeError:
        return _fallback_provider_runner, provider_id
    try:
        with _provider_runner_lock:
            current = _provider_runners.get(provider_id)
            if current is not None and current[0]() is provider:
                return current[1], None
            runner = _DaemonSingleFlightProbe()
            _provider_runners[provider_id] = (reference, runner)
            return runner, None
    except Exception:
        return _fallback_provider_runner, provider_id


def _provider_async_runner_for(
    provider: Any,
) -> tuple[_AsyncSingleFlightProbe, object]:
    owned = getattr(provider, "_provider_async_runner", None)
    if isinstance(owned, _AsyncSingleFlightProbe):
        return owned, None
    provider_id = id(provider)
    try:
        reference = weakref.ref(
            provider,
            lambda value: _discard_provider_async_runner(provider_id, value),
        )
    except TypeError:
        return _fallback_provider_async_runner, provider_id
    try:
        with _provider_async_runner_lock:
            current = _provider_async_runners.get(provider_id)
            if current is not None and current[0]() is provider:
                return current[1], None
            runner = _AsyncSingleFlightProbe()
            _provider_async_runners[provider_id] = (reference, runner)
            return runner, None
    except Exception:
        return _fallback_provider_async_runner, provider_id


async def _invoke_provider(provider: Any) -> Any:
    probe = getattr(provider, "probe", None)
    if not callable(probe):
        raise TypeError("customer service health provider requires probe")
    if inspect.iscoroutinefunction(probe):
        if isinstance(provider, CustomerServiceDependencyHealth):
            return await probe()
        runner, key = _provider_async_runner_for(provider)
        return await runner.invoke(probe, key=key)
    runner, key = _provider_sync_runner(provider)
    value = await runner.invoke(probe, key=key)
    if not inspect.isawaitable(value):
        return value
    async_runner, async_key = _provider_async_runner_for(provider)
    return await async_runner.invoke_awaitable(
        value, key=("sync", async_key),
    )


def _unavailable_dependencies() -> dict[str, bool]:
    return {name: False for name in REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES}


async def _probe_dependency(
    dependency: Any,
    sync_runner: _DaemonSingleFlightProbe,
    async_runner: _AsyncSingleFlightProbe,
    *,
    key: object,
) -> bool:
    try:
        probe = getattr(dependency, "health_ready_sync", None)
        if probe is None:
            probe = getattr(dependency, "health_ready", None)
        if probe is None:
            probe = getattr(dependency, "ping", None)
        if probe is None:
            probe = dependency if callable(dependency) else None
        if probe is None:
            return dependency is not None
        if inspect.iscoroutinefunction(probe):
            value = await async_runner.invoke(probe, key=key)
        else:
            value = await sync_runner.invoke(probe, key=id(dependency))
            if inspect.isawaitable(value):
                value = await async_runner.invoke_awaitable(value, key=key)
        return value is True
    except asyncio.CancelledError:
        raise
    except _SyncProbeBusyError:
        raise
    except _AsyncProbeBusyError:
        raise
    except Exception:
        return False


async def probe_customer_service_health(
    provider: CustomerServiceHealthProvider | None,
    *,
    enabled: bool,
    timeout_seconds: float = 1.0,
) -> CustomerServiceHealthSummary:
    """Return a bounded, fail-safe summary containing only stable booleans."""

    if not enabled:
        return CustomerServiceHealthSummary(
            False, "disabled", "CUSTOMER_SERVICE_RAG_DISABLED", False, False, {},
        )
    if provider is None:
        return CustomerServiceHealthSummary(
            True, "degraded", "CUSTOMER_SERVICE_HEALTH_NOT_CONFIGURED",
            False, True, {},
        )
    reason: str | None = None
    try:
        raw = await asyncio.wait_for(
            _invoke_provider(provider), timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_TIMEOUT"
        raw = {}
    except _SyncProbeBusyError:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_BUSY"
        raw = {}
    except _AsyncProbeBusyError:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_BUSY"
        raw = {}
    except Exception:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_FAILED"
        raw = {}
    if not isinstance(raw, Mapping):
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_FAILED"
        raw = {}
    dependencies = {
        name: raw.get(name) is True
        for name in REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES
    }
    ready = all(dependencies.values())
    if reason is None:
        reason = (
            "CUSTOMER_SERVICE_READY"
            if ready else "CUSTOMER_SERVICE_DEPENDENCY_UNAVAILABLE"
        )
    return CustomerServiceHealthSummary(
        True,
        "healthy" if ready else "degraded",
        reason,
        ready,
        not ready,
        dependencies,
    )


async def dispose_customer_service_health_provider(provider: Any) -> None:
    """Best-effort cancellation for background probe tasks during lifespan close."""

    runner, key = _provider_async_runner_for(provider)
    await runner.cancel(key=key)
    close_probes = getattr(provider, "close_health_probes", None)
    if callable(close_probes):
        result = close_probes()
        if inspect.isawaitable(result):
            await result
