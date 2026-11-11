"""构造 Vue 单 Reviewer 的证据输入，不修改生成和修复的持久化上下文。"""

from typing import Any

from ai_service.models.generation_brief import rewrite_context


def vue_review_context(context: dict[str, Any]) -> dict[str, Any]:
    """只保留审查需要的约束，过时工具结果不能与当前源码快照竞争。"""
    source = rewrite_context({**context, "prompt": context.get("prompt", "")}, "VUE_PROJECT")
    result = {key: context[key] for key in (
        "currentArtifact", "imageAssets", "editIntent", "generationPreparation", "validation", "build",
    ) if key in context}
    result.update(
        codeGenType="VUE_PROJECT", prompt=source["prompt"], initialPrompt=source["initialPrompt"],
        conversation=[{"role": "user", "content": text} for text in source["userHistory"]],
    )
    return result
