"""只识别修复阶段的重复完整读取，不推断源码质量或全工程未变化。"""

from typing import Any

from ai_service.orchestration.vue_budget import is_complete_file_read


def repair_reads_stalled(history: list[dict[str, Any]], prefix: str) -> bool:
    """本阶段成功写入后，连续四次无新增读取信息才允许交回原验证。"""
    written = False
    repeated = 0
    seen: dict[str, str] = {}
    for item in history:
        if item.get("toolCallId", "").rpartition(":")[0] != prefix:
            continue
        tool, result = item.get("tool"), item.get("result", {})
        if tool in {"file_write", "file_modify"} and result.get("ok") is True:
            written = True
        path = item.get("arguments", {}).get("relativeFilePath")
        # 未知字段可能表示截断或失败；不能把部分源码及操作摘要用作相同内容证据。
        if not is_complete_file_read(item):
            seen.clear()
            repeated = 0
            continue
        content = result["content"]
        if path in seen and seen[path] == content:
            repeated += 1
        else:
            repeated = 0
        seen[path] = content
    return written and repeated >= 4
