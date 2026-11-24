"""修复异常的固定安全摘要；观察故障不替换原异常或吞掉取消。"""

import logging

from ai_service.models.verification_diagnostics import (
    MAX_VERIFICATION_RECORDS, REPAIR_ERROR_CODES, safe_verification_history,
)
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.vue_budget import budget_counts
from ai_service.orchestration.vue_read_stall import read_repetition_stats

logger = logging.getLogger(__name__)


async def save_repair_failure(workflow, thread_id, state, code):
    if code not in REPAIR_ERROR_CODES:
        return
    loop = state.get("vue_loop", {})
    prefix = loop.get("call_id_prefix", "")
    history = state.get("vue_tool_results", [])
    counts = budget_counts(history, prefix)
    record = {
        "round": state.get("repair_count", 0), "stage": "repair", "verdict": "ERROR", "errorCode": code,
        "toolCount": state.get("tool_call_count", 0), "phaseReadCount": counts["phase_read_count"],
        "phaseWriteCount": counts["phase_write_count"],
        "repeatedReadCount": read_repetition_stats(history, prefix)["total"],
        "visibleRepeatCount": loop.get("visible_repeat_total", 0),
        "omittedRereadCount": loop.get("omitted_reread_count", 0), "issueCount": 0, "issueCodes": [],
    }
    records = safe_verification_history(state.get("verification_history")) or []
    records = [item for item in records if (item["round"], item["stage"]) != (record["round"], "repair")]
    records = safe_verification_history([*records, record][-MAX_VERIFICATION_RECORDS:])
    if records is None:
        return
    workflow._raise_if_cancelled(thread_id)
    try:
        await workflow.checkpoint.save(thread_id, workflow._checkpoint_payload(
            {**state, "verification_history": records}, "repair"))
    except GenerationCancelled:
        raise
    except Exception:
        try:
            logger.warning("Repair failure diagnostic save reason=CHECKPOINT_WRITE_FAILED")
        except Exception:
            pass
    workflow._raise_if_cancelled(thread_id)
