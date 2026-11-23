"""只识别修复阶段的重复完整读取，不推断源码质量或全工程未变化。"""

from typing import Any

from ai_service.orchestration.vue_budget import is_complete_file_read


def read_repetition_stats(history: list[dict[str, Any]], prefix: str) -> dict[str, int]:
    """从完整审计历史计算阶段计数；仅返回数字，路径和源码不得进入诊断。"""
    repeated = 0
    total = 0
    seen: dict[str, str] = {}
    for item in history:
        if item.get("toolCallId", "").rpartition(":")[0] != prefix:
            continue
        result = item.get("result", {})
        path = item.get("arguments", {}).get("relativeFilePath")
        # 未知字段可能表示截断或失败；不能把部分源码及操作摘要用作相同内容证据。
        if not is_complete_file_read(item):
            seen.clear()
            repeated = 0
            continue
        content = result["content"]
        if path in seen and seen[path] == content:
            repeated += 1
            total += 1
        else:
            repeated = 0
        seen[path] = content
    return {"consecutive": repeated, "total": total}


def phase_has_successful_write(history: list[dict[str, Any]], prefix: str) -> bool:
    return any(item.get("toolCallId", "").rpartition(":")[0] == prefix
               and item.get("tool") in {"file_write", "file_modify"}
               and item.get("result", {}).get("ok") is True for item in history)


def repair_reads_stalled(history: list[dict[str, Any]], prefix: str) -> bool:
    """本阶段成功写入后，连续四次无新增读取信息才允许交回原验证。"""
    return phase_has_successful_write(history, prefix) and read_repetition_stats(history, prefix)["consecutive"] >= 4
