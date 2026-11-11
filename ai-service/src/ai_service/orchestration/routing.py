"""确定性路由规则，生成类型和修复预算在父子图之间保持一致。"""

from ai_service.orchestration.state import WorkflowState


def _raise_for_incomplete_model_turn(finish_reason: str | None) -> None:
    """在模型因长度或内容策略中止时阻止候选进入校验和发布。"""
    normalized = (finish_reason or "").upper()
    if normalized in {"LENGTH", "MAX_TOKENS"}:
        raise ValueError("MODEL_OUTPUT_TRUNCATED: model output reached its length limit")
    if normalized in {"CONTENT_FILTER", "CONTENT_FILTERED"}:
        raise ValueError("MODEL_OUTPUT_BLOCKED: model output was filtered")


def _after_validation(state: WorkflowState, max_attempts: int) -> str:
    """根据硬校验结果选择构建、质量检查、修复或失败节点。"""
    if not state.get("validation", {}).get("valid", False):
        return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"
    return "build" if state["code_gen_type"] == "VUE_PROJECT" else "review"


def _after_build(state: WorkflowState, max_attempts: int) -> str:
    """构建成功后才能质量审查，否则修复并重建，耗尽次数后失败。"""
    if state.get("build", {}).get("built") is True:
        return "review"
    # 缺少入口或检查器环境不可用时直接返回稳定错误，不能让模型反复修复环境。
    if state.get("build", {}).get("errorCode") in {
        "VUE_PROJECT_DIR_MISSING",
        "VUE_PACKAGE_JSON_MISSING",
        "VUE_COMPONENT_CHECK_UNAVAILABLE",
    }:
        return "fail"
    return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"


def _after_review(state: WorkflowState, max_attempts: int) -> str:
    """质量通过后将 HTML 和多文件交给 Spring 原子发布，Vue 保持原构建终态。"""
    if state.get("quality_passed", False):
        return "publish" if state["code_gen_type"] in {"HTML", "MULTI_FILE"} else "finalize"
    return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"
