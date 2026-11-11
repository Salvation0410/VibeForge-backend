"""区分既有图片保护要求与需要外部图片资源的修改请求。"""

import re


_IMAGE = r"(?:图片|照片|配图|实景图|背景图|\b(?:images?|photos?|pictures?)\b)"
_ACTION = r"(?:添加|新增|增加|补充|替换|更换|换成|搜索|搜图|获取|插入|修复|\b(?:add|insert|replace|search|find|fetch|fix)\b)"
_REQUEST = re.compile(rf"{_ACTION}.{{0,32}}?{_IMAGE}", re.IGNORECASE)
_NEGATION = re.compile(r"不要|不需要|无需|禁止|不得|别|\b(?:do not|don't|without|never|no need to)\b", re.IGNORECASE)
_PRESERVE_ACTION = re.compile(r"替换|更换|修改|改变|调整|删除|移除|隐藏|裁剪|压缩|保留")


def declines_image_assets(prompt: str) -> bool:
    """识别不配图要求，不把“不要替换原有图片”当作禁止首次配图。"""
    # 否定可作用于并列需求（如“不需要登录、后端或外部图片”），但不能越过分句。
    for match in re.finditer(rf"(?:无需|不需要|不要)([^。！？；，,;.!?\n]{{0,80}}?){_IMAGE}", prompt, re.IGNORECASE):
        scope = match.group(1)
        if _PRESERVE_ACTION.search(scope):
            continue
        image_prefix = re.split(r"、|或者|以及|或|和|及", scope)[-1]
        if re.fullmatch(r"(?:任何|使用|添加|新增|外部|网络|在线|的|\s)*", image_prefix):
            return True
    return bool(re.search(
        rf"\b(?:without|no)\s+(?:(?:any|external|online)\s+)*{_IMAGE}", prompt, re.IGNORECASE,
    ))


def requests_image_assets(prompt: str) -> bool:
    """仅对明确的新增、替换或修复图片操作自动搜图，普通图片布局优化不触发。"""
    # 按分句识别否定，不能因前句“不要替换”吞掉后句“但请添加”的明确要求。
    clauses = re.split(r"[。！？；，,;.!?\n]|但是|不过|但|\bbut\b", prompt, flags=re.IGNORECASE)
    for clause in clauses:
        for match in _REQUEST.finditer(clause):
            if not _NEGATION.search(clause[:match.start()]):
                return True
    return False
