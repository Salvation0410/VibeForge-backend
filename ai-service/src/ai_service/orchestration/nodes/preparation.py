"""一次性准备辅助需求与前端规则，通过既有 context 进入窄子图契约。"""

import asyncio

from ai_service.models.generation_brief import rewrite_context, validate_brief
from ai_service.models.input_review import InputReviewError
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.prompts.frontend_skills import resolve_skills


def create_preparation_node(workflow, emitter, thread_id):
    async def generation_prepare(state):
        context = dict(state["context"])
        # 同一检查点再次进入节点时复用已解析结果，避免重复模型调用。
        if "generationPreparation" in context:
            return {"context": context}
        settings = workflow.settings
        if not settings.frontend_skills_enabled and not settings.prompt_rewrite_enabled:
            return {"context": context}
        source = rewrite_context(context, state["code_gen_type"])
        prepared = {"version": "1", "mode": source["mode"], "skills": [], "brief": None,
                    "rewriteStatus": "disabled"}
        if settings.frontend_skills_enabled:
            prepared["skills"] = resolve_skills(state["code_gen_type"], source["mode"], state["prompt"])
        source["selectedSkills"] = prepared["skills"]
        await emitter.emit("node_status", "generation_prepare", data={
            "status": "progress", "message": "正在整理需求与页面设计要点。" if settings.prompt_rewrite_enabled
            else "正在准备页面设计与实现要点。"})
        if settings.prompt_rewrite_enabled:
            workflow._raise_if_cancelled(thread_id)
            try:
                async with asyncio.timeout(settings.prompt_rewrite_timeout_seconds):
                    brief = await workflow.model.rewrite_requirements(source)
                workflow._raise_if_cancelled(thread_id)
                prepared["brief"] = validate_brief(brief, source, settings.prompt_rewrite_max_bytes).model_dump()
                prepared["rewriteStatus"] = "ready"
            except (GenerationCancelled, asyncio.CancelledError, InputReviewError):
                raise
            except Exception:
                # 不记录供应商异常或模型输出，避免需求与源码进入日志或事件。
                workflow._raise_if_cancelled(thread_id)
                prepared["rewriteStatus"] = "fallback"
                await emitter.emit("node_status", "generation_prepare", data={
                    "status": "progress", "message": "需求整理暂不可用，将按原始需求继续生成。"})
        workflow._raise_if_cancelled(thread_id)
        context["generationPreparation"] = prepared
        return {"context": context}
    return generation_prepare
