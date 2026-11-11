"""需求整理的固定诊断契约；任何自由文本都不能进入观测数据。"""

import math
from typing import Any

PREPARATION_STATUSES = frozenset({"ready", "fallback", "disabled", "failed", "cancelled", "skipped"})
PREPARATION_REASONS = frozenset({
    "NONE", "DISABLED", "TIMEOUT", "PROVIDER_ERROR", "INVALID_JSON", "INVALID_SCHEMA",
    "INCOMPLETE_RESPONSE", "INVALID_EVIDENCE", "BRIEF_TOO_LARGE", "BUDGET_EXCEEDED",
    "CANCELLED", "INPUT_ERROR", "SIMPLE_EDIT", "STRUCTURED_EDIT",
})
_MESSAGES = {
    "INVALID_JSON": "需求摘要 JSON 无效",
    "INVALID_SCHEMA": "需求摘要结构无效",
    "INCOMPLETE_RESPONSE": "需求摘要响应不完整",
    "INVALID_EVIDENCE": "需求摘要引用无效",
    "BRIEF_TOO_LARGE": "需求摘要超出字节上限",
}


class PreparationFailure(ValueError):
    """保持 ValueError 兼容，同时避免 Pydantic/供应商异常携带模型原文。"""

    def __init__(self, reason: str):
        self.reason = reason if reason in PREPARATION_REASONS else "PROVIDER_ERROR"
        super().__init__(_MESSAGES.get(self.reason, "需求整理失败"))


def trusted_skill_ids(skills: Any) -> list[str]:
    """只接受当前本地规则版本，避免 checkpoint 或元数据注入观测标签。"""
    from ai_service.prompts.frontend_skills import RULES

    if not isinstance(skills, list):
        return []
    allowed = {f"{key}@{rule.version}" for key, rule in RULES.items()}
    result = []
    for item in skills[:32]:
        if isinstance(item, dict):
            key, version = item.get("id"), item.get("version")
            item = f"{key}@{version}" if isinstance(key, str) and isinstance(version, str) else None
        if isinstance(item, str) and item in allowed and item not in result:
            result.append(item)
    return result[:8]


def safe_preparation_diagnostics(value: Any) -> dict[str, Any] | None:
    """完整验证固定字段后再投影，不复制未知键或嵌套内容。"""
    if not isinstance(value, dict):
        return None
    status, reason = value.get("rewriteStatus"), value.get("reason")
    duration, attempted, size = value.get("durationMs"), value.get("modelAttempted"), value.get("briefBytes")
    if not isinstance(status, str) or status not in PREPARATION_STATUSES:
        return None
    if not isinstance(reason, str) or reason not in PREPARATION_REASONS:
        return None
    if type(duration) not in (int, float) or duration < 0 or (isinstance(duration, float) and not math.isfinite(duration)):
        return None
    if type(attempted) is not bool or type(size) is not int or size < 0:
        return None
    return {"rewriteStatus": status, "reason": reason, "durationMs": duration,
            "modelAttempted": attempted, "briefBytes": size,
            "skillIds": trusted_skill_ids(value.get("skillIds"))}


def preparation_trace_metadata(value: Any) -> dict[str, Any]:
    safe = safe_preparation_diagnostics(value)
    if safe is None:
        return {}
    return {"preparation_status": safe["rewriteStatus"], "preparation_reason": safe["reason"],
            "preparation_duration_ms": safe["durationMs"], "preparation_attempted": safe["modelAttempted"],
            "preparation_brief_bytes": safe["briefBytes"],
            "preparation_skill_ids": bounded_skill_labels(safe["skillIds"])}


def bounded_skill_labels(skills: Any) -> str:
    """追踪标量有 128 字符上限，只保留完整的可信规则标识。"""
    result = ""
    for item in trusted_skill_ids(skills):
        candidate = f"{result},{item}" if result else item
        if len(candidate) <= 128:
            result = candidate
    return result
