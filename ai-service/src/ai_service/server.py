from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn


def main() -> None:
    """使用兼容 psycopg 的事件循环启动 Uvicorn，Windows 下固定使用 SelectorEventLoop。"""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = uvicorn.Config(
        "ai_service.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
