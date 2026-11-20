"""第31轮冻结证据回答实验；prepare 不联网，live 无重试且首次失败即停止。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

import httpx
from langchain_core.messages import AIMessage

from ai_service.config import Settings
from ai_service.models.base import CustomerServiceContext
from ai_service.models.openai_compatible import OpenAICompatibleModel, CustomerServiceModelOutputError
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_hybrid_cap_evaluation import expand_archive
from ai_service.orchestration.rag_qa_linked_corpus import ROOT, PRODUCT, read_json, sha
from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt

RUN = ROOT / 'tmp/rag-hybrid-append-answer-20261120'
PLAN = RUN / 'plan.json'
ANCHOR = RUN / 'authorization.jsonl'
COMPARISON = ROOT / 'tmp/rag-hybrid-append-20261120/run-final/comparison.json'
INTEGRITY = COMPARISON.with_name('input-integrity.json')
SOURCE_RESULT = PRODUCT / 'evaluation/results/hybrid-append-selection-20261120-v1.json'
RUBRIC = PRODUCT / 'evaluation/rubric-20261118-v1.json'
IDS = ('app-generation-005', 'app-generation-008', 'admin-knowledge-004',
       'deployment-download-009')
AUTHORIZATION = '继续执行下一轮'
SOURCE_SHA256 = 'cb9ad0c12cc66ce686640600bf644ae838a9af8d21cd35fd2d30678dfa18ef48'
COMPARISON_SHA256 = 'f039bae138cdcd33196b40943cdbb6139f5b370997a175c054a18eae88a34dbe'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def check_run_paths():
    """拒绝运行目录链上的链接，避免无意将唯一账本转移到其他目录。"""
    for path in (RUN, PLAN, ANCHOR):
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise ValueError('ANSWER_LINKED_PATH')


def durable_json(path, value, *, exclusive=False):
    """发送前及结果落盘均确认 fsync；失败不尝试修复或覆盖既有准备文件。"""
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def load_inputs():
    # 固定上轮归档和现场摘要，不能仅信任可随输入一起修改的自报哈希。
    if sha(SOURCE_RESULT) != SOURCE_SHA256 or sha(COMPARISON) != COMPARISON_SHA256:
        raise ValueError('ANSWER_SOURCE_CHANGED')
    source, integrity = read_json(SOURCE_RESULT), read_json(INTEGRITY)
    if source.get('schemaVersion') != 'rag-hybrid-append-validation-bundle/v1':
        raise ValueError('ANSWER_ARCHIVE_INVALID')
    if source.get('inputIntegrity') != integrity:
        raise ValueError('ANSWER_INTEGRITY_INVALID')
    hashes = integrity['sha256']
    if integrity['unchanged'] is not True or integrity['fileCount'] != 168 or len(hashes) != 168:
        raise ValueError('ANSWER_INTEGRITY_INVALID')
    if any(sha(Path(path)) != digest for path, digest in hashes.items()):
        raise ValueError('ANSWER_INPUT_CHANGED')
    comparison = read_json(COMPARISON)
    if expand_archive(source['comparison'], source['chunksById']) != comparison:
        raise ValueError('ANSWER_ARCHIVE_CHANGED')
    rows, entries = comparison['cases'], read_json(RUBRIC)['entries']
    if (len(rows) != 90 or len({row['id'] for row in rows}) != 90
            or len(entries) != 90 or len({row['id'] for row in entries}) != 90
            or {row['id'] for row in rows} != {row['id'] for row in entries}):
        raise ValueError('ANSWER_CASES_INVALID')
    cases = {row['id']: row for row in rows}
    rubric = {row['id']: row for row in entries}
    if sum(len(rubric[key]['requiredFacts']) for key in IDS) != 8:
        raise ValueError('ANSWER_FACT_COUNT_CHANGED')
    slots = []
    for index, key in enumerate(IDS):
        row = cases[key]
        if (row['question'] != rubric[key]['question'] or rubric[key]['expectedAnswerable'] is not True
                or row['requiredFacts'] != rubric[key]['requiredFacts'] or set(row['arms']) != {'cap1', 'append1'}):
            raise ValueError('ANSWER_QUESTION_CHANGED')
        for arm in (('cap1', 'append1') if index % 2 == 0 else ('append1', 'cap1')):
            contexts = row['arms'][arm]['stages']['finalContext']
            if (not contexts or contexts != row['arms'][arm]['contexts']
                    or len({c['chunkId'] for c in contexts}) != len(contexts)
                    or any(not isinstance(c.get('chunkId'), str) or not c['chunkId']
                        or not isinstance(c.get('content'), str) or not c['content'] for c in contexts)):
                raise ValueError('ANSWER_CONTEXT_INVALID')
            values = [CustomerServiceContext(c['chunkId'], c['content']) for c in contexts]
            messages = [dict(role='system', content=CUSTOMER_SERVICE_SYSTEM_PROMPT),
                        dict(role='user', content=customer_service_user_prompt(row['question'], values))]
            slots.append(dict(key=f'{key}:{arm}', id=key, arm=arm, question=row['question'],
                contexts=contexts, messages=messages, inputSha256=fingerprint(messages)))
    return slots


def configuration(settings):
    if not settings.model_api_key or settings.model_api_key == 'not-configured':
        raise ValueError('ANSWER_CONFIG_INVALID')
    url = httpx.URL(settings.model_base_url)
    if url.scheme not in ('http', 'https') or not url.host or url.userinfo or url.query or url.fragment:
        raise ValueError('ANSWER_ENDPOINT_INVALID')
    return dict(model=settings.model_name, temperature=settings.model_temperature,
        maxTokens=settings.model_max_tokens, timeoutSeconds=settings.rag_answer_timeout_seconds,
        responseFormat=settings.rag_answer_response_format, promptBytes=settings.rag_prompt_max_bytes,
        contextWindowTokens=settings.model_context_window_tokens,
        overheadTokens=settings.model_context_overhead_tokens,
        endpointSha256=fingerprint(settings.model_base_url), automaticRetries=0,
        chatLimit=8, embeddingCalls=0, bgeCalls=0, productionWrites=0)


def bindings():
    files = [COMPARISON, INTEGRITY, SOURCE_RESULT, RUBRIC, Path(__file__),
        RUN / 'frozen-review-basis.json', RUN / 'pre-answer-evidence-review.json',
        ROOT / 'ai-service/tests/test_rag_hybrid_append_answer_experiment.py',
        ROOT / 'docs/superpowers/plans/2026-11-20-rag-hybrid-append-answer-round31.md',
        ROOT / 'docs/superpowers/specs/2026-11-20-rag-hybrid-append-answer-round31-design.md']
    files += [ROOT / 'ai-service/src/ai_service' / path for path in (
        'config.py', 'models/base.py', 'models/openai_compatible.py', 'models/input_review.py',
        'prompts/__init__.py', 'prompts/customer_service.py', 'orchestration/rag_qa_linked_budget.py',
        'orchestration/rag_qa_linked_corpus.py', 'orchestration/rag_hybrid_cap_evaluation.py',
        'orchestration/rag_qa_linked_cap_evaluation.py', 'orchestration/rag_answer_first_evaluation.py')]
    files += [Path(p) for p in read_json(INTEGRITY)['sha256']]
    return {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in files}


def build_plan(settings):
    return dict(schemaVersion='rag-hybrid-append-answer-plan/v1', authorization=AUTHORIZATION,
        config=configuration(settings), bindings=bindings(), slots=load_inputs(),
        humanReview='pending', boundary='冻结上下文回答实验；非双服务、Milvus、权威快照或生产验收。')


def prepare(settings):
    check_run_paths()
    plan = build_plan(settings)
    # 根目录允许已有人工复核模板；计划排他创建保护半写入准备结果。
    RUN.mkdir(parents=True, exist_ok=True)
    if ANCHOR.exists() or (RUN / 'report.json').exists() or any(RUN.glob('*-cap1*.json')) or any(RUN.glob('*-append1*.json')):
        raise ValueError('ANSWER_OUTPUT_EXISTS')
    durable_json(PLAN, plan, exclusive=True)
    durable_json(RUN / 'freeze.json', dict(schemaVersion=plan['schemaVersion'],
        planSha256=fingerprint(plan), config=plan['config'], bindings=plan['bindings'],
        slots=[{k: s[k] for k in ('key', 'inputSha256')} for s in plan['slots']]), exclusive=True)
    return fingerprint(plan)


class AnswerBudget:
    """固定顺序的唯一预占账本；落盘不确定也永久停止本进程后续发送。"""

    def __init__(self, plan):
        check_run_paths()
        self.keys = [s['key'] for s in plan['slots']]
        expected = [f'{key}:{arm}' for i, key in enumerate(IDS)
                    for arm in (('cap1', 'append1') if i % 2 == 0 else ('append1', 'cap1'))]
        if self.keys != expected:
            raise ValueError('ANSWER_SCHEDULE_INVALID')
        self.count = 0
        self.poisoned = False
        self.stream = ANCHOR.open('x', encoding='utf-8')
        try:
            self._append(dict(type='authorization', planSha256=fingerprint(plan), chatLimit=8,
                embeddingLimit=0, bgeLimit=0, productionWrites=0, authorization=AUTHORIZATION))
        except BaseException:
            self.stream.close()
            raise

    def _append(self, row):
        if self.poisoned or self.stream.closed:
            raise ValueError('ANSWER_LEDGER_POISONED')
        try:
            text = json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
            if self.stream.write(text) != len(text):
                raise OSError('ANSWER_LEDGER_SHORT_WRITE')
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.poisoned = True
            raise

    def reserve(self, slot):
        if self.count >= 8 or slot['key'] != self.keys[self.count]:
            raise ValueError('ANSWER_BUDGET_ORDER')
        self._append(dict(type='reserve', ordinal=self.count + 1, key=slot['key'],
                          inputSha256=slot['inputSha256']))
        self.count += 1

    def close(self):
        self.stream.close()


class FrozenAnswerModel(OpenAICompatibleModel):
    def __init__(self, settings, client, budget, slot, result):
        # 不构造 SDK 客户端，避免其自动重试；其余正式提示/预算/答案解析直接继承。
        self._settings = settings
        self._customer_service_prompt_max_bytes = settings.rag_prompt_max_bytes
        self.client, self.budget, self.slot, self.result = client, budget, slot, result

    async def _invoke(self, messages, *, response_format=None):
        self._check_budget(messages)
        actual = [dict(role={'system': 'system', 'human': 'user'}[m.type], content=m.content) for m in messages]
        expected_format = 'json_object' if self._settings.rag_answer_response_format == 'json_object' else None
        if actual != self.slot['messages'] or fingerprint(actual) != self.slot['inputSha256'] or response_format != expected_format:
            raise ValueError('ANSWER_MESSAGES_CHANGED')
        cfg = self._settings
        body = dict(model=cfg.model_name, messages=actual, temperature=cfg.model_temperature,
                    max_tokens=cfg.model_max_tokens, stream=False)
        if response_format:
            body['response_format'] = dict(type=response_format)
        try:
            self.budget.reserve(self.slot)
        except BaseException:
            if self.budget.poisoned:
                self.result['reservationState'] = 'unknown'
            raise
        self.result['reserved'] = True
        self.result['reservationState'] = 'confirmed'
        self.result['status'] = 'failed'
        started = time.monotonic()
        try:
            async with asyncio.timeout(cfg.rag_answer_timeout_seconds):
                self.result['attemptedSend'] = True
                async with self.client.stream('POST', cfg.model_base_url.rstrip('/') + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + cfg.model_api_key}, json=body) as response:
                    self.result['httpStatus'] = response.status_code
                    if response.status_code != 200:
                        raise ValueError('ANSWER_HTTP_FAILED')
                    data = bytearray()
                    async for block in response.aiter_bytes():
                        data.extend(block)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ValueError('ANSWER_RESPONSE_TOO_LARGE')
            # 原始供应商输出只进入本轮 tmp，不进入可提交的冻结摘要。
            durable_json(RUN / (self.slot['key'].replace(':', '-') + '-raw.json'),
                         dict(raw=data.decode('utf-8')), exclusive=True)
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError('ANSWER_RESPONSE_INVALID')
            # 供应商自报名称仅为有界观测，不证明实际模型身份，不改变答案协议。
            name = payload.get('model')
            self.result['responseModel'] = (name if isinstance(name, str) and 0 < len(name) <= 128
                and name.isprintable() else None)
            choices = payload.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError('ANSWER_RESPONSE_INVALID')
            choice = choices[0]
            reason = choice.get('finish_reason')
            self.result['finishReason'] = reason if reason in ('stop', 'length', 'content_filter', 'tool_calls', 'function_call') else 'unknown'
            usage = payload.get('usage', {})
            self.result['usage'] = {key: usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0}
            message = choice.get('message', {})
            if reason != 'stop':
                raise ValueError('ANSWER_FINISH_INVALID')
            if not isinstance(message, dict) or not isinstance(message.get('content'), str) or message.get('tool_calls') or message.get('function_call'):
                raise ValueError('ANSWER_RESPONSE_INVALID')
            return AIMessage(content=message['content'])
        finally:
            self.result['latencyMs'] = round((time.monotonic() - started) * 1000)


def error_code(error):
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
        return 'CANCELLED'
    if isinstance(error, CustomerServiceModelOutputError):
        return 'ANSWER_CONTRACT_INVALID'
    if isinstance(error, (TimeoutError, httpx.HTTPError)):
        return 'ANSWER_TRANSPORT_FAILED'
    if isinstance(error, OSError):
        return 'ANSWER_PERSISTENCE_FAILED'
    if isinstance(error, ValueError) and str(error) in {
        'ANSWER_HTTP_FAILED', 'ANSWER_RESPONSE_TOO_LARGE', 'ANSWER_RESPONSE_INVALID',
        'ANSWER_FINISH_INVALID', 'ANSWER_MESSAGES_CHANGED', 'ANSWER_INPUT_CHANGED'}:
        return str(error)
    return 'ANSWER_EXECUTION_FAILED'


async def execute(settings, plan, expected_hash, client):
    if (RUN / 'report.json').exists():
        raise FileExistsError('ANSWER_OUTPUT_EXISTS')
    budget = AnswerBudget(plan)
    report = dict(schemaVersion='rag-hybrid-append-answer-report/v1', planSha256=expected_hash,
        status='running', humanReview='pending', reservedOperations=0,
        results=[dict(key=s['key'], inputSha256=s['inputSha256'], status='not_run', reserved=False,
                      reservationState='not_attempted', attemptedSend=False, httpStatus=None, finishReason=None, usage={}, latencyMs=None,
                      errorCode=None, responseModel=None) for s in plan['slots']])
    report_created = False
    try:
        durable_json(RUN / 'report.json', report, exclusive=True)
        report_created = True
        for slot, result in zip(plan['slots'], report['results'], strict=True):
            try:
                if plan != build_plan(settings):
                    raise ValueError('ANSWER_INPUT_CHANGED')
                model = FrozenAnswerModel(settings, client, budget, slot, result)
                answer = await model.answer_customer_service(slot['question'],
                    [CustomerServiceContext(c['chunkId'], c['content']) for c in slot['contexts']])
                result.update(status='completed', answer=asdict(answer), contractValid=True)
                durable_json(RUN / (slot['key'].replace(':', '-') + '.json'), result, exclusive=True)
                report['reservedOperations'] = budget.count
                durable_json(RUN / 'report.json', report)
                print('answer_completed ' + slot['key'], flush=True)
            except BaseException as error:
                result.update(status='failed', errorCode=error_code(error), contractValid=False)
                print('answer_failed ' + slot['key'] + ' ' + result['errorCode'], flush=True)
                raise
        if plan != build_plan(settings):
            raise ValueError('ANSWER_INPUT_CHANGED')
        report['status'] = 'completed'
    except BaseException as error:
        report.update(status='failed', errorCode=error_code(error))
        raise
    finally:
        report['reservedOperations'] = budget.count
        budget.close()
        if report_created:
            durable_json(RUN / 'report.json', report)
    return report


async def live(settings, expected_hash, authorization):
    check_run_paths()
    if authorization != AUTHORIZATION:
        raise ValueError('ANSWER_AUTHORIZATION_REQUIRED')
    plan = read_json(PLAN)
    if fingerprint(plan) != expected_hash or plan != build_plan(settings):
        raise ValueError('ANSWER_PLAN_CHANGED')
    # 固定锚点排他创建是操作护栏；本地摘要并非抵抗恶意修改的安全系统。
    async with httpx.AsyncClient(timeout=settings.rag_answer_timeout_seconds, trust_env=False,
            follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=0)) as client:
        return await execute(settings, plan, expected_hash, client)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--authorization')
    args = parser.parse_args()
    if args.live and (not args.plan_sha256 or not args.authorization):
        parser.error('live requires --plan-sha256 and --authorization')
    try:
        settings = Settings(_env_file=ROOT / 'ai-service/.env')
        if args.prepare:
            print(prepare(settings))
        else:
            result = asyncio.run(live(settings, args.plan_sha256, args.authorization))
            print(json.dumps(dict(status=result['status'], reservedOperations=result['reservedOperations'])))
    except (Exception, KeyboardInterrupt):
        parser.exit(2, 'ANSWER_EXPERIMENT_FAILED_CHECK_LOCAL_REPORT\n')


if __name__ == '__main__':
    main()
