from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Awaitable

from fastapi import Header, HTTPException, status


def create_internal_auth_dependency(token: str) -> Callable[..., Awaitable[None]]:
    """创建内部接口鉴权依赖，并使用常量时间比较校验 Bearer 令牌。"""

    async def require_internal_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid internal bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_internal_auth
