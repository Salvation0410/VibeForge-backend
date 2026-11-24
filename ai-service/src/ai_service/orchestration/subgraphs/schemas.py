"""阶段数据契约；不继承完整请求状态，防止无关字段进入嵌套检查点。"""

from typing import Any, TypedDict


class GenerationInput(TypedDict, total=False):
    """生成及追踪需要的最小输入；模型所需会话信息已包含在 context。"""

    app_id: str
    request_id: str
    code_gen_type: str
    context: dict[str, Any]
    repair_count: int
    tool_call_count: int


class StaticGenerationOutput(TypedDict, total=False):
    """静态生成仅负责候选和模型结束元数据，不回写上下文或预算。"""

    artifact: str
    finish_reason: str | None
    token_usage: dict[str, int]


class GenerationOutput(StaticGenerationOutput, total=False):
    """Vue 工具执行额外更新上下文和跨阶段工具预算。"""

    context: dict[str, Any]
    tool_call_count: int


class StaticGenerationState(GenerationInput, StaticGenerationOutput):
    """静态生成的内部通道，排除请求生命周期和验证阶段字段。"""


class VerificationInput(GenerationInput, StaticGenerationOutput, total=False):
    """验证从新候选开始，不接收上一阶段的校验结论。"""

    prompt: str


class VerificationOutput(GenerationOutput, total=False):
    """验证子图回传质量结论及修复产生的更新。"""

    validation: dict[str, Any]
    build: dict[str, Any]
    quality_passed: bool
    repair_feedback: str
    repair_count: int


class VerificationState(VerificationInput, VerificationOutput, total=False):
    """校验、构建、质量结论和反馈仅在验证阶段内部产生。"""

    # 仅在此子图持久化，禁止投影到父图、修复输入或模型上下文。
    candidate_fingerprints: list[str]
    # 仅留在验证子图，摘要经白名单投影；不进入父图或修复模型输入。
    review_diagnostics: dict[str, Any]
    # 逐轮固定字段历史仅留验证子图，避免进入修复模型或父状态。
    verification_history: list[dict[str, Any]]
    repair_issues: list[dict[str, str]]
    repair_file_manifest: list[str]


class RepairInput(GenerationInput, total=False):
    """修复所需的候选与诊断，不接收已失败的质量布尔结论。"""

    artifact: str
    validation: dict[str, Any]
    build: dict[str, Any]
    repair_feedback: str
    # 私有诊断仅供异常摘要续写，禁止加入模型上下文或修复输出。
    verification_history: list[dict[str, Any]]
    # 严格Reviewer问题与当前网关快照路径，仅供修复组织，不回传父图。
    repair_issues: list[dict[str, str]]
    repair_file_manifest: list[str]


class RepairOutput(GenerationOutput, total=False):
    """修复只更新候选/上下文、预算及反馈，不覆盖原验证与质量结论。"""

    build: dict[str, Any]
    repair_count: int
    repair_feedback: str


class RepairState(RepairInput, RepairOutput):
    """修复分派层只保存修复输入与允许回传的字段。"""
