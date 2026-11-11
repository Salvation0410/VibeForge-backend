"""否定配图与保护既有图片是不同意图，不能仅凭否定词吞掉后续新增请求。"""

import pytest

from ai_service.orchestration.image_intent import declines_image_assets, requests_image_assets


@pytest.mark.parametrize("prompt", [
    "不要替换原有图片。", "不需要修改文字和图片。", "无需删除导航、背景和照片。",
    "不要移除外部图片。", "不需要改变配图。",
])
def test_preserving_images_is_not_a_global_decline(prompt):
    assert not declines_image_assets(prompt)


def test_later_explicit_image_request_still_overrides_decline():
    prompt = "不需要登录或外部图片，但请添加一张旅行照片。"
    assert declines_image_assets(prompt)
    assert requests_image_assets(prompt)
