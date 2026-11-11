"""只用确定性内容相等判重，不把文本相似度当成语义或页面质量证据。"""

import hashlib
import json
from typing import Any


def candidate_fingerprint(artifact: str) -> str:
    return hashlib.sha256(artifact.encode("utf-8")).hexdigest()


def vue_snapshot_fingerprint(snapshot: dict[str, Any]) -> str | None:
    """仅比较网关明确完整的非空源码集合，截断或缺失信息时不作无进展判断。"""
    files = snapshot.get("files")
    if snapshot.get("truncated") is not False or not isinstance(files, list) or not files:
        return None
    count = len(files)
    if (type(snapshot.get("eligibleFileCount")) is not int
            or type(snapshot.get("includedFileCount")) is not int
            or type(snapshot.get("omittedFileCount")) is not int
            or snapshot["eligibleFileCount"] != count
            or snapshot["includedFileCount"] != count
            or snapshot["omittedFileCount"] != 0):
        return None
    contents = {}
    for item in files:
        if not isinstance(item, dict) or item.get("truncated") is not False:
            return None
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not path or not isinstance(content, str) or path in contents:
            return None
        contents[path] = content
    return candidate_fingerprint(json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def record_candidate(previous: list[str], fingerprint: str | None) -> list[str]:
    """历史受原修复上限约束；也拦截 A→B→A 循环，不额外尝试修复。"""
    if fingerprint is None:
        return list(previous)
    if fingerprint in previous:
        raise ValueError("REPAIR_NO_PROGRESS: candidate content already verified in this request")
    return [*previous, fingerprint]
