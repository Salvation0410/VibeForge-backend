"""逐轮验证诊断契约；只保留固定枚举和计数，不保存模型理由及工具正文。"""

from typing import Any

from ai_service.models.review_diagnostics import ISSUE_CODES
from ai_service.models.single_quality_review import OUTPUT_FAILURE_REASONS

MAX_VERIFICATION_RECORDS = 36
REPAIR_ERROR_CODES = frozenset({
    "VUE_REPAIR_READ_STALLED", "VUE_TOOL_CALL_LIMIT_EXCEEDED", "VUE_CONTEXT_BUDGET_EXCEEDED",
    "VUE_MODEL_INVALID_OUTPUT", "VUE_REPAIR_PLAN_INVALID", "VUE_REPAIR_WORKSET_STALLED",
})
ERROR_CODES = frozenset({
    "NONE", "UNCLASSIFIED", "ARTIFACT_VALIDATION_FAILED", "QUALITY_REVIEW_FAILED",
    "QUALITY_REVIEW_INVALID_OUTPUT", "QUALITY_REVIEW_INCOMPLETE",
    "VUE_COMPONENT_CONTRACT_FAILED", "VUE_COMPONENT_CHECK_UNAVAILABLE",
    "VUE_EVENT_CONTRACT_FAILED", "VUE_EVENT_CHECK_UNAVAILABLE",
    "VUE_ANCHOR_TARGET_MISSING", "VUE_ANCHOR_CHECK_UNAVAILABLE",
    "VUE_PROJECT_DIR_MISSING", "VUE_PACKAGE_JSON_MISSING", "VUE_NPM_INSTALL_FAILED",
    "VUE_NPM_BUILD_FAILED", "VUE_DIST_INDEX_MISSING", "VUE_BUILD_FAILED", "VUE_BUILD_CANCELLED",
    "VUE_BUILD_WAIT_FAILED", "VUE_BUILD_IN_PROGRESS",
}) | REPAIR_ERROR_CODES
COUNT_FIELDS = frozenset({"round", "toolCount", "phaseReadCount", "phaseWriteCount", "repeatedReadCount", "issueCount"})
FIELDS = COUNT_FIELDS | {"stage", "verdict", "errorCode", "issueCodes"}
REPAIR_COUNT_FIELDS = frozenset({"visibleRepeatCount", "omittedRereadCount"})


def safe_verification_history(value: Any) -> list[dict[str, Any]] | None:
    """写入摘要前二次校验；旧记录缺字段时省略，不补造历史或截取自由文本。"""
    if not isinstance(value, list) or len(value) > MAX_VERIFICATION_RECORDS:
        return None
    result = []
    for record in value:
        if not isinstance(record, dict) or set(record) not in (FIELDS, FIELDS | {"outputReason"}, FIELDS | REPAIR_COUNT_FIELDS):
            return None
        if any(type(record[key]) is not int or not 0 <= record[key] <= 10000 for key in COUNT_FIELDS):
            return None
        if record["round"] > 5 or record["issueCount"] > 12:
            return None
        if type(record["stage"]) is not str or record["stage"] not in {"artifact_validation", "project_build", "quality_review", "repair"}:
            return None
        if type(record["verdict"]) is not str or record["verdict"] not in {"PASS", "REPAIR", "ERROR"}:
            return None
        if type(record["errorCode"]) is not str or record["errorCode"] not in ERROR_CODES:
            return None
        if (record["verdict"] == "PASS") != (record["errorCode"] == "NONE"):
            return None
        parse_codes = {"QUALITY_REVIEW_INVALID_OUTPUT", "QUALITY_REVIEW_INCOMPLETE"}
        if record["stage"] == "repair":
            if (set(record) != FIELDS | REPAIR_COUNT_FIELDS or record["verdict"] != "ERROR"
                    or record["errorCode"] not in REPAIR_ERROR_CODES or record["issueCount"] or record["issueCodes"]
                    or any(type(record[key]) is not int or not 0 <= record[key] <= record["phaseReadCount"]
                           for key in REPAIR_COUNT_FIELDS)
                    or sum(record[key] for key in REPAIR_COUNT_FIELDS) > record["phaseReadCount"]):
                return None
        elif REPAIR_COUNT_FIELDS & set(record) or record["errorCode"] in REPAIR_ERROR_CODES:
            return None
        elif record["verdict"] == "ERROR":
            reason = record.get("outputReason")
            if (record["stage"] != "quality_review" or record["errorCode"] not in parse_codes
                    or type(reason) is not str or reason not in OUTPUT_FAILURE_REASONS
                    or (reason == "INCOMPLETE") != (record["errorCode"] == "QUALITY_REVIEW_INCOMPLETE")
                    or record["issueCount"] or record["issueCodes"]):
                return None
        elif "outputReason" in record or record["errorCode"] in parse_codes:
            return None
        if record["phaseReadCount"] + record["phaseWriteCount"] > record["toolCount"] or record["repeatedReadCount"] > record["phaseReadCount"]:
            return None
        codes = record["issueCodes"]
        if not isinstance(codes, list) or len(codes) > min(record["issueCount"], len(ISSUE_CODES)):
            return None
        if any(type(code) is not str or code not in ISSUE_CODES for code in codes) or len(set(codes)) != len(codes):
            return None
        if (record["issueCount"] > 0 and not codes) or (record["verdict"] == "PASS" and record["issueCount"]):
            return None
        if record["stage"] != "quality_review" and (record["issueCount"] or codes):
            return None
        result.append({**record, "issueCodes": sorted(codes)})
    return result
