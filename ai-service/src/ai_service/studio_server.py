"""Load local credentials without printing them and launch Studio's server."""

import os

from dotenv import load_dotenv


def main() -> None:
    load_dotenv(".env", encoding="utf-8", override=False)
    if not os.environ.get("LANGSMITH_API_KEY") and os.environ.get("LANGGRAPH_API_KEY"):
        os.environ["LANGSMITH_API_KEY"] = os.environ["LANGGRAPH_API_KEY"]
    os.environ["LANGSMITH_TRACING"] = "false"
    from langgraph_cli.cli import cli

    cli(["dev", "--host", "127.0.0.1", "--port", "2024", "--no-browser", "--no-reload"])


if __name__ == "__main__":
    main()
