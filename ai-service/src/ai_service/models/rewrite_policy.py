"""保守的需求整理门控；未完整识别的请求一律继续重写，不改变原始需求。"""

import re
from typing import Any

POLICY_VERSION = "2"

_COLOR = r"(?:红色|绿色|蓝色|黑色|白色|灰色|橙色|紫色|red|green|blue|black|white|gray|orange|purple|\#[0-9a-f]{6}|\#[0-9a-f]{3})"
_SIZE = r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,2})? *(?:px|rem|em|%)"
_QUOTED = r'''(?:“[^“”‘’"\r\n<>]{1,40}”|‘[^“”‘’"\r\n<>]{1,40}’|"[^“”‘’"\r\n<>]{1,40}")'''
# 目标允许业务限定词、页面位置和引号标签；后续仍须通过名词/指代/复合操作检查。
_TARGET = r'''(?P<target>[\w ‘’“”"#.-]{1,80}?)'''
_ZH_PREFIX = rf"(?:请)?(?:只)?(?:把|将)?{_TARGET}"
_ZH_CHANGE = r"(?:改为|改成|设置为|设为|替换为) *"
_EN_PREFIX = r"(?:please )?(?:only )?(?:change|set) (?:the )?"
_ZH_COLOR_PROP = r"(?:背景色|背景颜色|文字颜色|边框颜色|颜色)"
_ZH_SIZE_PROP = r"(?:字号|字体大小|圆角|宽度|高度|(?:上|下|左|右)?(?:内边距|外边距))"
_EN_COLOR_PROP = r"(?:color|text color|background color|border color)"
_EN_SIZE_PROP = r"(?:font size|border radius|width|height|(?:top |bottom |left |right )?(?:padding|margin))"
_COMMANDS = (
    rf"{_ZH_PREFIX}(?:的)?{_ZH_COLOR_PROP}?{_ZH_CHANGE}{_COLOR}",
    rf"{_ZH_PREFIX}(?:的)?{_ZH_SIZE_PROP}{_ZH_CHANGE}{_SIZE}",
    rf"{_ZH_PREFIX}(?:的(?:文字|文案|文本)从|上的|中的){_QUOTED}{_ZH_CHANGE}{_QUOTED}",
    rf"{_EN_PREFIX}{_TARGET} {_EN_COLOR_PROP} to {_COLOR}",
    rf"{_EN_PREFIX}{_TARGET} {_EN_SIZE_PROP} to {_SIZE}",
    rf"{_EN_PREFIX}{_EN_COLOR_PROP} of (?:the )?{_TARGET} to {_COLOR}",
    rf"{_EN_PREFIX}{_EN_SIZE_PROP} of (?:the )?{_TARGET} to {_SIZE}",
    rf"{_EN_PREFIX}{_TARGET} (?:text|label) from {_QUOTED} to {_QUOTED}",
    rf"(?:please )?in (?:the )?{_TARGET}, replace the text {_QUOTED} with {_QUOTED}",
    rf"(?:please )?replace the text {_QUOTED} with {_QUOTED} in (?:the )?{_TARGET}",
)
_PRESERVE = (
    r"保留文字、图片和其他操作|保留原有功能、文字、图片和操作方式|"
    r"保留图片|其他保持不变|其余不变|不要修改其他内容|keep everything else unchanged"
)
_SUFFIX = rf"(?:[，,；;。] ?(?:{_PRESERVE})){{0,2}}[。.!]?"
_PATTERNS = tuple(re.compile(command + _SUFFIX, re.IGNORECASE) for command in _COMMANDS)
_ELEMENT = re.compile(
    r"(?:按钮|标题|页脚|导航栏|卡片|标志|图片|输入框|文本|标签|链接|段落|容器|页眉)$|"
    r"\b(?:button|heading|title|subtitle|footer|header|navigation|card|logo|image|input|label|link|paragraph|container)\b",
    re.IGNORECASE,
)
_ZH_ELEMENTS = re.compile(r"按钮|标题|页脚|导航栏|卡片|标志|图片|输入框|文本|标签|链接|段落|容器|页眉")
# 对未加引号的目标采用词组白名单；不能靠不断扩充排除词来判断任意自然语言目标。
_ZH_CORE = (
    r"(?:命名(?:按钮|标题|卡片|图片|输入框|标签|链接)|"
    r"(?:预约|提交|登录|注册|取消|搜索|返回|保存|确认|关闭|导出|下载|订阅)按钮|"
    r"(?:主|副)?标题|页脚|页眉|导航栏|品牌标志|金额汇总卡片|卡片|图片|输入框|链接|段落|容器)"
)
_ZH_SCOPE = r"(?:首页|页面|个人资料页|账户页|账号页|设置页|订单页|订单详情页|发票详情页|活动页|联系页|邮件订阅区|页头|页尾)"
_ZH_NAMED_TARGET = re.compile(rf"(?:{_ZH_SCOPE}(?:的|里|上|里的|上的|左侧|右侧)?)*{_ZH_CORE}")
_EN_TARGET_WORDS = frozenset((
    "the named booking submit login signup cancel search save confirm close export download subscribe "
    "primary secondary main sub hero section title subtitle heading button footer header navigation bar "
    "card logo image input label link paragraph container homepage home page contact account settings "
    "profile order invoice detail details summary featured workshop events pricing on in of left right"
).split())


