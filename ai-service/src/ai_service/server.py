from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn


def main() -> None:
    """Start Uvicorn with a psycopg-compatible event loop on Windows."""

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
