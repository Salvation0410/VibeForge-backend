"""Vue 工具预算的有界计数与完整读取资格，不推断源码质量。"""

from typing import Any


REPAIR_READ_BATCH_LIMIT = 3
REPAIR_TOOL_RESERVE = 4


def is_complete_file_read(item: dict[str, Any]) -> bool:
    """只有成功且完整的有界源码读取才可累计纯读批次。"""
    result = item.get("result", {})
    path = item.get("arguments", {}).get("relativeFilePath")
    content = result.get("content")
    return (
        item.get("tool") == "file_read" and isinstance(path, str) and bool(path)
        and set(result) <= {"content", "ok"} and result.get("ok", True) is True
        and isinstance(content, str) and len(content) <= 12000
        and len(content.encode("utf-8", errors="surrogatepass")) <= 48000
    )


def budget_counts(history: list[dict[str, Any]], prefix: str) -> dict[str, int]:
    """只返回计数；调用参数、路径和结果正文不得进入诊断日志。"""
    counts = {key: 0 for key in ("read_count", "write_count", "other_count",
                               "phase_tool_count", "phase_read_count", "phase_write_count", "phase_other_count")}
    for item in history:
        tool = item.get("tool")
        kind = "read" if tool == "file_read" else (
            "write" if tool in {"file_write", "file_modify"} else "other"
        )
        counts[f"{kind}_count"] += 1
        if item.get("toolCallId", "").rpartition(":")[0] == prefix:
            counts["phase_tool_count"] += 1
            counts[f"phase_{kind}_count"] += 1
    return counts