def _explicit_target(target: str) -> bool:
    # 引号内是用户明确给出的名称，不当作目标外的新增操作或指代。
    plain = re.sub(_QUOTED, "命名" if re.search(r"[\u4e00-\u9fff]", re.sub(_QUOTED, "", target)) else "named", target)
    if re.search(r"[\u4e00-\u9fff]", plain):
        return _ZH_NAMED_TARGET.fullmatch(plain) is not None
    return bool(plain.split()) and all(word in _EN_TARGET_WORDS for word in plain.lower().split())


_UNCLEAR_OR_COMPOUND = re.compile(
    r"不|别|禁止|无需|某个|它|这个|那个|这些|那些|之前|上次|上述|所有|全|各|每|整个|整站|以及|同时|并|"
    r"新增|增加|添加|删除|去掉|修改|改成|设置|替换|隐藏|显示|开启|关闭|禁用|启用|移除|移动|调整|重置|清空|和|或|再|后|"
    r"多[个张处]|[一二三四五六七八九十两\d]+(?:个|张|处|组)|"
    r"\b(?:it|this|that|these|those|same|previous|earlier|all|every|each|and|or|then|"
    r"global|shared|universal|multiple|both|several|across|throughout|site[- ]?wide|"
    r"add|remove|delete|disable|enable|create|change|set|replace|update|hide|show|move|reset|clear)\b",
    re.IGNORECASE,
)


def should_skip_rewrite(context: dict[str, Any]) -> bool:
    """仅返回建议；配置、审核、取消、后续模型预算仍由既有工作流负责。"""
    artifact = context.get("currentArtifact")
    if not isinstance(artifact, dict) or artifact.get("exists") is not True:
        return False
    prompt = context.get("prompt")
    if not isinstance(prompt, str) or len(prompt) > 240 or "\n" in prompt or "\r" in prompt:
        return False
    # fullmatch 防止只命中第一项样式修改，却忽略后面的付款、鉴权或数据变更。
    for pattern in _PATTERNS:
        match = pattern.fullmatch(prompt.strip())
        if match:
            target = match["target"].strip()
            # 多元素中文目标可能夹带另一项操作；宁可漏识别，也不省略其需求整理。
            if (_explicit_target(target) and _ELEMENT.search(target) and len(_ZH_ELEMENTS.findall(target)) <= 1
                    and not _UNCLEAR_OR_COMPOUND.search(target)):
                return True
    return False
