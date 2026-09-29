from __future__ import annotations

import asyncio

from ai_service.config import get_settings
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint


async def main_async() -> None:
    """只初始化 checkpoint schema，不启动 FastAPI、模型或 Spring 网关。"""
    settings = get_settings()
    checkpoint = PostgresCheckpoint(
        settings.checkpoint_postgres_url,
        required=True,
        auto_setup=True,
        ttl_seconds=settings.checkpoint_ttl_seconds,
        pool_min_size=settings.checkpoint_pool_min_size,
        pool_max_size=settings.checkpoint_pool_max_size,
    )
    await checkpoint.start()
    await checkpoint.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
