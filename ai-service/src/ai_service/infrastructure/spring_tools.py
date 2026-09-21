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
        app_id: str,
        request_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        """携带幂等调用 ID 执行工具；发布响应不确定时使用原 ID 有界重试。"""
        request_body = {
            "appId": app_id,
            "requestId": request_id,
            "toolCallId": tool_call_id,
            "toolName": name,
            "arguments": arguments,
        }
        max_attempts = 2 if name == "artifact_publish" else 1
        for attempt in range(max_attempts):
            try:
                response = await self._client.post("/invoke", json=request_body)
                response.raise_for_status()
                payload = response.json()
                return payload.get("data", payload)
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt + 1 >= max_attempts:
                    raise
            except (httpx.TransportError, ValueError):
                if attempt + 1 >= max_attempts:
                    raise
        raise RuntimeError("Spring tool invocation exhausted without a result")

    async def close(self) -> None:
        """释放异步 HTTP 客户端连接池。"""
        await self._client.aclose()

