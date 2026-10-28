"""模型工作上下文必须有限，且不能修改持久化历史或伪造完整文件。"""

from copy import deepcopy

import pytest

from ai_service.models.input_review import InputReviewError
from ai_service.models.vue_context import project_vue_context


def record(index, content, path=None, tool="file_read"):
    return {"toolCallId": str(index), "tool": tool,
            "arguments": {"relativeFilePath": path or f"src/{index}.vue"},
            "result": {"content": content} if tool == "file_read" else {"ok": True}}


def test_projection_keeps_recent_whole_files_and_original_history():
    context = {"prompt": "保留全部功能", "toolResults": [record(i, "中" * 3000) for i in range(8)]}
    original = deepcopy(context)
    result = project_vue_context(context, fits=lambda _: True)
    assert context == original
    assert result["prompt"] == context["prompt"]
    assert len(result["toolResults"]) == 8
    assert result["toolResults"][-1] == context["toolResults"][-1]
    assert result["toolResults"][0]["result"]["contextOmitted"] is True
    assert "content" not in result["toolResults"][0]["result"]


def test_old_read_is_invalidated_by_successful_write_but_not_failed_write():
    for success in (True, False):
        write = record(2, "", path="src/App.vue", tool="file_modify")
        write["result"]["ok"] = success
        result = project_vue_context({"toolResults": [record(1, "old", path="src/App.vue"), write]}, fits=lambda _: True)
        first = result["toolResults"][0]["result"]
        assert first.get("contextOmitted", False) is success
        if success:
            assert first["reason"] == "superseded"
        else:
            assert first["content"] == "old"


def test_actual_message_budget_controls_selection():
    result = project_vue_context({"toolResults": [record(i, "x" * 500) for i in range(5)]},
                                 fits=lambda ctx: sum("content" in t["result"] for t in ctx["toolResults"]) <= 1)
    assert result["toolResults"][-1]["result"]["content"] == "x" * 500
    assert all(t["result"].get("contextOmitted") for t in result["toolResults"][:-1])


def test_latest_result_cannot_be_silently_truncated():
    with pytest.raises(InputReviewError, match="VUE_CONTEXT_BUDGET_EXCEEDED"):
        project_vue_context({"toolResults": [record(1, "中" * 20000)]}, fits=lambda _: True)


def test_summary_overflow_fails_without_claiming_user_prompt_is_long():
    with pytest.raises(InputReviewError) as error:
        project_vue_context({"toolResults": [record(1, "small")]}, fits=lambda _: False)
    assert error.value.code == "VUE_CONTEXT_BUDGET_EXCEEDED"
    assert "工具" in error.value.user_message
