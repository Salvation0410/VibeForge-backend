from __future__ import annotations

from typing import Any

import httpx


class SpringToolGateway:
    """调用 Spring 工具网关，是 Python 服务操作项目文件和执行构建的唯一边界。"""

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
        """携带幂等调用 ID 执行工具，并返回 Spring 统一响应中的业务数据。"""
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
        """释放异步 HTTP 客户端连接池。"""
        await self._client.aclose()

