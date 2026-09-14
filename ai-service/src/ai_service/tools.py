from __future__ import annotations

from typing import Any

import httpx


class SpringToolGateway:
    """The only boundary for project operations owned by the Spring service."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=str(base_url).rstrip("/"),
            headers={"Authorization": f"Bearer {bearer_token}"},
            transport=transport,
            timeout=httpx.Timeout(30.0),
        )

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/invoke",
            json={
                "toolCallId": tool_call_id,
                "toolName": name,
                "arguments": arguments,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("data", payload)

    async def close(self) -> None:
        await self._client.aclose()

