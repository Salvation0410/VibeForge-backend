"""将可信检查器的有界结构化证据转成私有修复问题，不解析自由错误文本。"""

import re

from ai_service.models.vue_repair import safe_repair_path, snapshot_file_manifest


def valid_component_issues(build):
    """旧网关无证据时保持原路径；非法或其他类型错误不提升成工作集任务。"""
    if build.get("built") is not False or build.get("errorCode") != "VUE_COMPONENT_CONTRACT_FAILED":
        return False
    issues = build.get("componentIssues")
    return (isinstance(issues, list) and 1 <= len(issues) <= 4
            and all(isinstance(issue, dict) and set(issue) == {"file", "component", "prop"}
                    and safe_repair_path(issue["file"]) and len(issue["file"]) <= 180
                    and issue["file"].endswith(".vue")
                    and all(isinstance(issue[key], str)
                            and re.fullmatch(r"[$A-Za-z_][\w$-]{0,79}", issue[key], re.ASCII)
                            for key in ("component", "prop")) for issue in issues))


def component_repair_issues(build, snapshot):
    """仅用当前完整有界快照形成路径白名单；不会把快照源码整体送给修复模型。"""
    if not valid_component_issues(build):
        return [], []
    manifest = snapshot_file_manifest(snapshot)
    if (not manifest or snapshot.get("truncated") is not False
            or type(snapshot.get("omittedFileCount")) is not int or snapshot["omittedFileCount"] != 0
            or type(snapshot.get("includedFileCount")) is not int or snapshot["includedFileCount"] != len(manifest)
            or type(snapshot.get("eligibleFileCount")) is not int or snapshot["eligibleFileCount"] != len(manifest)
            or any(not isinstance(item.get("content"), str) or item.get("truncated") is not False
                   for item in snapshot["files"])
            or any(issue["file"] not in manifest for issue in build["componentIssues"])):
        return [], []
    issues = []
    for item in build["componentIssues"]:
        issues.append({
            "code": "TECHNICAL_ERROR",
            "summary": f"{item['component']} 缺少必填属性 {item['prop']}",
            "evidence": f"可信必填属性检查：{item['file']} -> {item['component']}.{item['prop']}",
            "repair_hint": "读取该调用方和直接子组件，按实际声明补齐属性并核对对象字段；沿用现有功能和数据，不删除必填声明规避检查。",
        })
    return issues, manifest
