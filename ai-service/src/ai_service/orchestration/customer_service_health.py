from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class CustomerServiceHealthProvider(Protocol):
    async def probe(self) -> Mapping[str, bool]: ...


@dataclass(frozen=True, slots=True)
class CustomerServiceHealthSummary:
    enabled: bool
    status: str
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


async def _probe_dependency(dependency: Any) -> bool:
    try:
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
        return CustomerServiceHealthSummary(False, "disabled", False, False, {})
    if provider is None:
        return CustomerServiceHealthSummary(True, "degraded", False, True, {})
    try:
        async with asyncio.timeout(timeout_seconds):
            raw = await provider.probe()
    except asyncio.CancelledError:
        raise
    except Exception:
        fallback = getattr(provider, "failure_summary", None)
        raw = fallback() if callable(fallback) else {}
    if not isinstance(raw, Mapping):
        fallback = getattr(provider, "failure_summary", None)
        raw = fallback() if callable(fallback) else {}
    if not isinstance(raw, Mapping):
        raw = {}
    dependencies = {
        str(name): value is True
        for name, value in sorted(raw.items())
        if isinstance(name, str) and 0 < len(name) <= 64
    }
    ready = bool(dependencies) and all(dependencies.values())
    return CustomerServiceHealthSummary(
        True,
        "healthy" if ready else "degraded",
        ready,
        not ready,
        dependencies,
    )
