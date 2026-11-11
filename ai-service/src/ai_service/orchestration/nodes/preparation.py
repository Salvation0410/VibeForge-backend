"""一次性准备辅助需求与前端规则，通过既有 context 进入窄子图契约。"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from time import monotonic

from ai_service.models.generation_brief import rewrite_context, validate_brief
from ai_service.models.input_review import InputReviewError
from ai_service.models.rewrite_policy import should_skip_rewrite
from ai_service.models.structured_edit import validated_edit_intent, require_edit_artifact
from ai_service.models.preparation_diagnostics import (
    PreparationFailure, preparation_trace_metadata, safe_preparation_diagnostics, trusted_skill_ids,
)
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.prompts.frontend_skills import RULES, resolve_skills

logger = logging.getLogger(__name__)


async def _observe(workflow, diagnostic, started_at):
    """旁路失败不能覆盖生成终态；日志和追踪都仅接收经过白名单投影的内容。"""
    safe = safe_preparation_diagnostics(diagnostic)
    if safe is None:
        return
    try:
        logger.info("preparation_diagnostics %s", json.dumps(safe, ensure_ascii=True, separators=(",", ":")))
    except (Exception, asyncio.CancelledError):
        pass
    tracer = getattr(workflow, "tracer", None)
    if tracer is not None:
        try:
            await tracer.record(name="generation_preparation_diagnostics",
                                metadata=preparation_trace_metadata(safe),
                                status=safe["rewriteStatus"], started_at=started_at)
        except (Exception, asyncio.CancelledError):
            pass


def create_preparation_node(workflow, emitter, thread_id):
    async def prepare(state, diagnostic):
        context = dict(state["context"])
        explicit_edit = state.get("edit_intent")
        if explicit_edit is not None:
            validated_edit_intent(explicit_edit, context["prompt"])
            require_edit_artifact(context.get("currentArtifact", {}), state["code_gen_type"])
        # 同一检查点再次进入节点时复用已解析结果，避免重复模型调用。
        if "generationPreparation" in context:
            if diagnostic is not None:
                cached = safe_preparation_diagnostics(state.get("preparation_diagnostics"))
                if cached is not None:
                    diagnostic.update(cached)
                    diagnostic["modelAttempted"] = False
                else:
                    # 老检查点没有诊断时，只根据既有准备结果重建安全状态，不重复重写。
                    prepared = context["generationPreparation"]
                    status = prepared.get("rewriteStatus") if isinstance(prepared, dict) else None
                    if status in {"ready", "fallback", "disabled", "skipped"}:
                        diagnostic.update(rewriteStatus=status, reason={
                            "ready": "NONE", "fallback": "PROVIDER_ERROR", "disabled": "DISABLED",
                            "skipped": "STRUCTURED_EDIT" if explicit_edit is not None else "SIMPLE_EDIT"}[status],
                            skillIds=trusted_skill_ids(prepared.get("skills")))
            return {"context": context}
        settings = workflow.settings
        if not settings.frontend_skills_enabled and not settings.prompt_rewrite_enabled:
            return {"context": context}
        source = rewrite_context(context, state["code_gen_type"])
        prepared = {"version": "1", "mode": source["mode"], "skills": [], "brief": None,
                    "rewriteStatus": "disabled"}
        if settings.frontend_skills_enabled:
            prepared["skills"] = resolve_skills(state["code_gen_type"], source["mode"], state["prompt"])
            if explicit_edit is not None:
                # 仅已重验的顶层意图激活范围规则，不能由自由文本或 metadata 冒充。
                prepared["skills"].append({"id": "structured-edit-scope",
                                           "version": RULES["structured-edit-scope"].version})
        if diagnostic is not None:
            diagnostic["skillIds"] = trusted_skill_ids(prepared["skills"])
        source["selectedSkills"] = prepared["skills"]
        structured_skip = explicit_edit is not None and getattr(settings, "structured_edit_enabled", True)
        skip_rewrite = settings.prompt_rewrite_enabled and (structured_skip or (
                        getattr(settings, "prompt_rewrite_policy", "always") == "conservative"
                        and should_skip_rewrite(context)))
        if skip_rewrite:
            # 跳过仅省去辅助摘要，不删除原文、保留规则，也不跳过后续生成和校验。
            prepared["rewriteStatus"] = "skipped"
            if diagnostic is not None:
                diagnostic.update(rewriteStatus="skipped", reason="STRUCTURED_EDIT" if structured_skip else "SIMPLE_EDIT")
        await emitter.emit("node_status", "generation_prepare", data={
            "status": "progress", "message": "正在整理需求与页面设计要点。" if settings.prompt_rewrite_enabled and not skip_rewrite
            else "正在准备页面设计与实现要点。"})
        if settings.prompt_rewrite_enabled and not skip_rewrite:
            workflow._raise_if_cancelled(thread_id)
            try:
                if diagnostic is not None:
                    diagnostic["modelAttempted"] = True
                async with asyncio.timeout(settings.prompt_rewrite_timeout_seconds):
                    brief = await workflow.model.rewrite_requirements(source)
                workflow._raise_if_cancelled(thread_id)
                prepared["brief"] = validate_brief(brief, source, settings.prompt_rewrite_max_bytes).model_dump()
                prepared["rewriteStatus"] = "ready"
                if diagnostic is not None:
                    diagnostic.update(rewriteStatus="ready", reason="NONE",
                                      briefBytes=len(json.dumps(prepared["brief"], ensure_ascii=False,
                                                               separators=(",", ":")).encode("utf-8")))
            except (GenerationCancelled, asyncio.CancelledError, InputReviewError):
                raise
            except Exception as exc:
                # 不记录供应商异常或模型输出，避免需求与源码进入日志或事件。
                workflow._raise_if_cancelled(thread_id)
                prepared["rewriteStatus"] = "fallback"
                if diagnostic is not None:
                    reason = exc.reason if isinstance(exc, PreparationFailure) else (
                        "TIMEOUT" if isinstance(exc, TimeoutError) else "PROVIDER_ERROR")
                    diagnostic.update(rewriteStatus="fallback", reason=reason)
                await emitter.emit("node_status", "generation_prepare", data={
                    "status": "progress", "message": "需求整理暂不可用，将按原始需求继续生成。"})
        workflow._raise_if_cancelled(thread_id)
        context["generationPreparation"] = prepared
        return {"context": context}

    async def generation_prepare(state):
        if not workflow.settings.preparation_diagnostics_enabled:
            return await prepare(state, None)
        started, started_at = monotonic(), datetime.now(UTC)
        diagnostic = {"rewriteStatus": "disabled", "reason": "DISABLED", "durationMs": 0.0,
                      "modelAttempted": False, "briefBytes": 0, "skillIds": []}
        try:
            update = await prepare(state, diagnostic)
            update["preparation_diagnostics"] = diagnostic
            return update
        except (GenerationCancelled, asyncio.CancelledError):
            diagnostic.update(rewriteStatus="cancelled", reason="CANCELLED")
            raise
        except InputReviewError as exc:
            # 当前审核错误只来自供应商调用前的硬预算/字符检查，不代表已发送请求。
            diagnostic.update(rewriteStatus="failed", reason=("BUDGET_EXCEEDED"
                              if exc.code == "INPUT_CONTEXT_BUDGET_EXCEEDED" else "INPUT_ERROR"),
                              modelAttempted=False)
            raise
        except Exception:
            diagnostic.update(rewriteStatus="failed", reason="INPUT_ERROR")
            raise
        finally:
            diagnostic["durationMs"] = max(0.0, (monotonic() - started) * 1000)
            try:
                await _observe(workflow, diagnostic, started_at)
            except (Exception, asyncio.CancelledError):
                # 连投影/序列化本身失败时也不能遮蔽原始业务终态。
                pass
    return generation_prepare
