from __future__ import annotations

import pytest

from ai_service.prompts import (
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    generation_system_prompt,
    quality_review_system_prompt,
)
from ai_service.models.quality_review import ReviewerRole


def test_routing_prompt_preserves_supported_generation_types_and_strict_output():
    assert "HTML" in ROUTING_SYSTEM_PROMPT
    assert "MULTI_FILE" in ROUTING_SYSTEM_PROMPT
    assert "VUE_PROJECT" in ROUTING_SYSTEM_PROMPT
    assert "只能返回一个大写类型标识" in ROUTING_SYSTEM_PROMPT


def test_html_prompt_requires_a_complete_single_document():
    prompt = generation_system_prompt("HTML")

    assert "<!DOCTYPE html>" in prompt
    assert "只能输出一个完整的 html Markdown 代码块" in prompt
    assert "完整页面" in prompt
    assert "保留其他功能、文字、图片和操作方式" in prompt
    assert "currentArtifact.exists" in prompt


def test_multi_file_prompt_requires_the_three_complete_files():
    prompt = generation_system_prompt("MULTI_FILE")

    for filename in ("index.html", "style.css", "script.js"):
        assert filename in prompt
    assert "顺序固定" in prompt
    assert "只能输出三个 Markdown 代码块" in prompt
    assert "currentArtifact.artifact" in prompt


def test_vue_prompt_uses_python_tool_call_contract():
    prompt = generation_system_prompt("VUE_PROJECT")

    assert "toolCalls" in prompt
    assert "Spring 工具" in prompt
    assert "appId 或 codeGenType" in prompt
    assert "currentArtifact.entries" in prompt


def test_review_and_repair_prompts_define_the_python_workflow_contract():
    assert "PASS" in QUALITY_REVIEW_SYSTEM_PROMPT
    assert "REPAIR" in QUALITY_REVIEW_SYSTEM_PROMPT
    assert "完整的修复后产物" in REPAIR_SYSTEM_PROMPT
    assert "校验、构建和质量检查结果" in REPAIR_SYSTEM_PROMPT
    assert "补丁" in REPAIR_SYSTEM_PROMPT


def test_role_review_prompts_define_distinct_responsibilities_and_shared_contract():
    prompts = {
        role: quality_review_system_prompt(role.value)
        for role in ReviewerRole
    }

    assert "需求符合度" in prompts[ReviewerRole.REQUIREMENT]
    assert "功能正确性" in prompts[ReviewerRole.FUNCTION]
    assert "技术质量" in prompts[ReviewerRole.TECHNICAL]
    assert len(set(prompts.values())) == 3

    for role, prompt in prompts.items():
        assert f'"reviewer":"{role.value}"' in prompt
        assert "用户需求、构建摘要和源码快照" in prompt
        assert "不得调用工具" in prompt
        assert "不得建议无关重构" in prompt
        assert "critical" in prompt
        assert "major" in prompt
        assert "minor" in prompt
        assert "最多 5 条" in prompt
        assert "只返回 JSON" in prompt
        assert "Markdown" in prompt
        assert '"summary"' in prompt
        assert '"issues"' in prompt


def test_unknown_review_role_is_rejected():
    with pytest.raises(ValueError, match="Unsupported reviewer role"):
        quality_review_system_prompt("security")


def test_unknown_generation_branch_is_rejected():
    with pytest.raises(ValueError, match="Unsupported generation branch"):
        generation_system_prompt("UNKNOWN")
