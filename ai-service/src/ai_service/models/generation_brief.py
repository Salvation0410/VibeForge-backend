"""有界需求摘要契约；原文证据检查不等于语义一致性证明。"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

ShortText = Annotated[str, Field(min_length=1, max_length=400)]


class GroundedDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    description: ShortText
    evidence: ShortText


class GenerationBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    requirements: list[ShortText] = Field(min_length=1, max_length=12)
    preserve_constraints: list[ShortText] = Field(max_length=12)
    design_brief: str = Field(max_length=800)
    assumptions: list[ShortText] = Field(max_length=5)
    interactions: list[GroundedDetail] = Field(max_length=8)
    acceptance: list[GroundedDetail] = Field(max_length=8)


def _utf16_prefix(text: str, units: int) -> str:
    """与 Spring UTF-16 上限一致，且不把补充平面字符截成孤立代理项。"""
    return text.encode("utf-16-le", errors="surrogatepass")[:units * 2].decode("utf-16-le", errors="ignore")


def rewrite_context(context: dict[str, Any], branch: str) -> dict[str, Any]:
    """仅传需求和有限用户历史，不传源码、元数据或工具结果。"""
    users = [_utf16_prefix(item["content"], 1000) for item in context.get("conversation", [])
             if item.get("role") == "user" and isinstance(item.get("content"), str)][-6:]
    initial = context.get("metadata", {}).get("initialPrompt", "")
    return {"prompt": context["prompt"], "userHistory": users, "codeGenType": branch,
            "initialPrompt": _utf16_prefix(initial, 2000) if isinstance(initial, str) else "",
            "mode": "edit" if context.get("currentArtifact", {}).get("exists") is True else "create"}


def validate_brief(brief: GenerationBrief, context: dict[str, Any], max_bytes: int) -> GenerationBrief:
    """核对引用来源和总字节上限，不声称能判断所有遗漏或冲突。"""
    brief = GenerationBrief.model_validate(brief.model_dump())
    sources = [context["prompt"], context.get("initialPrompt", ""), *context["userHistory"]]
    evidence = [*brief.requirements, *brief.preserve_constraints,
                *(item.evidence for item in [*brief.interactions, *brief.acceptance])]
    if any(not value.strip() or not any(value in source for source in sources) for value in evidence):
        raise ValueError("需求摘要引用无效")
    if len(brief.model_dump_json().encode("utf-8")) > max_bytes:
        raise ValueError("需求摘要超出字节上限")
    return brief
