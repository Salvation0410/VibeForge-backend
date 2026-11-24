"""只识别修复阶段的重复完整读取，不推断源码质量或全工程未变化。"""

from typing import Any
from pathlib import PurePosixPath

from ai_service.orchestration.vue_budget import is_complete_file_read
from ai_service.models.vue_context import file_content_fingerprint


def record_visible_read(loop, item, history):
    """读取前模型已见同份完整源码才判无效重读；补读单独计数，仍照常扣工具预算。"""
    progress = {**loop, "visible_files": dict(loop.get("visible_files", {}))}
    progress.setdefault("visible_repeat_total", 0)
    progress.setdefault("omitted_reread_count", 0)
    progress["visible_repeat_count"] = 0
    path = item.get("arguments", {}).get("relativeFilePath")
    if isinstance(path, str):
        path = str(PurePosixPath(path.replace("\\", "/")))
    if is_complete_file_read(item):
        fingerprint = file_content_fingerprint(item["result"]["content"])
        if progress["visible_files"].get(path) == fingerprint:
            progress["visible_repeat_count"] = loop.get("visible_repeat_count", 0) + 1
            progress["visible_repeat_total"] += 1
        elif any(is_complete_file_read(old)
                 and str(PurePosixPath(old["arguments"]["relativeFilePath"].replace("\\", "/"))) == path
                 and file_content_fingerprint(old["result"]["content"]) == fingerprint for old in history):
            progress["omitted_reread_count"] += 1
    elif item.get("tool") in {"file_write", "file_modify", "file_delete"}:
        # 同批后续工具还使用先前模型消息；写入后不能继续把旧指纹视为有效。
        progress["visible_files"].pop(path, None)
    return progress


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
