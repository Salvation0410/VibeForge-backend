from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from ai_service.infrastructure.milvus_knowledge import (
    KnowledgeMutationLease,
    MilvusKnowledgeError,
)


def _validation_url(gateway_base_url: str) -> str:
    parsed = urlsplit(gateway_base_url)
    marker = "/internal/"
    prefix = parsed.path.split(marker, 1)[0] + "/internal"
    path = f"{prefix}/customer-service/knowledge-mutation-leases:validate"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _health_url(validation_url: str) -> str:
    return validation_url.replace(
        "/knowledge-mutation-leases:validate",
        "/knowledge-mutation-leases/health",
    )


@dataclass(slots=True)
class _SpringMutationPermit:
    coordinator: "SpringKnowledgeMutationCoordinator"
    lease: KnowledgeMutationLease
    scope: str
    operation: str

    @property
    def fence(self) -> int:
        return self.lease.fence

    async def assert_current(self) -> None:
        await self.coordinator.validate(
            self.lease, scope=self.scope, operation=self.operation
        )


class SpringKnowledgeMutationCoordinator:
    """Fail-closed adapter for Spring/MySQL's authoritative mutation leases."""

    def __init__(
        self, *, gateway_base_url: str, bearer_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._validation_url = _validation_url(gateway_base_url)
        self._health_url = _health_url(self._validation_url)
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer_token}"},
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        )

    async def validate(
        self, lease: KnowledgeMutationLease, *, scope: str, operation: str,
    ) -> None:
        external_operation = {
            "upsert": "INDEX",
            "delete": "DELETE",
            "rebuild": "REBUILD",
        }.get(operation)
        if external_operation is None or lease.operation != external_operation:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
        request = {
            "scope": lease.scope,
            "operationId": lease.operation_id,
            "operation": lease.operation,
            "fence": lease.fence,
            "expiresAt": lease.expires_at,
            "proof": lease.proof,
        }
        try:
            response = await self._client.post(self._validation_url, json=request)
            response.raise_for_status()
            payload = response.json()
            data: Any = payload.get("data") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or payload.get("code") != 0
                or not isinstance(data, dict)
                or data.get("verified") is not True
                or data.get("current") is not True
                or data.get("scope") != scope
                or data.get("operationId") != lease.operation_id
                or data.get("operation") != lease.operation
                or type(data.get("fence")) is not int
                or data.get("fence") != lease.fence
                or not isinstance(data.get("expiresAt"), (int, float))
                or float(data["expiresAt"]) != lease.expires_at
            ):
                raise ValueError
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID") from None

    async def ping(self) -> bool:
        try:
            response = await self._client.get(self._health_url)
            response.raise_for_status()
            payload = response.json()
            return bool(
                isinstance(payload, dict)
                and payload.get("code") == 0
                and isinstance(payload.get("data"), dict)
                and payload["data"].get("ready") is True
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return False

    @asynccontextmanager
    async def hold(
        self, lease: KnowledgeMutationLease, *, scope: str, operation: str,
    ) -> AsyncIterator[_SpringMutationPermit]:
        if lease.scope != scope:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
        permit = _SpringMutationPermit(self, lease, scope, operation)
        await permit.assert_current()
        yield permit

    async def close(self) -> None:
        await self._client.aclose()
