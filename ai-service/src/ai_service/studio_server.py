"""加载本地凭据但不输出密钥，并启动 Studio Agent Server。"""

import os

from dotenv import load_dotenv


def main() -> None:
    """加载环境变量并启动本地 Studio 服务，关闭云端自动追踪。"""
    load_dotenv(".env", encoding="utf-8", override=False)
    if not os.environ.get("LANGSMITH_API_KEY") and os.environ.get("LANGGRAPH_API_KEY"):
        os.environ["LANGSMITH_API_KEY"] = os.environ["LANGGRAPH_API_KEY"]
    os.environ["LANGSMITH_TRACING"] = "false"
    from langgraph_cli.cli import cli

    cli(["dev", "--host", "127.0.0.1", "--port", "2024", "--no-browser", "--no-reload"])


if __name__ == "__main__":
    main()
