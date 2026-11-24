"""逐问题修复工作集的纯状态转换，不读取文件、不调用模型或宣告质量通过。"""

from copy import deepcopy
from typing import Any

from ai_service.models.vue_repair import safe_repair_path, valid_repair_plan
from ai_service.models.vue_plan_diagnostics import PLAN_REJECTION_REASONS, STALLED_REASON
from ai_service.orchestration.vue_budget import is_complete_file_read


def initial_repair_work(issues, manifest):
    if not isinstance(issues, list) or not 1 <= len(issues) <= 12:
        return {}
    limits = {"code": 64, "summary": 300, "evidence": 600, "repair_hint": 600}
    if any(not isinstance(issue, dict) or set(issue) != set(limits)
           or any(not isinstance(issue[key], str) or not 1 <= len(issue[key]) <= size
                  for key, size in limits.items()) for issue in issues):
        return {}
    if (not isinstance(manifest, list) or not 1 <= len(manifest) <= 24
            or not all(safe_repair_path(path) for path in manifest) or len(set(manifest)) != len(manifest)):
        return {}
    return {"issues": deepcopy(issues), "manifest": list(manifest), "active": {}, "completed": [],
            "active_write": False, "read_batches": 0, "planning_only": False}


def repair_work_payload(work):
    """任务和声明可以供模型接续，私有可见性及停滞诊断不进入载荷。"""
    return {"issues": deepcopy(work["issues"]), "allowedFiles": list(work["manifest"]),
            "maxFiles": 3, "activePlan": deepcopy(work["active"]),
            "completedIssueIndexes": list(work["completed"]), "activeWriteSucceeded": work["active_write"]}


class RepairPlanError(ValueError):
    """公开错误保持稳定，内部原因不携带模型响应或工作集正文。"""

    def __init__(self, reason):
        if reason not in PLAN_REJECTION_REASONS | {STALLED_REASON}:
            raise ValueError("未知修复计划诊断原因")
        self.reason = reason
        self.plan_rejection = None
        self.code = "VUE_REPAIR_WORKSET_STALLED" if reason == STALLED_REASON else "VUE_REPAIR_PLAN_INVALID"
        message = ("当前问题已读取必要文件，请先完成修改再继续" if reason == STALLED_REASON
                   else "修复计划或完成声明不符合当前问题及文件范围")
        super().__init__(f"{self.code}: {message}")


def _invalid(reason):
    raise RepairPlanError(reason)


def prepare_work_turn(previous: dict[str, Any], turn: dict[str, Any]):
    """整批执行前校验计划与工具范围；错误响应不允许先执行合法的第一个工具。"""
    work = deepcopy(previous)
    calls = turn["tool_calls"]
    plan, completed = turn.get("repair_plan"), turn.get("completed_issue_index")
    pending = next((i for i in range(len(work["issues"])) if i not in work["completed"]), None)
    if turn.get("file_plan"):
        _invalid("FILE_PLAN_NOT_ALLOWED")
    if completed is not None:
        if type(completed) is not int or completed != pending:
            _invalid("COMPLETION_OUT_OF_ORDER")
        if not work["active"]:
            _invalid("COMPLETION_WITHOUT_PLAN")
        if not work["active_write"]:
            _invalid("COMPLETION_BEFORE_WRITE")
        if calls:
            _invalid("COMPLETION_WITH_TOOLS")
        if plan is not None:
            _invalid("COMPLETION_WITH_PLAN")
        work.update(completed=[*work["completed"], completed], active={}, active_write=False,
                    read_batches=0, planning_only=False)
        return work, "continue"
    if pending is None:
        if calls or plan is not None:
            _invalid("WORK_ALREADY_COMPLETED")
        return work, "finish"
    if plan is not None:
        if not valid_repair_plan(plan):
            _invalid("PLAN_SCHEMA_INVALID")
        if plan["issueIndex"] != pending:
            _invalid("ISSUE_OUT_OF_ORDER")
        if not set(plan["files"]) <= set(work["manifest"]):
            _invalid("PLAN_OUTSIDE_MANIFEST")
        if plan != work["active"]:
            if work["active"] and not work["active_write"]:
                _invalid("WORKSET_SWITCH_BEFORE_WRITE")
            work.update(active=deepcopy(plan), active_write=False, read_batches=0, planning_only=False)
    if not work["active"]:
        _invalid("PLAN_REQUIRED")
    if not calls:
        # 每个工作集只容许一次纯规划响应；后续无工具响应须明确逐项声明完成。
        if plan is None:
            _invalid("EMPTY_TURN")
        if work["planning_only"]:
            _invalid("PLANNING_ONLY_REPEATED")
        work["planning_only"] = True
        return work, "continue"
    for call in calls:
        name = call["name"]
        if name == "image_search":
            continue
        if name not in {"file_read", "file_write", "file_modify", "file_delete"}:
            _invalid("TOOL_NOT_ALLOWED")
        path = call.get("arguments", {}).get("relativeFilePath")
        if not safe_repair_path(path) or path not in work["active"]["files"]:
            _invalid("TOOL_OUTSIDE_WORKSET")
    return work, "execute"


def record_work_result(previous, item):
    work = deepcopy(previous)
    if item.get("tool") in {"file_write", "file_modify"} and item.get("result", {}).get("ok") is True:
        work["active_write"] = True
    return work


def finish_work_batch(previous, results):
    work = deepcopy(previous)
    complete_reads = bool(results) and all(is_complete_file_read(item) for item in results)
    if complete_reads:
        work["read_batches"] += 1
    else:
        work["read_batches"] = 0
    return work


def require_write_after_reads(work, calls, *, remaining_tools=None):
    # 三个完整纯读批次后只允许包含写操作的批次，无法通过换文件继续读；失败仍受工具预算限制。
    if (work["read_batches"] >= 3 and calls
            and not any(call["name"] in {"file_write", "file_modify"} for call in calls)):
        raise RepairPlanError(STALLED_REASON)
    # 首次写入前不能用最后额度继续轮换补读；原纠偏允许利用当前可见源码最小写入。
    if (not work["active_write"] and calls and remaining_tools is not None
            and all(call["name"] == "file_read" for call in calls)
            and len(calls) >= remaining_tools):
        raise RepairPlanError(STALLED_REASON)
