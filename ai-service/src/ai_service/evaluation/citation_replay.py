"""受控历史上下文回放：只比较生成提示，不连接检索、数据库或 GPU。"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import unicodedata
from typing import Any

from ai_service.prompts.customer_service import customer_service_user_prompt


# 固定历史输入的字节指纹防止把修改后的快照误称为同一历史实验。
HISTORICAL_HASHES = {
    "candidate": "e4ac0f414d70a5f81845e1ddbb5b8df6174b13454964e25e43c8bcd3302afd2c",
    "baseline": "657f7504a0078f0ce3941e312102b445d7d8339bbf8c028b89c9ae4c3e8c415d",
    "evidence": "637affdd8e4db6c128d36496903cb529d4a0d092fd81131f5d05e98b12925078",
    "trace": "ac604cbfe318ebd10894679166d540b5721efc60a956f6d2a2cd189ae81a243b",
    "dataset": "2aa97999d3187964472732596c74d593a2b6e5bdbe1cb4663a930c57abcb87d8",
    "annotations": "3617f42c3f2ba2bbad6b8744c2ca4c493a9a149985032ed0ce59cb9f831b9f8c",
    "mapping": "88ff16bf9b2de8290b72cf5bf333d30a9fb5b586b54d5bfdbe127a6ab9d0d09f",
}


class Unreplayable(ValueError):
    """证据不完整或与历史实际输入不一致。"""


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def clean(value: str) -> str:
    visible = "".join(" " if unicodedata.category(c).startswith("C") else c for c in value)
    return re.sub(r"\s+", " ", visible).strip()


@dataclass(frozen=True)
class Context:
    chunk_id: str
    content: str
    document_id: str
    document_version: int
    source_sha256: str
    content_sha256: str


def restore_contexts(trace: dict, evidence: list[dict], question: str) -> list[Context]:
    """按历史前缀裁剪规则恢复正文，任一长度/身份不匹配即拒绝回放。"""
    chunks = {c["chunkId"]: c for c in evidence}
    if len(chunks) != len(evidence):
        raise Unreplayable("duplicate evidence chunk")
    character = trace.get("characterBudget")
    byte = trace.get("byteBudget")
    if not isinstance(character, list) or not isinstance(byte, list):
        raise Unreplayable("missing budget records")
    char_rows = {r["chunkId"]: r for r in character if r.get("kept")}
    rows = [r for r in byte if r.get("kept")]
    if len({r["chunkId"] for r in rows}) != len(rows):
        raise Unreplayable("duplicate final chunk")
    if [r["chunkId"] for r in rows] != [r["chunkId"] for r in character if r.get("kept")]:
        raise Unreplayable("context order or membership mismatch")
    contexts = []
    for row in rows:
        key = row["chunkId"]
        if key not in chunks or key not in char_rows:
            raise Unreplayable("missing chunk or character record")
        chunk, chars = chunks[key], char_rows[key]
        raw = chunk["content"]
        normalized = clean(raw)
        # beforeChars 是原始块长度；归一化发生在字符预算阶段。
        if len(raw) != chars["beforeChars"]:
            raise Unreplayable("source length mismatch")
        content = normalized[:chars["afterChars"]]
        if len(content) != chars["afterChars"] or len(content) != row["beforeChars"]:
            raise Unreplayable("character budget mismatch")
        if len(content.encode("utf-8")) != row["beforeBytes"]:
            raise Unreplayable("before byte length mismatch")
        content = content[:row["afterChars"]]
        if not content or len(content.encode("utf-8")) != row["afterBytes"]:
            raise Unreplayable("final byte length mismatch")
        if any(row[k] != chunk[k] or chars[k] != chunk[k]
               for k in ("documentId", "documentVersion")):
            raise Unreplayable("document identity mismatch")
        # 文件整体先按冻结 hash 核验；这里再保存原块与实际裁剪块的独立 hash。
        contexts.append(Context(key, content, chunk["documentId"], chunk["documentVersion"],
                                digest(raw), digest(content)))
    if trace.get("model", {}).get("called") is not True or not contexts:
        raise Unreplayable("historical model not called or empty contexts")
    payload = customer_service_user_prompt(question.strip(), contexts)
    if len(payload.encode("utf-8")) > trace["configFingerprint"]["promptMaxBytes"]:
        raise Unreplayable("restored prompt exceeds historical budget")
    return contexts


def strict_v1(entry: dict, answered: bool, document_ids: list[str], mapping: dict,
              *, error: bool = False) -> bool:
    if error:
        return False
    if not entry["expectedAnswerable"]:
        return answered is False and not document_ids
    allowed = {mapping[x]["documentId"] for x in entry["expectedDocumentIds"]}
    return (answered is True and bool(document_ids) and
            len(document_ids) == len(set(document_ids)) and set(document_ids) <= allowed)


def validate_answer(raw: str, contexts: list[Context]) -> dict:
    """契约错误不能变成 answered=false；未知来源和重复来源保持可审查。"""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    data = json.loads(raw, object_pairs_hook=unique_pairs)
    if not isinstance(data, dict) or set(data) != {"answered", "answer", "citedChunkIds"}:
        raise ValueError("invalid response keys")
    answered, answer, citations = data["answered"], data["answer"], data["citedChunkIds"]
    if type(answered) is not bool or not isinstance(answer, str) or not isinstance(citations, list):
        raise ValueError("invalid response types")
    if len(answer) > 4000:
        raise ValueError("answer exceeds production limit")
    allowed = {c.chunk_id for c in contexts}
    if any(not isinstance(c, str) or c not in allowed for c in citations):
        raise ValueError("unknown citation")
    if len(citations) != len(set(citations)):
        raise ValueError("duplicate citation")
    if answered and (not answer.strip() or not 1 <= len(citations) <= 3):
        raise ValueError("invalid answered response")
    if not answered and (answer != "" or citations):
        raise ValueError("invalid refusal response")
    return data


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def prepare(paths: dict[str, Path], *, scope: str = "diagnostic") -> dict:
    if scope not in {"diagnostic", "full", "remaining"}:
        raise ValueError("unsupported scope")
    fingerprints = {k: digest(p.read_bytes()) for k, p in paths.items()}
    for key, expected in HISTORICAL_HASHES.items():
        if fingerprints.get(key) != expected:
            raise Unreplayable(f"frozen input hash mismatch: {key}")
    dataset, annotations = _load(paths["dataset"]), _load(paths["annotations"])
    candidate, baseline = _load(paths["candidate"]), _load(paths["baseline"])
    evidence, mapping = _load(paths["evidence"]), _load(paths["mapping"])["documents"]
    traces = [json.loads(line) for line in paths["trace"].read_text(encoding="utf-8-sig").splitlines()]
    by_trace = {t["requestId"]: t for t in traces}
    by_candidate = {c["id"]: c for c in candidate["cases"]}
    by_baseline = {c["id"]: c for c in baseline["cases"]}
    by_annotation = {a["id"]: a for a in annotations["entries"]}
    changed, safety = [], []
    for entry in dataset["entries"]:
        scores = []
        for history in (by_baseline[entry["id"]], by_candidate[entry["id"]]):
            scores.append(strict_v1(entry, history["answered"],
                [s["documentId"] for s in history["sources"]], mapping,
                error=bool(history.get("error") or history.get("diagnosticErrors"))))
        if scores[0] != scores[1]:
            changed.append(entry["id"])
        if not entry["expectedAnswerable"]:
            safety.append(entry["id"])
    if len(changed) != 14 or len(safety) != 9 or len(set(changed + safety)) != 23:
        raise Unreplayable("historical diagnostic membership mismatch")
    diagnostic = set(changed + safety)
    cases, failures = [], []
    for entry in dataset["entries"]:
        if scope == "diagnostic" and entry["id"] not in diagnostic:
            continue
        if scope == "remaining" and entry["id"] in diagnostic:
            continue
        try:
            trace = by_trace[by_candidate[entry["id"]]["requestId"]]
            contexts = restore_contexts(trace, evidence, entry["question"])
            for context in contexts:
                logical = next((x for x in mapping.values()
                                if x["documentId"] == context.document_id), None)
                chunk = next(c for c in evidence if c["chunkId"] == context.chunk_id)
                if logical and (logical["sha256"] != chunk["documentContentHash"] or
                                logical["documentVersion"] != context.document_version):
                    raise Unreplayable("document snapshot hash mismatch")
            annotation = by_annotation[entry["id"]]
            if list(dict.fromkeys(c.document_id for c in contexts)) != by_candidate[entry["id"]]["finalContextDocumentIds"]:
                raise Unreplayable("candidate final document order mismatch")
            user = customer_service_user_prompt(entry["question"].strip(), contexts)
            cases.append({**entry, "category": annotation["category"],
                          "questionType": annotation["questionType"],
                          "expectedActualDocumentIds": [mapping[x]["documentId"] for x in entry["expectedDocumentIds"]],
                          "referenceAnnotation": annotation,
                          "contexts": [asdict(c) for c in contexts],
                          "userPrompt": user, "userPromptSha256": digest(user)})
        except (KeyError, ValueError, StopIteration) as exc:
            failures.append({"caseId": entry["id"], "status": "unreplayable",
                             "reason": str(exc)})
    return {"schemaVersion": "citation-replay/v1", "scope": scope,
            "historicalOnly": True, "currentEndToEndValidated": False,
            "snapshotNote": "历史81块12文档含旧手册；不代表当前活动知识状态或端到端成绩",
            "inputFingerprints": fingerprints, "historicalConfig": candidate["configFingerprint"],
            "historicalSourceCommit": candidate["sourceCommit"],
            "changedCaseIds": changed, "safetyCaseIds": safety,
            "cases": cases, "unreplayable": failures,
            "plannedCalls": len(cases) * 6, "semanticReview": "人工待审"}


def schedule(cases: list[dict]):
    # 同题配对相邻；轮次和题序翻转，避免 B0 总是先于 E1。
    for repeat in range(1, 4):
        ordered = cases if repeat % 2 else list(reversed(cases))
        for index, case in enumerate(ordered):
            for variant in (("B0", "E1") if (index + repeat) % 2 else ("E1", "B0")):
                yield repeat, variant, case


def _append(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def normalize_usage(value: Any) -> dict | None:
    """缺失/非法字段保留 null；缓存token单列且不二次累加到total。"""
    if not isinstance(value, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    result = {k: value[k] if type(value.get(k)) is int and value[k] >= 0 else None for k in keys}
    details = value.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    result["cached_tokens"] = cached if type(cached) is int and cached >= 0 else None
    return result


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        for dimension, value in (("overall", "all"), ("category", row["category"]),
                                 ("questionType", row["questionType"]),
                                 ("repeat", str(row["repeatId"]))):
            groups[(row["variant"], dimension, value)].append(row)
    slices = []
    for (variant, dimension, value), items in sorted(groups.items()):
        latency = sorted(x["latencyMs"] for x in items)
        known_usage = [x["usage"]["total_tokens"] for x in items
                       if isinstance(x.get("usage"), dict) and
                       type(x["usage"].get("total_tokens")) is int and
                       x["usage"]["total_tokens"] >= 0]
        slices.append({"variant": variant, "dimension": dimension, "value": value,
                       "samples": len(items), "strictPassed": sum(x["strictPassed"] for x in items),
                       "answered": sum(x.get("answered") is True for x in items),
                       "safetyCorrect": sum(not x["expectedAnswerable"] and x["strictPassed"] for x in items),
                       "errors": sum(x["status"] != "ok" for x in items),
                       "latencyMeanMs": sum(latency) / len(latency),
                       "latencyP95Ms": latency[math.ceil(len(latency) * .95) - 1],
                       "knownTotalTokens": sum(known_usage) if known_usage else None,
                       "usageMissing": len(items) - len(known_usage), "cost": None})
    pairs = defaultdict(dict)
    for row in rows:
        pairs[(row["caseId"], row["repeatId"])][row["variant"]] = row
    changes = [{"caseId": case, "repeatId": repeat,
                "B0": values.get("B0", {}).get("strictPassed"),
                "E1": values.get("E1", {}).get("strictPassed"),
                "gain": values.get("B0", {}).get("strictPassed") is False and values.get("E1", {}).get("strictPassed") is True,
                "loss": values.get("B0", {}).get("strictPassed") is True and values.get("E1", {}).get("strictPassed") is False,
                "B0Answered": values.get("B0", {}).get("answered"),
                "E1Answered": values.get("E1", {}).get("answered"),
                "newRefusal": values.get("B0", {}).get("answered") is True and values.get("E1", {}).get("answered") is False,
                "pairedComparable": len(values) == 2 and all(v["status"] == "ok" for v in values.values())}
               for (case, repeat), values in pairs.items()]
    return {"slices": slices, "pairedCases": changes, "semanticReview": "人工待审",
            "automaticPromotionApproved": False,
            "cost": None, "costReason": "未配置经核验的供应商计价；禁止由缺失usage倒推费用"}


async def execute(plan: dict, output: Path, *, model: str, base_url: str, api_key: str,
                  prompts: dict[str, str], max_calls: int, token_budget: int,
                  max_tokens: int = 8192, temperature: float = .1, top_p: float = 1.0,
                  timeout: float = 120, client=None) -> list[dict]:
    if plan["unreplayable"]:
        raise Unreplayable("incomplete replay; no model calls permitted")
    if max_calls < 1 or max_calls > 540 or token_budget < 1 or max_tokens < 1:
        raise ValueError("invalid call/token budget")
    rows, reserved = [], 0
    stop_reason = "interrupted"

    def persist_summary(reason: str | None) -> None:
        report = summarize(rows)
        planned = len(plan["cases"]) * 6
        report.update({"plannedCalls": planned, "completedCalls": len(rows),
                       "missingCalls": planned - len(rows), "reservedTokens": reserved,
                       "complete": len(rows) == planned, "stopReason": reason})
        (output / "summary.json").write_text(canonical(report), encoding="utf-8")

    owned = client is None
    if owned:
        import httpx
        client = httpx.AsyncClient(timeout=timeout, transport=httpx.AsyncHTTPTransport(retries=0))
    try:
        for repeat, variant, case in schedule(plan["cases"]):
            system = prompts[variant]
            # UTF-8字节数作为输入token保守上界，额外预留消息封装预算；不冒充供应商usage。
            allowance = len((system + case["userPrompt"]).encode("utf-8")) + max_tokens + 256
            if len(rows) >= max_calls or reserved + allowance > token_budget:
                stop_reason = "max_calls" if len(rows) >= max_calls else "token_budget"
                _append(output / "events.jsonl", {"status": "budget_stopped", "calls": len(rows),
                                                  "reservedTokens": reserved, "nextAllowance": allowance,
                                                  "stopReason": stop_reason})
                break
            reserved += allowance
            request_id = f"{output.name}:{case['id']}:{repeat}:{variant}"
            request = {"model": model, "temperature": temperature, "top_p": top_p,
                       "max_tokens": max_tokens, "stream": False,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": case["userPrompt"]}]}
            fingerprint = digest(canonical(request))
            _append(output / "events.jsonl", {"status": "started", "requestId": request_id,
                                              "requestSha256": fingerprint, "reservedTokens": reserved})
            row = {"caseId": case["id"], "requestId": request_id, "repeatId": repeat,
                   "variant": variant, "category": case["category"], "questionType": case["questionType"],
                   "expectedAnswerable": case["expectedAnswerable"], "requestSha256": fingerprint,
                   "systemPromptSha256": digest(system), "userPromptSha256": case["userPromptSha256"],
                   "status": "error", "strictPassed": False, "answered": None,
                   "usage": None, "cost": None, "retryCount": 0, "reservedTokens": allowance}
            started = time.perf_counter()
            try:
                response = await client.post(base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"}, json=request)
                row["httpStatus"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                row["providerResponseId"] = payload.get("id")
                row["providerModel"] = payload.get("model")
                row["systemFingerprint"] = payload.get("system_fingerprint")
                row["usage"] = normalize_usage(payload.get("usage"))
                choice = payload["choices"][0]
                if len(payload["choices"]) != 1 or choice["message"].get("tool_calls") or choice["message"].get("function_call"):
                    raise ValueError("unexpected choices or tool calls")
                row["rawAnswer"] = choice["message"]["content"]
                row["finishReason"] = choice.get("finish_reason")
                if choice.get("finish_reason") != "stop":
                    raise ValueError("non-stop finish reason")
                contexts = [Context(**c) for c in case["contexts"]]
                answer = validate_answer(row["rawAnswer"], contexts)
                by_id = {c.chunk_id: c for c in contexts}
                docs = [by_id[c].document_id for c in answer["citedChunkIds"]]
                # 评分使用评测器侧映射；请求 messages 从未包含标注、ID或允许集。
                actual = {c.document_id for c in contexts
                          if c.document_id in case.get("expectedActualDocumentIds", [])}
                row.update(answer)
                row["citationDocumentIds"] = docs
                row["strictPassed"] = ((answer["answered"] and bool(docs) and
                    len(docs) == len(set(docs)) and set(docs) <= actual)
                    if case["expectedAnswerable"] else not answer["answered"] and not docs)
                row["status"] = "ok"
            except Exception as exc:
                # 不持久化异常正文，HTTP异常可能携带内部URL或供应商敏感响应。
                row["errorType"] = type(exc).__name__
                if isinstance(exc, ValueError) and not hasattr(exc, "request"):
                    row["validationReason"] = str(exc)[:160]
            row["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
            _append(output / "results.jsonl", row)
            rows.append(row)
            persist_summary(None)
        else:
            stop_reason = "completed"
    finally:
        # 零调用预算停止也必须留下完整终态，缺失样本不能被误认为已完成。
        persist_summary(stop_reason)
        if owned:
            await client.aclose()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in HISTORICAL_HASHES:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", choices=("diagnostic", "remaining", "full"), default="diagnostic")
    parser.add_argument("--execute", action="store_true", help="显式允许真实API调用；默认只准备")
    parser.add_argument("--max-calls", type=int, default=138)
    parser.add_argument("--token-budget", type=int, default=2_000_000)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    from ai_service.prompts.customer_service import (
        CUSTOMER_SERVICE_SYSTEM_PROMPT, CUSTOMER_SERVICE_FOCUSED_SYSTEM_PROMPT)
    plan = prepare({name: getattr(args, name) for name in HISTORICAL_HASHES}, scope=args.scope)
    mapping = _load(args.mapping)["documents"]
    for case in plan["cases"]:
        case["expectedActualDocumentIds"] = [mapping[x]["documentId"] for x in case["expectedDocumentIds"]]
    prompts = {"B0": CUSTOMER_SERVICE_SYSTEM_PROMPT, "E1": CUSTOMER_SERVICE_FOCUSED_SYSTEM_PROMPT}
    settings = None
    if args.execute:
        from ai_service.config import Settings
        settings = Settings(_env_file=str(args.env_file) if args.env_file else None)
        plan["modelParameters"] = {"model": settings.model_name,
            "baseUrlSha256": digest(settings.model_base_url),
            "temperature": settings.model_temperature, "topP": 1.0,
            "seed": None, "maxTokens": args.max_tokens,
            "providerVersion": None, "retries": 0}
    plan.update({"createdAt": datetime.now(timezone.utc).isoformat(), "prompts": prompts,
                 "promptFingerprints": {k: digest(v) for k, v in prompts.items()},
                 "budget": {"maxCalls": args.max_calls, "tokenBudget": args.token_budget,
                            "maxTokensPerCall": args.max_tokens}})
    root = Path(__file__).resolve().parents[4]
    def git(*parts):
        return subprocess.run(["git", "-C", str(root), *parts], capture_output=True, check=True).stdout
    plan["sourceCommit"] = git("rev-parse", "HEAD").decode().strip()
    plan["workingTreeDiffSha256"] = digest(git("diff", "HEAD"))
    plan["runnerSha256"] = digest(Path(__file__).read_bytes())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "inputs.json").write_text(canonical(plan), encoding="utf-8")
    print(canonical({"plannedCalls": plan["plannedCalls"], "unreplayable": plan["unreplayable"],
                     "output": str(args.output), "execute": args.execute}), flush=True)
    if plan["unreplayable"]:
        raise SystemExit(2)
    if args.execute:
        rows = asyncio.run(execute(plan, args.output, model=settings.model_name,
            base_url=settings.model_base_url, api_key=settings.model_api_key, prompts=prompts,
            max_calls=args.max_calls, token_budget=args.token_budget, max_tokens=args.max_tokens,
            temperature=settings.model_temperature))
        print(canonical({"completedCalls": len(rows), "plannedCalls": plan["plannedCalls"]}), flush=True)


if __name__ == "__main__":
    main()
