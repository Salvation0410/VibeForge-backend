"""Vue修复计划的有界协议；路径只表达候选范围，权限仍由Spring网关负责。"""

from pathlib import PurePosixPath
import re
from typing import Any


def safe_repair_path(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 256 and "\\" not in value
            and not value.startswith("/") and not any(part in {"", ".", ".."} for part in value.split("/"))
            and not re.search(r"[:\x00-\x1f\x7f]", value) and not PurePosixPath(value).is_absolute())


def valid_repair_plan(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"issueIndex", "files"}
            and type(value["issueIndex"]) is int and 0 <= value["issueIndex"] < 12
            and isinstance(value["files"], list) and 1 <= len(value["files"]) <= 3
            and all(safe_repair_path(path) for path in value["files"])
            and len(set(value["files"])) == len(value["files"]))


def snapshot_file_manifest(snapshot: dict[str, Any]) -> list[str]:
    """只采用网关本次快照实际提供的有界路径，不从Reviewer文字推导文件操作。"""
    files = snapshot.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 24:
        return []
    paths = [item.get("path") if isinstance(item, dict) else None for item in files]
    return paths if all(safe_repair_path(path) for path in paths) and len(set(paths)) == len(paths) else []
