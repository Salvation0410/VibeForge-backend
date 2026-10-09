"""区分既有图片保护要求与需要外部图片资源的修改请求。"""

import re


_IMAGE = r"(?:图片|照片|配图|实景图|背景图|\b(?:images?|photos?|pictures?)\b)"
_ACTION = r"(?:添加|新增|增加|补充|替换|更换|换成|搜索|搜图|获取|插入|修复|\b(?:add|insert|replace|search|find|fetch|fix)\b)"
_REQUEST = re.compile(rf"{_ACTION}.{{0,32}}?{_IMAGE}", re.IGNORECASE)
_NEGATION = re.compile(r"不要|不需要|无需|禁止|不得|别|\b(?:do not|don't|without|never|no need to)\b", re.IGNORECASE)


def requests_image_assets(prompt: str) -> bool:
    """仅对明确的新增、替换或修复图片操作自动搜图，普通图片布局优化不触发。"""
    # 按分句识别否定，不能因前句“不要替换”吞掉后句“但请添加”的明确要求。
    clauses = re.split(r"[。！？；，,;.!?\n]|但是|不过|但|\bbut\b", prompt, flags=re.IGNORECASE)
    for clause in clauses:
        for match in _REQUEST.finditer(clause):
            if not _NEGATION.search(clause[:match.start()]):
                return True
    return False
