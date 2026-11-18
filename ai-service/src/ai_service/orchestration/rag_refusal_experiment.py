"""固定真实历史上下文的拒答配对诊断；默认离线，真实调用必须显式 --live。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import fmean
import time

from ai_service.orchestration.rag_evaluation_workflow import ROOT, fingerprint
from ai_service.orchestration.rag_snapshot_experiment import CallBudget, error_code, safe_output
from ai_service.prompts.customer_service import (
    CUSTOMER_SERVICE_REFUSAL_CANDIDATE_SYSTEM_PROMPT,
    CUSTOMER_SERVICE_REFUSAL_SYSTEM_PROMPT,
    CUSTOMER_SERVICE_SYSTEM_PROMPT,
    customer_service_user_prompt,
)

DATASET = ROOT / 'ai-service/evaluation/customer-service-refusal-pairs-v1.json'
PROMPTS = {'baseline_r1': CUSTOMER_SERVICE_REFUSAL_SYSTEM_PROMPT,
           'candidate_r2': CUSTOMER_SERVICE_REFUSAL_CANDIDATE_SYSTEM_PROMPT}


def load_diagnostic(path: Path = DATASET, *, snapshot: Path | None = None) -> tuple[dict, dict]:
    """先校验历史实际上下文及冻结题身份，禁止实验时用更完整知识给回答补证。"""
    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schemaVersion') != 'rag-refusal-fixed-context/v1':
        raise ValueError('RAG_EVAL_REFUSAL_SCHEMA_INVALID')
    source = snapshot or ROOT / data['sourceReportPath']
    if hashlib.sha256(source.read_bytes()).hexdigest() != data['sourceReportSha256']:
        raise ValueError('RAG_EVAL_SNAPSHOT_CHANGED')
    report = json.loads(source.read_text(encoding='utf8'))
    if report.get('datasetSha256') != data['sourceDatasetSha256']:
        raise ValueError('RAG_EVAL_SOURCE_DATASET_CHANGED')
    source_rows = {(row['id'], row['arm']): row for row in report['cases']}
    # 仅读取冻结v2原题身份，不修改原数据和原成绩。
    frozen = json.loads((ROOT / 'ai-service/evaluation/customer-service-boundaries-v2.json').read_text(encoding='utf8'))
    if fingerprint(frozen) != data['sourceDatasetSha256']:
        raise ValueError('RAG_EVAL_FROZEN_DATASET_CHANGED')
    frozen_entries = {e['id']: e for e in frozen['entries']}
    sets = data.get('contextSets', {})
    if len(sets) != 8:
        raise ValueError('RAG_EVAL_CONTEXT_SETS_INVALID')
    for value in sets.values():
        original = source_rows.get((value['sourceCaseId'], value['sourceArm']), {})
        actual = [{k: r[k] for k in ('chunkId', 'documentId', 'locator', 'content')}
                  for r in original.get('contexts', [])]
        if original.get('status') != 'ok' or not actual or value['contexts'] != actual:
            raise ValueError('RAG_EVAL_CONTEXT_CHANGED')
    seen, pairs, questions = set(), {}, set()
    for entry in data.get('entries', []):
        answerable = entry.get('expectedAnswerable')
        if (not entry.get('id') or entry['id'] in seen or not entry.get('question')
                or entry['question'] in questions or type(answerable) is not bool
                or entry.get('contextSetId') not in sets or not entry.get('pairId')
                or bool(entry.get('requiredFacts')) != answerable):
            raise ValueError('RAG_EVAL_REFUSAL_ENTRY_INVALID')
        if not answerable:
            original = frozen_entries.get(entry['sourceCaseId'], {})
            if original.get('expectedAnswerable') is not False or original.get('question') != entry['question']:
                raise ValueError('RAG_EVAL_FROZEN_REFUSAL_CHANGED')
        contexts = {r['chunkId']: r['content'] for r in sets[entry['contextSetId']]['contexts']}
        for fact in entry['requiredFacts']:
            if not fact.get('statement') or not fact.get('evidence'):
                raise ValueError('RAG_EVAL_REFUSAL_FACT_INVALID')
            for ref in fact['evidence']:
                if not ref.get('quote') or ref['quote'] not in contexts.get(ref.get('chunkId'), ''):
                    raise ValueError('RAG_EVAL_REFUSAL_EVIDENCE_INVALID')
        seen.add(entry['id']); questions.add(entry['question'])
        pairs.setdefault(entry['pairId'], []).append(entry)
    if (len(seen) != 16 or len(pairs) != 8 or any(len(rows) != 2
            or {r['expectedAnswerable'] for r in rows} != {False, True}
            or len({r['contextSetId'] for r in rows}) != 1 for rows in pairs.values())):
        raise ValueError('RAG_EVAL_REFUSAL_PAIRS_INVALID')
    if CUSTOMER_SERVICE_SYSTEM_PROMPT != CUSTOMER_SERVICE_REFUSAL_SYSTEM_PROMPT:
        raise ValueError('RAG_EVAL_PRODUCTION_BASELINE_CHANGED')
    return data, report


def planned_calls(data: dict, rounds: int, max_chat: int | None = None) -> int:
    if type(rounds) is not int or not 1 <= rounds <= 3:
        raise ValueError('RAG_EVAL_ROUNDS_INVALID')
    required = len(data['entries']) * len(PROMPTS) * rounds
    if required > 96 or (max_chat is not None and (
            type(max_chat) is not int or not required <= max_chat <= 96)):
        raise ValueError('RAG_EVAL_BUDGET_INVALID')
    return required


def parse_answer(raw: str, contexts: list[dict], finish_reason: str | None = None) -> dict:
    """沿用生产严格输出schema和引用边界，截断输出另算错误，不能当正确拒答。"""
    from ai_service.models.openai_compatible import _CustomerServiceAnswerPayload
    if finish_reason not in (None, 'stop'):
        raise ValueError('RAG_EVAL_ANSWER_FINISH_INVALID')
    try:
        value = _CustomerServiceAnswerPayload.model_validate_json(raw)
    except Exception:
        raise ValueError('RAG_EVAL_ANSWER_INVALID') from None
    refs = value.cited_chunk_ids
    allowed = {r['chunkId'] for r in contexts}
    if (len(refs) != len(set(refs)) or not set(refs) <= allowed
            or (value.answered and (not value.answer.strip() or not refs))
            or (not value.answered and (value.answer != '' or refs))):
        raise ValueError('RAG_EVAL_ANSWER_INVALID')
    return {'answered': value.answered, 'answer': value.answer, 'citedChunkIds': refs}


def summarize(data: dict, cases: list[dict], rounds: int) -> dict:
    """预定分母不随失败/中断缩小；公开题契约通过仍须另行人工事实判断。"""
    lookup = {(r['id'], r['arm'], r['round']): r for r in cases}
    result = {}
    for arm in PROMPTS:
        metrics = {'expected': len(data['entries']) * rounds, 'missing': 0, 'errors': 0,
                   'refusalExpected': 8 * rounds, 'refusalContractPassed': 0,
                   'publicExpected': 8 * rounds, 'publicAnswered': 0, 'publicFalseRefusals': 0,
                   'publicSemanticCorrectness': None}
        for entry in data['entries']:
            for repeat in range(1, rounds + 1):
                row = lookup.get((entry['id'], arm, repeat))
                if row is None:
                    metrics['missing'] += 1
                elif row['status'] != 'ok':
                    metrics['errors'] += 1
                elif entry['expectedAnswerable']:
                    metrics['publicAnswered' if row['answered'] else 'publicFalseRefusals'] += 1
                elif not row['answered'] and row['answer'] == '' and row['citedChunkIds'] == []:
                    metrics['refusalContractPassed'] += 1
        measured = [r for r in cases if r['arm'] == arm and r['status'] == 'ok']
        latencies = sorted(r['latencyMs'] for r in measured if 'latencyMs' in r)
        metrics['meanLatencyMs'] = fmean(latencies) if latencies else None
        metrics['p95LatencyMs'] = latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None
        metrics['usage'] = {key: sum(r.get('usage', {}).get(key, 0) for r in cases if r['arm'] == arm)
                            for key in ('input_tokens', 'output_tokens', 'total_tokens')}
        result[arm] = metrics
    result['paired'] = [{'id': e['id'], 'round': repeat,
        'baseline': lookup.get((e['id'], 'baseline_r1', repeat), {}).get('answered'),
        'candidate': lookup.get((e['id'], 'candidate_r2', repeat), {}).get('answered')}
        for e in data['entries'] for repeat in range(1, rounds + 1)]
    return result


def calibration_queue(data: dict, historical: dict, cases: list[dict]) -> list[dict]:
    """显式抽取已知失败与争议题；不再用等距抽样漏掉关键题，也不自动裁决争议。"""
    focus = data['calibrationFocus']
    queue = [{'origin': 'historical-fixed-snapshot', 'id': row['id'], 'arm': row['arm'],
              'question': next(e['question'] for e in json.loads((ROOT /
                  'ai-service/evaluation/customer-service-boundaries-v2.json').read_text(encoding='utf8'))['entries']
                  if e['id'] == row['id']),
              'contexts': row['contexts'], 'answer': row.get('answer'),
              'answered': row.get('answered'), 'citedChunkIds': row.get('citedChunkIds'),
              'priorModelJudge': row.get('judge'),
              'annotationDispute': focus['annotationDisputes'].get(row['id']),
              'reviewStatus': 'human-review-pending', 'humanVerdict': None,
              'reviewDimensions': focus['reviewDimensions']}
             for row in historical['cases'] if row['id'] in focus['historicalCaseIds']]
    entries = {e['id']: e for e in data['entries']}
    for row in cases:
        entry = entries[row['id']]
        queue.append({'origin': 'refusal-paired-diagnostic', 'id': row['id'],
                      'arm': row['arm'], 'round': row['round'], 'status': row['status'],
                      'question': entry['question'], 'expectedAnswerable': entry['expectedAnswerable'],
                      'requiredFacts': entry['requiredFacts'], 'contexts': row['contexts'],
                      'answer': row.get('answer'), 'answered': row.get('answered'),
                      'citedChunkIds': row.get('citedChunkIds'),
                      'reviewStatus': 'human-review-pending', 'humanVerdict': None,
                      'reviewDimensions': focus['reviewDimensions']})
    return queue


async def execute(data: dict, historical: dict, output: Path, *, rounds: int,
                  max_chat: int, invoke, config: dict, prompt_max_bytes: int) -> dict:
    """串行交错两臂；调用函数无工具，额度预占失败时绝不发送请求。"""
    from ai_service.models.base import CustomerServiceContext
    planned = planned_calls(data, rounds, max_chat)
    # 提前验证全部消息大小，固定上下文不允许为新题偷偷裁剪或替换。
    user_prompts = {}
    for entry in data['entries']:
        contexts = data['contextSets'][entry['contextSetId']]['contexts']
        user_prompts[entry['id']] = customer_service_user_prompt(entry['question'], [
            CustomerServiceContext(r['chunkId'], r['content']) for r in contexts], max_bytes=prompt_max_bytes)
    output.mkdir(parents=True, exist_ok=False)
    budget = CallBudget(output / 'calls.jsonl', max_chat=max_chat)
    report = {'schemaVersion': 'rag-refusal-experiment/v1', 'status': 'running',
              'startedAtUtc': datetime.now(timezone.utc).isoformat(),
              'datasetSha256': fingerprint(data), 'sourceReportSha256': data['sourceReportSha256'],
              'implementationSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'promptSha256': {k: hashlib.sha256(p.encode()).hexdigest() for k, p in PROMPTS.items()},
              'config': {**config, 'rounds': rounds, 'maxChatCalls': max_chat,
                         'plannedCalls': planned, 'automaticRetries': 0, 'concurrency': 1,
                         'order': 'alternating-arms-by-case-and-round', 'promptMaxBytes': prompt_max_bytes},
              'retrievalCalls': 0, 'embeddingCalls': 0, 'judgeCalls': 0,
              'productionPromptChanged': False, 'productionIndexWrites': 0,
              'humanReview': 'pending', 'promotionDecision': 'not-evaluated', 'cases': []}

    def save():
        report['callsReserved'] = dict(budget.counts)
        report['metrics'] = summarize(data, report['cases'], rounds)
        temporary = output / 'report.partial.json'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
        temporary.replace(output / 'report.json')

    save()
    try:
        failures = 0
        for repeat in range(1, rounds + 1):
            for index, entry in enumerate(data['entries']):
                arms = list(PROMPTS) if (index + repeat) % 2 else list(reversed(PROMPTS))
                for arm in arms:
                    row = {'id': entry['id'], 'pairId': entry['pairId'], 'arm': arm, 'round': repeat,
                           'status': 'reserved', 'contexts': data['contextSets'][entry['contextSetId']]['contexts']}
                    key = f"{entry['id']}:{arm}:round-{repeat}"
                    # 写盘和报告均在任何网络调用前完成；取消或异常不会返还额度。
                    budget.reserve('chat', key)
                    report['cases'].append(row)
                    save()
                    start = time.perf_counter()
                    try:
                        response = await invoke(PROMPTS[arm], user_prompts[entry['id']])
                        row['usage'] = response.get('usage', {})
                        row['providerModel'] = response.get('model')
                        row['providerFingerprint'] = response.get('systemFingerprint')
                        row.update(parse_answer(response['content'], row['contexts'], response.get('finishReason')))
                        row['status'] = 'ok'
                        failures = 0
                    except asyncio.CancelledError:
                        row.update(status='error', errorCode='INTERRUPTED')
                        raise
                    except Exception as error:
                        row.update(status='error', errorCode=error_code(error))
                        failures += 1
                    finally:
                        row['latencyMs'] = (time.perf_counter() - start) * 1000
                        save()
                    print(json.dumps({'completed': len(report['cases']), 'planned': planned,
                                      'reserved': budget.counts['chat'], 'status': row['status']}), flush=True)
                    if failures >= 3:
                        raise ValueError('RAG_EVAL_REPEATED_CALL_FAILURE')
        report['status'] = 'completed'
    except BaseException:
        report['status'] = 'failed-or-interrupted'
        raise
    finally:
        report['finishedAtUtc'] = datetime.now(timezone.utc).isoformat()
        save()
        queue = calibration_queue(data, historical, report['cases'])
        (output / 'human-calibration.json').write_text(json.dumps(queue, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return report


async def run_live(data: dict, historical: dict, output: Path, *, rounds: int, max_chat: int):
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from langsmith import tracing_context
    from ai_service.config import Settings
    cfg = Settings(_env_file=ROOT / 'ai-service/.env')
    # 此实验直接使用冻结提示和 SDK，必须在创建客户端前拒绝非文本模式。
    if cfg.rag_answer_response_format != 'text':
        raise ValueError('RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG')
    client = ChatOpenAI(api_key=cfg.model_api_key, base_url=cfg.model_base_url,
                        model=cfg.model_name, temperature=cfg.model_temperature,
                        max_tokens=cfg.model_max_tokens, max_retries=0,
                        timeout=cfg.rag_answer_timeout_seconds)

    async def invoke(system: str, user: str):
        # 禁止旁路trace携带评测正文或供应商凭据；不登记原始异常消息。
        with tracing_context(enabled=False):
            response = await client.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        metadata = response.response_metadata or {}
        return {'content': str(response.content), 'usage': response.usage_metadata or {},
                'finishReason': metadata.get('finish_reason'), 'model': metadata.get('model_name'),
                'systemFingerprint': metadata.get('system_fingerprint')}

    return await execute(data, historical, output, rounds=rounds, max_chat=max_chat, invoke=invoke,
                         prompt_max_bytes=cfg.rag_prompt_max_bytes,
                         config={'model': cfg.model_name, 'temperature': cfg.model_temperature,
                                 'maxTokens': cfg.model_max_tokens})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--max-chat-calls', type=int, default=0)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        data, historical = load_diagnostic(args.dataset, snapshot=args.snapshot)
        required = planned_calls(data, args.rounds)
        if not args.live:
            print(json.dumps({'mode': 'offline-validation', 'cases': len(data['entries']),
                              'pairs': len(data['contextSets']), 'plannedLiveCalls': required,
                              'modelCalls': 0, 'datasetSha256': fingerprint(data)}))
            return
        planned_calls(data, args.rounds, args.max_chat_calls)
        if args.output is None:
            raise ValueError('RAG_EVAL_OUTPUT_REQUIRED')
        output = safe_output(args.output)
        report = asyncio.run(run_live(data, historical, output, rounds=args.rounds, max_chat=args.max_chat_calls))
        print(json.dumps({'status': report['status'], 'callsReserved': report['callsReserved'],
                          'metrics': report['metrics']}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status': 'failed', 'errorCode': error_code(error)}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
