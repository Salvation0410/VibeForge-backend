"""质量诊断只保留可信枚举与数量，不持久化模型理由正文。"""

from typing import Any

ISSUE_CODES = frozenset({
    "REQUIREMENT_MISSING", "FUNCTIONAL_ERROR", "TECHNICAL_ERROR", "SCOPE_VIOLATION", "UNCLASSIFIED",
})


def review_diagnostics(passed: bool, issues=()) -> dict[str, Any]:
    """多 Reviewer 的任意代码只映射为未分类，不把自由文本提升为日志标签。"""
    return {
        "stage": "quality_review", "verdict": "PASS" if passed else "REPAIR",
        "issueCount": len(issues),
        "issueCodes": sorted({issue.code if issue.code in ISSUE_CODES else "UNCLASSIFIED" for issue in issues}),
    }


def safe_review_diagnostics(value: Any) -> dict[str, Any] | None:
    """读取状态和旁路前再次投影；旧 checkpoint 缺字段时不补造诊断。"""
    if not isinstance(value, dict) or set(value) != {"stage", "verdict", "issueCount", "issueCodes"}:
        return None
    count, codes = value["issueCount"], value["issueCodes"]
    if value["stage"] != "quality_review" or type(value["verdict"]) is not str or value["verdict"] not in {"PASS", "REPAIR"}:
        return None
    if type(count) is not int or not 0 <= count <= 12 or not isinstance(codes, list):
        return None
    if len(codes) > min(count, len(ISSUE_CODES)) or any(type(code) is not str or code not in ISSUE_CODES for code in codes):
        return None
    if len(set(codes)) != len(codes) or (count > 0 and not codes) or (value["verdict"] == "PASS" and count):
        return None
    return {"stage": "quality_review", "verdict": value["verdict"], "issueCount": count, "issueCodes": sorted(codes)}


def review_trace_metadata(value: Any) -> dict[str, Any]:
    diagnostic = safe_review_diagnostics(value)
    if diagnostic is None:
        return {}
    return {"review_verdict": diagnostic["verdict"], "review_issue_count": diagnostic["issueCount"],
            "review_issue_codes": ",".join(diagnostic["issueCodes"])}
