from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Mapping, Protocol


REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES = frozenset({
    "answerModel",
    "answerService",
    "embedding",
    "etl",
    "leaseValidator",
    "milvus",
    "reranker",
})


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
        self._dependencies = dict(dependencies)

    def failure_summary(self) -> dict[str, bool]:
        return {name: False for name in sorted(self._dependencies)}

    async def probe(self) -> dict[str, bool]:
        names = sorted(self._dependencies)
        results = await asyncio.gather(*(
            _probe_dependency(self._dependencies[name]) for name in names
        ))
        return dict(zip(names, results, strict=True))


class CustomerServiceDependencyReference:
    """Resolve mutable application state at probe time."""

    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    async def health_ready(self) -> bool:
        return await _probe_dependency(self._getter())


def _failure_summary(provider: Any) -> Mapping[str, bool]:
    try:
        fallback = getattr(provider, "failure_summary", None)
        raw = fallback() if callable(fallback) else {}
    except Exception:
        return {}
    return raw if isinstance(raw, Mapping) else {}


async def _probe_dependency(dependency: Any) -> bool:
    try:
        probe = getattr(dependency, "health_ready", None)
        if probe is None:
            probe = getattr(dependency, "ping", None)
        if probe is None:
            probe = dependency if callable(dependency) else None
        if probe is None:
            return dependency is not None
        if inspect.iscoroutinefunction(probe):
            value = await probe()
        else:
            value = await asyncio.to_thread(probe)
            if inspect.isawaitable(value):
                value = await value
        return value is True
    except asyncio.CancelledError:
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
        async with asyncio.timeout(timeout_seconds):
            raw = await provider.probe()
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_TIMEOUT"
        raw = _failure_summary(provider)
    except Exception:
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_FAILED"
        raw = _failure_summary(provider)
    if not isinstance(raw, Mapping):
        reason = "CUSTOMER_SERVICE_HEALTH_PROBE_FAILED"
        raw = _failure_summary(provider)
    valid_items = [
        (name, value)
        for name, value in raw.items()
        if isinstance(name, str) and 0 < len(name) <= 64
    ]
    dependencies = {
        name: value is True
        for name, value in sorted(valid_items, key=lambda item: item[0])
    }
    for name in REQUIRED_CUSTOMER_SERVICE_DEPENDENCIES:
        dependencies.setdefault(name, False)
    dependencies = dict(sorted(dependencies.items()))
    ready = bool(dependencies) and all(dependencies.values())
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
