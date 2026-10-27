"""父子图共享的请求状态，不保存运行时依赖对象。"""

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    app_id: str
    request_id: str
    thread_id: str
    prompt: str
    code_gen_type: str
    conversation: list[dict[str, Any]]
    metadata: dict[str, Any]
    context: dict[str, Any]
    artifact: str
    validation: dict[str, Any]
    build: dict[str, Any]
    quality_passed: bool
    repair_feedback: str
    repair_count: int
    tool_call_count: int
    finish_reason: str | None
    token_usage: dict[str, int]
    publish: dict[str, Any]
    input_review: dict[str, Any]
