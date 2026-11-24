"""计划拒绝的安全协议：只有固定原因、布尔和计数可进入状态摘要。"""

from typing import Any


PLAN_REJECTION_REASONS = frozenset({
    "FILE_PLAN_NOT_ALLOWED", "COMPLETION_OUT_OF_ORDER", "COMPLETION_WITHOUT_PLAN",
    "COMPLETION_BEFORE_WRITE", "COMPLETION_WITH_TOOLS", "COMPLETION_WITH_PLAN",
    "WORK_ALREADY_COMPLETED", "PLAN_SCHEMA_INVALID", "ISSUE_OUT_OF_ORDER",
    "PLAN_OUTSIDE_MANIFEST", "WORKSET_SWITCH_BEFORE_WRITE", "PLAN_REQUIRED", "EMPTY_TURN",
    "PLANNING_ONLY_REPEATED", "TOOL_NOT_ALLOWED", "TOOL_OUTSIDE_WORKSET",
})
STALLED_REASON = "WRITE_REQUIRED_AFTER_READS"
PLAN_BOOL_FIELDS = frozenset({"hasPlan", "hasCompletion", "hasActivePlan", "activeWriteSucceeded"})
PLAN_COUNT_LIMITS = {
    "formatRetryCount": 2, "requestedToolCount": 40, "planFileCount": 40, "activeFileCount": 3,
    "issueCount": 12, "completedIssueCount": 12, "readBatchCount": 10000,
}


def safe_plan_rejection(value: Any, code: str) -> dict[str, Any] | None:
    """两次白名单共用同一严格结构；不截取自由文本或补造旧记录。"""
    if not isinstance(value, dict) or set(value) != {"reason"} | PLAN_BOOL_FIELDS | PLAN_COUNT_LIMITS.keys():
        return None
    reason = value["reason"]
    if type(reason) is not str:
        return None
    if not ((code == "VUE_REPAIR_PLAN_INVALID" and reason in PLAN_REJECTION_REASONS)
            or (code == "VUE_REPAIR_WORKSET_STALLED" and reason == STALLED_REASON)):
        return None
    if any(type(value[key]) is not bool for key in PLAN_BOOL_FIELDS):
        return None
    if any(type(value[key]) is not int or not 0 <= value[key] <= limit
           for key, limit in PLAN_COUNT_LIMITS.items()):
        return None
    if not 1 <= value["issueCount"] or value["completedIssueCount"] > value["issueCount"]:
        return None
    return dict(value)


def plan_rejection_snapshot(reason, work, turn, retries):
    """只统计形状，不保留原始问题、路径、参数、工具名或content。"""
    plan, active = turn.get("repair_plan"), work.get("active", {})
    files = plan.get("files", []) if isinstance(plan, dict) else []
    counts = {
        "formatRetryCount": retries, "requestedToolCount": len(turn["tool_calls"]),
        "planFileCount": len(files) if isinstance(files, list) else 0,
        "activeFileCount": len(active.get("files", [])), "issueCount": len(work["issues"]),
        "completedIssueCount": len(work["completed"]), "readBatchCount": work["read_batches"],
    }
    return {
        "reason": reason, "hasPlan": plan is not None,
        "hasCompletion": turn.get("completed_issue_index") is not None,
        "hasActivePlan": bool(active), "activeWriteSucceeded": work["active_write"] is True,
        **{key: min(max(value, 0), PLAN_COUNT_LIMITS[key]) for key, value in counts.items()},
    }
