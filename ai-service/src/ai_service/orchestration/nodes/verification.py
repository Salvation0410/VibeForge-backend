from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn
from ai_service.orchestration.multi_agent_review import run_multi_agent_review
from ai_service.orchestration.static_generation import invoke_static_model
from ai_service.models.generation_brief import rewrite_context
from ai_service.models.single_quality_review import SingleReviewResult, SingleReviewOutputError
from ai_service.models.review_diagnostics import review_diagnostics
from ai_service.models.verification_diagnostics import ERROR_CODES, MAX_VERIFICATION_RECORDS, safe_verification_history
from ai_service.orchestration.vue_budget import budget_counts
from ai_service.orchestration.vue_read_stall import read_repetition_stats
from ai_service.models.input_review import InputReviewError
from ai_service.models.vue_repair import snapshot_file_manifest
from ai_service.orchestration.repair_progress import candidate_fingerprint, record_candidate, vue_snapshot_fingerprint
from ai_service.orchestration.subgraphs.schemas import VerificationState
from ai_service.orchestration.cancellation import GenerationCancelled

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow

logger = logging.getLogger(__name__)


def _with_diagnostics(state, stage, update, passed, code="NONE", review=None, *, output_reason=None):
    """只将当前阶段固定统计追加到私有历史；反馈正文始终走原修复通道。"""
    count = state.get("repair_count", 0)
    prefix = (f"{state['request_id']}:vue-repair:{count}" if count else f"{state['request_id']}:vue-generate")
    history = state.get("context", {}).get("toolResults", [])
    counts = budget_counts(history, prefix)
    record = {
        "round": count, "stage": stage, "verdict": "PASS" if passed else "REPAIR",
        "errorCode": "NONE" if passed else code if type(code) is str and code in ERROR_CODES else "UNCLASSIFIED",
        "toolCount": state.get("tool_call_count", 0), "phaseReadCount": counts["phase_read_count"],
        "phaseWriteCount": counts["phase_write_count"], "repeatedReadCount": read_repetition_stats(history, prefix)["total"],
        "issueCount": review["issueCount"] if review else 0, "issueCodes": review["issueCodes"] if review else [],
    }
    if output_reason is not None:
        record.update(verdict="ERROR", outputReason=output_reason)
    records = safe_verification_history(state.get("verification_history")) or []
    # 节点恢复重放同一轮时替换同阶段记录，不无限追加；不宣称业务 exactly-once。
    records = [r for r in records if (r["round"], r["stage"]) != (count, stage)]
    records = safe_verification_history([*records, record][-MAX_VERIFICATION_RECORDS:])
    if records is None:
        return update
    try:
        logger.info("Generation verification request_id=%s round=%d stage=%s verdict=%s error_code=%s "
                "tool_count=%d phase_read_count=%d phase_write_count=%d repeated_read_count=%d issue_count=%d output_reason=%s",
                state["request_id"], count, stage, record["verdict"], record["errorCode"], record["toolCount"],
                record["phaseReadCount"], record["phaseWriteCount"], record["repeatedReadCount"], record["issueCount"],
                record.get("outputReason", "NONE"))
    except Exception:
        # 观测失败不能覆盖原业务结果或解析异常。
        pass
    return {**update, "verification_history": records}


def _vue_snapshot_artifact(snapshot: dict[str, Any]) -> str:
    """将 Vue 源码快照序列化为键顺序稳定、无多余空白的确定性 JSON。"""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _vue_snapshot_event_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    """仅暴露 Vue 快照统计元数据，避免源码进入工作流事件。"""
    return {
        "eligibleFileCount": snapshot["eligibleFileCount"],
        "includedFileCount": snapshot["includedFileCount"],
        "omittedFileCount": snapshot["omittedFileCount"],
        "truncated": snapshot["truncated"],
    }


