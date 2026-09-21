from __future__ import annotations

from typing import Any

import httpx


_STABLE_SPRING_TOOL_ERROR_CODES = frozenset(
    {
        "TOOL_IDEMPOTENCY_CONFLICT",
        "TOOL_EXECUTION_INDETERMINATE",
        "TOOL_EXECUTION_BUSY",
        "TOOL_IDEMPOTENCY_UNAVAILABLE",
    }
)


class SpringToolProtocolError(RuntimeError):
    """Spring 工具响应不符合预期协议。"""

    def __init__(self):
        super().__init__(
            "SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema"
        )


class SpringToolError(RuntimeError):
    """Spring 工具网关返回的明确业务错误。"""

    def __init__(self, *, spring_code: int, message: Any):
        self.spring_code = spring_code
        self.spring_message = message
        normalized = message.strip() if isinstance(message, str) else ""
        stable_code = (
            normalized
            if normalized in _STABLE_SPRING_TOOL_ERROR_CODES
            else "SPRING_TOOL_ERROR"
        )
        super().__init__(
            f"{stable_code}: Spring tool request failed (code={self.spring_code})"
        )


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
                try:
                    payload = response.json()
                except ValueError as error:
                    if attempt + 1 < max_attempts:
                        continue
                    raise SpringToolProtocolError() from error
                if not isinstance(payload, dict):
                    raise SpringToolProtocolError()
                if "code" in payload:
                    code = payload["code"]
                    if type(code) is not int:
                        raise SpringToolProtocolError()
                    if code != 0:
                        raise SpringToolError(
                            spring_code=code,
                            message=payload.get("message"),
                        )
                    if "data" not in payload or not isinstance(payload["data"], dict):
                        raise SpringToolProtocolError()
                    return payload["data"]
                if set(payload) != {"data"} or not isinstance(payload["data"], dict):
                    raise SpringToolProtocolError()
                return payload["data"]
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt + 1 >= max_attempts:
                    raise
            except httpx.TransportError:
                if attempt + 1 >= max_attempts:
                    raise
        raise RuntimeError("Spring tool invocation exhausted without a result")

    async def close(self) -> None:
        """释放异步 HTTP 客户端连接池。"""
        await self._client.aclose()

