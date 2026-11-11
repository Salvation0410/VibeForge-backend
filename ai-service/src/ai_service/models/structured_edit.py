"""可视化编辑的显式意图契约；只验证范围和原文一致性，不宣称已经定位真实 DOM。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_service.models.input_review import InputReviewError

LABELS = {"color": "文字颜色", "backgroundColor": "背景颜色", "fontSize": "字号", "textAlign": "对齐方式"}
SELECTOR_PATTERN = r"body(?: > [a-z][a-z0-9-]{0,30}:nth-of-type\([1-9][0-9]{0,3}\)){1,12}"


class StructuredEditIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True, frozen=True)
    version: Literal["1"]
    selector: str = Field(min_length=1, max_length=512)
    property: Literal["color", "backgroundColor", "fontSize", "textAlign"]
    value: str = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def check_scope(self):
        if re.fullmatch(SELECTOR_PATTERN, self.selector) is None:
            raise ValueError("STRUCTURED_EDIT_INVALID")
        if any(part.split(":", 1)[0] in {"html", "body", "script", "style", "link", "meta"}
               for part in self.selector.split(" > ")[1:]):
            raise ValueError("STRUCTURED_EDIT_INVALID")
        valid = (bool(re.fullmatch(r"#[0-9a-fA-F]{6}", self.value)) if self.property in {"color", "backgroundColor"}
                 else bool(re.fullmatch(r"(?:[1-9]|[1-8][0-9]|9[0-6])px", self.value)) if self.property == "fontSize"
                 else self.value in {"left", "center", "right"})
        if not valid:
            raise ValueError("STRUCTURED_EDIT_INVALID")
        return self

    def canonical_prompt(self) -> str:
        return (f"请只修改选中元素的{LABELS[self.property]}为“{self.value}”。\n"
                f"元素定位：{self.selector}\n"
                "保留原有功能、文字、图片和操作方式，其余内容不变。请先确认定位唯一，无法唯一定位时不要修改。")


def validated_edit_intent(payload, prompt: str) -> StructuredEditIntent:
    """工作流重验检查点/注入输入，不能只相信入口曾经检查过。"""
    try:
        intent = StructuredEditIntent.model_validate(payload)
    except ValueError:
        raise InputReviewError("STRUCTURED_EDIT_INVALID", "修改参数无效，请重新选择元素和属性。") from None
    if intent.canonical_prompt() != prompt:
        raise InputReviewError("STRUCTURED_EDIT_PROMPT_MISMATCH", "修改需求与所选属性不一致，请重新提交。")
    return intent


def require_edit_artifact(artifact: dict, branch: str) -> None:
    """Vue 的 exists 只代表目录；额外要求有界清单中至少可见一份页面源码。"""
    entries = artifact.get("entries", [])
    has_source = (branch != "VUE_PROJECT" or isinstance(entries, list) and any(
        isinstance(path, str) and path.endswith((".vue", ".html", ".js", ".ts", ".jsx", ".tsx"))
        for path in entries))
    if artifact.get("exists") is not True or not has_source:
        raise InputReviewError("STRUCTURED_EDIT_REQUIRES_ARTIFACT", "请先生成页面，再选择元素进行修改。")
