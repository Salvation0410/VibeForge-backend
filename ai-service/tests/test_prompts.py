from __future__ import annotations

import pytest

from ai_service.prompts import (
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    generation_system_prompt,
)


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


def test_multi_file_prompt_requires_the_three_complete_files():
    prompt = generation_system_prompt("MULTI_FILE")

    for filename in ("index.html", "style.css", "script.js"):
        assert filename in prompt
    assert "顺序固定" in prompt
    assert "只能输出三个 Markdown 代码块" in prompt


def test_vue_prompt_uses_python_tool_call_contract():
    prompt = generation_system_prompt("VUE_PROJECT")

    assert "toolCalls" in prompt
    assert "Spring 工具" in prompt
    assert "appId 或 codeGenType" in prompt


def test_review_and_repair_prompts_define_the_python_workflow_contract():
    assert "PASS" in QUALITY_REVIEW_SYSTEM_PROMPT
    assert "REPAIR" in QUALITY_REVIEW_SYSTEM_PROMPT
    assert "完整的修复后产物" in REPAIR_SYSTEM_PROMPT
    assert "校验、构建和质量检查结果" in REPAIR_SYSTEM_PROMPT
    assert "补丁" in REPAIR_SYSTEM_PROMPT


def test_unknown_generation_branch_is_rejected():
    with pytest.raises(ValueError, match="Unsupported generation branch"):
        generation_system_prompt("UNKNOWN")
