from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState
from ai_service.orchestration.image_intent import requests_image_assets

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow

logger = logging.getLogger(__name__)


def create_input_nodes(
    workflow: GenerationWorkflow,
    emitter: EventEmitter,
    thread_id: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """绑定单次请求依赖，节点闭包不进入图状态或检查点。"""
    async def input_guard(state: WorkflowState) -> dict[str, Any]:
        result = await workflow.input_reviewer.review(
            state["prompt"], request_id=state["request_id"], code_gen_type=state["code_gen_type"],
            conversation=state.get("conversation", []), metadata=state.get("metadata", {}),
        )
        workflow._raise_if_cancelled(thread_id)
        if result.decision == "ALLOW_WITH_WARNING":
            await emitter.emit("node_status", "input_guard", data={
                "status": "input_warning", "message": result.message,
            })
        return {"input_review": result.model_dump()}

    async def context_prepare(state: WorkflowState) -> dict[str, Any]:
        current_artifact = await workflow._invoke_tool(
            emitter,
            "context_prepare",
            "artifact_context",
            {"codeGenType": state["code_gen_type"]},
            state["app_id"],
            state["request_id"],
            f"{state['request_id']}:artifact_context",
        )
        # 首次生成主动配图；既有项目仅对明确配图操作检索，不能把“保留图片”误判为新增。
        image_assets = []
        needs_images = state["code_gen_type"] == "VUE_PROJECT" and (
            not current_artifact.get("exists", False)
            or requests_image_assets(state["prompt"])
        )
        if needs_images:
            query = state["prompt"].strip()[:160]
            # 常见中文主题转换成 Pexels 更容易匹配的短查询，其他需求保留原始关键词。
            for words, english in (("恋爱 情侣 日记 纪念", "couple love"), ("旅游 旅行 风景", "travel landscape"),
                                   ("美食 餐厅 咖啡", "food cafe"), ("商城 商品 购物", "product lifestyle")):
                if any(word in query for word in words.split()):
                    query = english
                    break
            try:
                images = await workflow._invoke_tool(
                    emitter, "context_prepare", "image_search",
                    {"codeGenType": state["code_gen_type"], "query": query},
                    state["app_id"], state["request_id"], f"{state['request_id']}:image_search",
                )
                image_assets = images.get("images", []) if images.get("ok") else []
            except Exception:
                logger.warning("图片检索降级 request_id=%s", state["request_id"])
            if not image_assets:
                await emitter.emit("node_status", "context_prepare", data={
                    "status": "progress", "message": "图片搜索暂未返回可用结果，请检查 Pexels 密钥或网络；继续生成页面。",
                })
        return {
            "context": {
                "appId": state["app_id"],
                "requestId": state["request_id"],
                "prompt": state["prompt"],
                "conversation": state.get("conversation", []),
                "metadata": state.get("metadata", {}),
                "currentArtifact": current_artifact,
                "imageAssets": image_assets,
            }
        }

    return {
        "input_guard": input_guard,
        "context_prepare": context_prepare,
    }