def create_verification_nodes(
    workflow: GenerationWorkflow,
    emitter: EventEmitter,
    thread_id: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """绑定单次请求依赖，节点闭包不进入图状态或检查点。"""
    async def artifact_validation(state: VerificationState) -> dict[str, Any]:
        """先拒绝截断或被过滤的响应，再调用 Spring 执行产物硬校验。"""
        _raise_for_incomplete_model_turn(state.get("finish_reason"))
        workflow._raise_if_cancelled(thread_id)
        fingerprints = state.get("candidate_fingerprints", [])
        if state["code_gen_type"] != "VUE_PROJECT":
            fingerprints = record_candidate(fingerprints, candidate_fingerprint(state.get("artifact", "")))
        call_id = f"{state['request_id']}:artifact_validation:{state.get('repair_count', 0)}"
        result = await workflow._invoke_tool(
            emitter,
            "artifact_validation",
            "artifact_validate",
            {"codeGenType": state["code_gen_type"], "artifact": state.get("artifact", "")},
            state["app_id"],
            state["request_id"],
            call_id,
        )
        # 新候选不能沿用上轮模型诊断；硬校验失败的修复只使用本轮硬校验结果。
        return _with_diagnostics(state, "artifact_validation", {
            "validation": result, "repair_feedback": "", "quality_passed": False, "review_diagnostics": {},
            "candidate_fingerprints": fingerprints,
            "repair_issues": [], "repair_file_manifest": [],
        }, result.get("valid") is True, "ARTIFACT_VALIDATION_FAILED")

    async def project_build(state: WorkflowState) -> dict[str, Any]:
        call_id = f"{state['request_id']}:project_build:{state.get('repair_count', 0)}"
        result = await workflow._invoke_tool(
            emitter,
            "project_build",
            "project_build",
            {"codeGenType": state["code_gen_type"]},
            state["app_id"],
            state["request_id"],
            call_id,
        )
        return _with_diagnostics(state, "project_build", {"build": result}, result.get("built") is True,
                                 result.get("errorCode") or "VUE_BUILD_FAILED")

    async def quality_review(state: VerificationState) -> dict[str, Any]:
        """仅对已通过确定性硬校验的候选执行模型质量检查。"""
        if not state.get("validation", {}).get("valid", False):
            return {"quality_passed": False}
        review_artifact = state.get("artifact", "")
        repair_count = state.get("repair_count", 0)
        multi_agent_enabled = (
            state["code_gen_type"] == "VUE_PROJECT"
            and workflow.settings.multi_agent_review_enabled
        )
        uses_vue_snapshot = (
            state["code_gen_type"] == "VUE_PROJECT"
            and state.get("build", {}).get("built") is True
        )
        if uses_vue_snapshot:
            try:
                snapshot = await workflow._invoke_tool(
                    emitter,
                    "quality_review",
                    "vue_source_snapshot",
                    {"codeGenType": "VUE_PROJECT"},
                    state["app_id"],
                    state["request_id"],
                    f"{state['request_id']}:vue-source-snapshot:{repair_count}",
                    event_result=_vue_snapshot_event_result,
                )
            except GenerationCancelled:
                raise
            except Exception:
                if multi_agent_enabled:
                    raise RuntimeError(
                        "MULTI_AGENT_REVIEW_SNAPSHOT_ERROR: "
                        "Vue source snapshot is unavailable"
                    ) from None
                raise
            review_artifact = _vue_snapshot_artifact(snapshot)
        workflow._raise_if_cancelled(thread_id)
        fingerprints = state.get("candidate_fingerprints", [])
        if uses_vue_snapshot:
            fingerprints = record_candidate(fingerprints, vue_snapshot_fingerprint(snapshot))
        if multi_agent_enabled:
            review_context = {
                "prompt": state["prompt"],
                "codeGenType": "VUE_PROJECT",
                "validation": state.get("validation"),
                "build": state.get("build"),
            }
            if "generationPreparation" in state["context"]:
                # 关闭增强时保持原上下文契约；开启后保留准备结果与对应用户原文。
                source = rewrite_context(state["context"], "VUE_PROJECT")
                review_context["generationPreparation"] = state["context"]["generationPreparation"]
                review_context["conversation"] = [
                    {"role": "user", "content": text} for text in source["userHistory"]
                ]
                # 摘要可能引用初始需求，审查仍须看见对应原文，不能只依赖模型摘录。
                review_context["initialPrompt"] = source["initialPrompt"]
            workflow._raise_if_cancelled(thread_id)
            result = await run_multi_agent_review(
                workflow.model,
                review_artifact,
                review_context,
                timeout_seconds=workflow.settings.multi_agent_review_timeout_seconds,
            )
            workflow._raise_if_cancelled(thread_id)
            diagnostic = review_diagnostics(result.passed, result.blocking_issues)
            return _with_diagnostics(state, "quality_review", {
                "quality_passed": result.passed,
                "repair_feedback": result.repair_feedback,
                "review_diagnostics": diagnostic,
                "candidate_fingerprints": fingerprints,
                "repair_issues": [{key: getattr(issue, key) for key in ("code", "summary", "evidence", "repair_hint")}
                                  for issue in result.blocking_issues],
                "repair_file_manifest": snapshot_file_manifest(snapshot) if uses_vue_snapshot else [],
            }, result.passed, "QUALITY_REVIEW_FAILED", diagnostic)
        review_context = {
            **state["context"],
            "codeGenType": state["code_gen_type"],
            "validation": state.get("validation"),
            "build": state.get("build"),
        }
        try:
            result = await workflow.model.review(review_artifact, review_context)
            workflow._raise_if_cancelled(thread_id)
            if isinstance(result, SingleReviewResult):
                passed, feedback = result.passed, result.repair_feedback
            elif type(result) is bool:
                # 仅兼容旧注入模型；真实适配器仍要求严格 JSON。
                passed, feedback = result, ""
            else:
                raise SingleReviewOutputError()
        except SingleReviewOutputError as exc:
            # 异常节点不会进入 complete_node，仅最佳努力写入既有状态摘要后原样失败。
            update = _with_diagnostics(state, "quality_review", {
                "quality_passed": False, "repair_feedback": "", "review_diagnostics": {},
            }, False, str(exc), output_reason=exc.output_reason)
            workflow._raise_if_cancelled(thread_id)
            try:
                await workflow.checkpoint.save(thread_id, workflow._checkpoint_payload(
                    {**state, **update}, "quality_review"))
            except GenerationCancelled:
                raise
            except Exception:
                try:
                    logger.warning("Review failure diagnostic save reason=CHECKPOINT_WRITE_FAILED")
                except Exception:
                    pass
            workflow._raise_if_cancelled(thread_id)
            raise
        except (InputReviewError, GenerationCancelled):
            raise
        except Exception:
            if uses_vue_snapshot:
                # 供应商异常可能携带快照正文，不能通过公共失败帧回显。
                raise RuntimeError("Vue source snapshot review failed") from None
            raise
        diagnostic = review_diagnostics(passed, result.issues if isinstance(result, SingleReviewResult) else ())
        return _with_diagnostics(state, "quality_review", {
            "quality_passed": passed, "repair_feedback": feedback, "review_diagnostics": diagnostic,
            "candidate_fingerprints": fingerprints,
            "repair_issues": [issue.model_dump() for issue in result.issues] if isinstance(result, SingleReviewResult) else [],
            "repair_file_manifest": snapshot_file_manifest(snapshot) if uses_vue_snapshot else [],
        }, passed, "QUALITY_REVIEW_FAILED", diagnostic)

    async def repair(state: WorkflowState) -> dict[str, Any]:
        """生成完整修复候选并更新结束元数据，供下一轮重新校验。"""
        count = state.get("repair_count", 0) + 1
        repair_context = {
            **state["context"],
            "codeGenType": state["code_gen_type"],
            "repairCount": count,
            "validation": state.get("validation"),
            "build": state.get("build"),
        }
        if state.get("repair_feedback"):
            repair_context["qualityReview"] = {
                "repairFeedback": state["repair_feedback"]
            }
        turn = await invoke_static_model(workflow, state, emitter, thread_id, repair_context=repair_context)
        return {
            "artifact": turn.content,
            "build": {},
            "repair_count": count,
            "finish_reason": turn.finish_reason,
            "token_usage": turn.token_usage,
        }

    async def fail_quality(state: WorkflowState) -> dict[str, Any]:
        """在硬校验或质量检查耗尽修复次数后生成明确失败终态。"""
        count = state.get("repair_count", 0)
        budget = workflow.settings.max_repair_attempts
        if not state.get("validation", {}).get("valid", False):
            raise ValueError(f"ARTIFACT_VALIDATION_FAILED: 产物校验未通过，自动修复次数已用完（{count}/{budget}）")
        build = state.get("build", {})
        if state.get("code_gen_type") == "VUE_PROJECT" and build.get("built") is not True:
            code = build.get("errorCode") or "VUE_BUILD_FAILED"
            message = build.get("message") or "Vue project build failed"
            raise ValueError(f"{code}: {message}")
        # 质量拒绝不能打印已经通过的硬校验列表，也不能向公共失败帧回显模型理由。
        prefix = "构建已通过，" if state.get("code_gen_type") == "VUE_PROJECT" else "产物校验已通过，"
        raise ValueError(f"QUALITY_REVIEW_FAILED: {prefix}质量检查未通过，自动修复次数已用完（{count}/{budget}）")

    return {
        "artifact_validation": artifact_validation,
        "project_build": project_build,
        "quality_review": quality_review,
        "repair": repair,
        "fail_quality": fail_quality,
    }
