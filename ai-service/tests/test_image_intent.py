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


@pytest.mark.parametrize('action', ['增加', '添加', '新增'])
def test_direct_negative_image_addition_does_not_request_assets(action):
    prompt = f'保留原有筛选和计数，不{action}外部图片。'
    assert not requests_image_assets(prompt)
    assert declines_image_assets(prompt)
    assert requests_image_assets(prompt + '但请添加一张旅行照片。')


def test_partial_negative_word_does_not_suppress_positive_request():
    assert requests_image_assets('不但添加图片，还要保留筛选功能。')
    assert requests_image_assets('不增加文字，但请新增图片。')
    assert not declines_image_assets('不要替换原有图片。')
