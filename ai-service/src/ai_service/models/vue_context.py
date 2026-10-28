"""仅投影模型工作上下文，不裁剪图状态、工具审计历史或真实文件。"""

from copy import deepcopy
import json
from pathlib import PurePosixPath
from typing import Any, Callable

from ai_service.models.input_review import InputReviewError


HISTORY_MESSAGE_BYTES = 24 * 1024
_PATH_FIELDS = ("relativeFilePath", "relativeDirPath", "query")


def _message_bytes(value: object) -> int:
    # 工具结果先序列化进 HumanMessage，再进入预算检查的 JSON，必须计入两层转义。
    inner = json.dumps(value, ensure_ascii=False)
    return len(json.dumps(inner, ensure_ascii=False).encode("utf-8"))


def _budget_error() -> InputReviewError:
    return InputReviewError(
        "VUE_CONTEXT_BUDGET_EXCEEDED",
        "项目工具上下文超过本轮模型预算，压缩历史后仍无法完整提供当前文件。请分步处理较小文件；本轮已停止。",
    )


def project_vue_context(context: dict[str, Any], *, fits: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """按新近程度选取完整结果；摘要显式标记省略，绝不截取半份源码。"""
    history = context.get("toolResults", [])
    if not history:
        return context
    projected = {**context, "toolContextPolicy": (
        "toolResults 中 contextOmitted=true 的结果仅为操作摘要，不是完整源码；"
        "修改前若所需源码已省略，请按 arguments 中路径重新 file_read，只读取当前任务需要的文件。"
        "reason=superseded 表示该读取结果已被后续读取或成功修改替代，不得使用旧源码覆盖当前文件。"
        "省略不代表文件缺失，也不能仅凭操作摘要认定实现完整。"
    )}
    summaries = []
    stale = set()
    touched = set()
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        path = item.get("arguments", {}).get("relativeFilePath")
        if not path:
            continue
        path = str(PurePosixPath(path.replace("\\", "/")))
        if item["tool"] == "file_read" and path in touched:
            stale.add(index)
        if item["tool"] in {"file_read", "file_write", "file_modify", "file_delete"} and item["result"].get("ok", True):
            touched.add(path)
    for index, item in enumerate(history):
        summaries.append({
            "toolCallId": item["toolCallId"], "tool": item["tool"],
            "arguments": {key: value for key, value in item.get("arguments", {}).items() if key in _PATH_FIELDS},
            "result": {"contextOmitted": True, "reason": "superseded" if index in stale else "history_budget",
                       **({"ok": item["result"]["ok"]} if "ok" in item["result"] else {})},
        })
    projected["toolResults"] = summaries
    if _message_bytes(summaries) > HISTORY_MESSAGE_BYTES or not fits(projected):
        raise _budget_error()
    # 最新结果是模型下一步操作的直接依据，放不下必须失败，不能诱导反复重读同一大文件。
    for index in range(len(history) - 1, -1, -1):
        if index in stale:
            continue
        summary = summaries[index]
        summaries[index] = deepcopy(history[index])
        if _message_bytes(summaries) > HISTORY_MESSAGE_BYTES or not fits(projected):
            summaries[index] = summary
            if index == len(history) - 1:
                raise _budget_error()
    return projected
