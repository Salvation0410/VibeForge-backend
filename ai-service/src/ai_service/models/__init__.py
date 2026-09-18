"""模型调用协议与供应商适配实现。"""

from ai_service.models.base import GenerationModel, ModelTurn, ToolCall
from ai_service.models.openai_compatible import OpenAICompatibleModel

__all__ = ["GenerationModel", "ModelTurn", "OpenAICompatibleModel", "ToolCall"]
