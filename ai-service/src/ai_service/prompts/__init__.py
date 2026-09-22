"""模型系统提示词，按动作和生成类型集中维护。"""

from ai_service.prompts.generation import generation_system_prompt
from ai_service.prompts.review import REPAIR_SYSTEM_PROMPT, QUALITY_REVIEW_SYSTEM_PROMPT
from ai_service.prompts.routing import ROUTING_SYSTEM_PROMPT

__all__ = [
    "REPAIR_SYSTEM_PROMPT",
    "QUALITY_REVIEW_SYSTEM_PROMPT",
    "ROUTING_SYSTEM_PROMPT",
    "generation_system_prompt",
]
