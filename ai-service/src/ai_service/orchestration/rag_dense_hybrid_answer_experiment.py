"""第32轮固定检索策略同期回答；唯一账本分阶段执行，不自动恢复或补跑。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from difflib import SequenceMatcher
import json
import os
from pathlib import Path

import httpx
from langchain_core.messages import AIMessage

from ai_service.config import Settings
from ai_service.models.base import CustomerServiceContext
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.orchestration.rag_hybrid_append_answer_experiment import durable_json, error_code, configuration
from ai_service.orchestration import rag_hybrid_experiment as retrieval
from ai_service.orchestration.rag_hybrid_cap_evaluation import ARCHIVE_SHA256, expand_archive
from ai_service.orchestration.rag_qa_linked_experiment import dense_top8, embedding_batch
from ai_service.orchestration.rag_qa_linked_cap_evaluation import safe_path, select_arm
from ai_service.orchestration.rag_qa_linked_corpus import ROOT, PRODUCT, read_json, sha
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_rerank_experiment import _local_model_manifest
from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt

RUN = ROOT / 'tmp/rag-dense-hybrid-answer-20261123'
NEW = ROOT / 'ai-service/evaluation/dense-hybrid-new-20261123.json'
ARCHIVE = PRODUCT / 'evaluation/results/hybrid-retrieval-20261120-v1.json'
ARMS = ('dense', 'hybrid')
LIMITS = dict(embedding=1, embeddingInputs=24, bge=48, chat=228)


def schedule(entries, kind):
    return [dict(kind=kind, key=f'{e["id"]}:{arm}') for i, e in enumerate(entries)
            for arm in (ARMS if i % 2 == 0 else ARMS[::-1])]


def check_bindings(plan):
    if any(sha(ROOT / path) != digest for path, digest in plan['bindings'].items()):
        raise ValueError('STUDY_INPUT_CHANGED')


class Ledger:
    """唯一调度账本；预占落盘失败后禁止继续，阶段重启也不能续跑。"""

    def __init__(self, path, operations, *, header=None):
        self.operations, self.count, self.poisoned = operations, 0, False
        self.stream = Path(path).open('x' if header is not None else 'a', encoding='utf8')
        try:
            if header is not None:
                self.append(dict(type='authorization', **header))
        except BaseException:
            self.close()
            raise

    def append(self, row):
        if self.poisoned or self.stream.closed:
            raise ValueError('STUDY_LEDGER_POISONED')
        try:
            text = json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
            if self.stream.write(text) != len(text):
                raise OSError('STUDY_LEDGER_SHORT_WRITE')
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.poisoned = True
            raise

    def reserve(self, operation):
        expected = self.operations[self.count] if self.count < len(self.operations) else None
        if expected is None or any(operation.get(k) != v for k, v in expected.items()):
            raise ValueError('STUDY_BUDGET_ORDER')
        self.append(dict(type='reserve', ordinal=self.count + 1, **operation))
        self.count += 1

    def close(self):
        self.stream.close()


def new_entries():
    data = read_json(NEW)
    entries = data['entries']
    old = read_json(retrieval.RUBRIC)['entries']
    if (len(entries) != 24 or len({e['id'] for e in entries}) != 24
            or len({e['question'] for e in entries}) != 24
            or sum(e['expectedAnswerable'] is False for e in entries) != 6
            or {e['question'] for e in entries} & {e['question'] for e in old}):
        raise ValueError('STUDY_NEW_CASES_INVALID')
    for e in entries:
        if bool(e['facts']) != e['expectedAnswerable']:
            raise ValueError('STUDY_NEW_FACTS_INVALID')
        for doc, quote in e['facts']:
            path = safe_path(retrieval.CORPUS / 'documents' / (doc + '.md'), retrieval.CORPUS)
            if not quote or quote not in path.read_text(encoding='utf8'):
                raise ValueError('STUDY_NEW_QUOTE_INVALID')
    return entries


def config(settings):
    result = configuration(settings)
    if (result['responseFormat'] != 'text' or result['promptBytes'] != 16384
            or settings.rag_embedding_model != 'openai:text-embedding-3-large'
            or settings.rag_embedding_dimension != 3072 or not settings.closeai_api_key
            or settings.rag_reranker_device != 'cuda'):
        raise ValueError('STUDY_CONFIG_INVALID')
    result.update(chatLimit=228, embeddingCalls=1, bgeCalls=48,
                  embeddingEndpointSha256=fingerprint(settings.closeai_base_url),
                  embeddingModel=settings.rag_embedding_model, dimension=3072)
    return result


def prepare(settings):
    safe_path(RUN, ROOT / 'tmp')
    if RUN.exists():
        raise FileExistsError('STUDY_RUN_EXISTS')
    inputs = retrieval.load_inputs()
    if sha(ARCHIVE) != ARCHIVE_SHA256:
        raise ValueError('STUDY_ARCHIVE_CHANGED')
    archive = read_json(ARCHIVE)
    cases = expand_archive(archive['comparison'], archive['chunksById'])['cases']
    entries = new_entries()
    _, model = _local_model_manifest(settings)
    if model != inputs['oldBge']:
        raise ValueError('STUDY_BGE_CHANGED')
    # 既有90题的检索结果只读冻结；新的回答不能挪用历史答案。
    if len(cases) != 90 or [c['id'] for c in cases] != [e['id'] for e in inputs['entries']]:
        raise ValueError('STUDY_OLD_CASES_CHANGED')
    bindings = dict(inputs['bindings'])
    files = [ARCHIVE, NEW, Path(__file__), Path(__file__).with_name('rag_dense_hybrid_review.py'),
        ROOT / 'ai-service/tests/test_rag_dense_hybrid_answer_experiment.py',
        ROOT / 'docs/superpowers/specs/2026-11-23-rag-dense-hybrid-answer-round32-design.md',
        ROOT / 'docs/superpowers/plans/2026-11-23-rag-dense-hybrid-answer-round32.md']
    files += [ROOT / 'ai-service/src/ai_service' / p for p in (
        'config.py', 'models/base.py', 'models/openai_compatible.py', 'models/reranker.py',
        'prompts/customer_service.py', 'orchestration/rag_hybrid_append_answer_experiment.py')]
    bindings.update({p.relative_to(ROOT).as_posix(): sha(p) for p in files})
    # 保护所有既有提交的评测材料，不能以新结果覆盖旧结果。
    bindings.update({p.relative_to(ROOT).as_posix(): sha(p) for p in (PRODUCT / 'evaluation').rglob('*.json')})
    queries = [e['question'] for e in entries]
    operations = [dict(kind='embedding', key='new-queries', inputs=24, inputSha256=fingerprint(queries))]
    operations += schedule(entries, 'bge')
    plan = dict(schemaVersion='rag-dense-hybrid-answer-plan/v1', limits=LIMITS,
        config=config(settings), bge=model, bindings=bindings, retrievalSchedule=operations,
        newCases=entries, oldCaseIds=[c['id'] for c in cases], humanReview='pending')
    old_entries = inputs['entries']
    plan['similarityAudit'] = [dict(id=e['id'], nearestOldId=nearest['id'],
        characterSimilarity=SequenceMatcher(None, e['question'], nearest['question']).ratio())
        for e in entries for nearest in [max(old_entries,
            key=lambda old: SequenceMatcher(None, e['question'], old['question']).ratio())]]
    check_bindings(plan)
    RUN.mkdir(parents=True)
    durable_json(RUN / 'plan.json', plan, exclusive=True)
    return fingerprint(plan)


def load_plan(settings, digest):
    safe_path(RUN, ROOT / 'tmp')
    plan = read_json(RUN / 'plan.json')
    if fingerprint(plan) != digest or plan['config'] != config(settings) or plan['limits'] != LIMITS:
        raise ValueError('STUDY_PLAN_CHANGED')
    check_bindings(plan)
    return plan


async def retrieve(settings, digest):
    plan = load_plan(settings, digest)
    if _local_model_manifest(settings)[1] != plan['bge']:
        raise ValueError('STUDY_BGE_CHANGED')
    inputs = retrieval.load_inputs()
    ledger = Ledger(RUN / 'authorization.jsonl', plan['retrievalSchedule'],
                    header=dict(planSha256=digest, limits=LIMITS))
    report = dict(status='running', rows=[], reservedOperations=0)
    reranker = None
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0)) as client:
            vectors, usage = await embedding_batch(client, settings,
                [e['question'] for e in plan['newCases']], ledger, plan['retrievalSchedule'][0])
        durable_json(RUN / 'query-vectors.json', dict(vectors=vectors, usage=usage), exclusive=True)
        reranker = await retrieval.create_reranker(settings)
        source_index = {c['chunk_id']: i for i, c in enumerate(inputs['chunks'])}
        for i, entry in enumerate(plan['newCases']):
            dense = dense_top8(inputs['chunks'], inputs['vectors']['after'], vectors[i])
            sparse = retrieval.bm25_top(inputs['chunks'], entry['question'])
            fused = retrieval.rrf_top([source_index[c.chunk_id] for c in dense], [j for j, _ in sparse])
            arms = dict(dense=dense, hybrid=[retrieval.make_chunk(inputs['chunks'][j], score) for j, score in fused])
            for arm in (ARMS if i % 2 == 0 else ARMS[::-1]):
                check_bindings(plan)
                operation = dict(kind='bge', key=f'{entry["id"]}:{arm}',
                    inputSha256=fingerprint(dict(question=entry['question'], chunks=[retrieval.record(c) for c in arms[arm]])))
                ledger.reserve(operation)
                ranked = await reranker.rerank(entry['question'], arms[arm], top_n=len(arms[arm]))
                row = dict(id=entry['id'], arm=arm, **select_arm(entry['question'], arms[arm], ranked, 1))
                durable_json(RUN / (entry['id'] + '-' + arm + '-retrieval.json'), row, exclusive=True)
                report['rows'].append(row)
                print('retrieval_completed ' + operation['key'], flush=True)
        report['status'] = 'completed'
        check_bindings(plan)
        ledger.append(dict(type='retrieval_complete', planSha256=digest, rowsSha256=fingerprint(report['rows'])))
    except BaseException as error:
        report.update(status='failed', errorCode=error_code(error))
        raise
    finally:
        report['reservedOperations'] = ledger.count
        ledger.close()
        try:
            durable_json(RUN / 'retrieval-report.json', report, exclusive=True)
        finally:
            if reranker is not None:
                await reranker.close()


def freeze_answers(settings, digest):
    plan = load_plan(settings, digest)
    report = read_json(RUN / 'retrieval-report.json')
    if report['status'] != 'completed' or report['reservedOperations'] != 49:
        raise ValueError('STUDY_RETRIEVAL_INCOMPLETE')
    lines = [json.loads(line) for line in (RUN / 'authorization.jsonl').read_text(encoding='utf8').splitlines()]
    expected = plan['retrievalSchedule']
    if len(lines) != 51 or lines[-1] != dict(type='retrieval_complete', planSha256=digest, rowsSha256=fingerprint(report['rows'])):
        raise ValueError('STUDY_RETRIEVAL_LEDGER')
    if any(r.get('type') != 'reserve' or r.get('ordinal') != i + 1 or any(r.get(k) != v for k, v in op.items())
           for i, (r, op) in enumerate(zip(lines[1:-1], expected, strict=True))):
        raise ValueError('STUDY_RETRIEVAL_LEDGER')
    archive = read_json(ARCHIVE)
    old = expand_archive(archive['comparison'], archive['chunksById'])['cases']
    old_questions = {e['id']: e['question'].strip() for e in read_json(retrieval.RUBRIC)['entries']}
    contexts = {(c['id'], a): c['arms'][a]['stages']['finalContext'] for c in old for a in ARMS}
    contexts.update({(r['id'], r['arm']): r['contexts'] for r in report['rows']})
    entries = [dict(id=key, question=old_questions[key], cohort='development') for key in plan['oldCaseIds']]
    entries += [dict(id=e['id'], question=e['question'], cohort='new_preregistered') for e in plan['newCases']]
    slots = []
    for i, e in enumerate(entries):
        for arm in (ARMS if i % 2 == 0 else ARMS[::-1]):
            value = contexts[(e['id'], arm)]
            if not 1 <= len(value) <= 3 or len({c['documentId'] for c in value}) != len(value):
                raise ValueError('STUDY_CONTEXT_CAP_INVALID')
            messages = [dict(role='system', content=CUSTOMER_SERVICE_SYSTEM_PROMPT), dict(role='user',
                content=customer_service_user_prompt(e['question'], [CustomerServiceContext(c['chunkId'], c['content']) for c in value], max_bytes=16384))]
            slots.append(dict(key=f'{e["id"]}:{arm}', arm=arm, **e, contexts=value,
                messages=messages, inputSha256=fingerprint(messages)))
    answer_plan = dict(planSha256=digest, config=plan['config'], slots=slots,
        retrievalReportSha256=sha(RUN / 'retrieval-report.json'), retrievalLedgerSha256=sha(RUN / 'authorization.jsonl'))
    durable_json(RUN / 'answer-plan.json', answer_plan, exclusive=True)
    return fingerprint(answer_plan)


class AnswerModel(OpenAICompatibleModel):
    def __init__(self, settings, client, ledger, slot, result):
        # 不初始化SDK；正式预算/提示/解析保留，HTTP单次发送受唯一账本约束。
        self._settings = settings
        self._customer_service_prompt_max_bytes = settings.rag_prompt_max_bytes
        self.client, self.ledger, self.slot, self.result = client, ledger, slot, result

    async def _invoke(self, messages, *, response_format=None):
        self._check_budget(messages)
        actual = [dict(role={'system':'system', 'human':'user'}[m.type], content=m.content) for m in messages]
        if response_format is not None or actual != self.slot['messages'] or fingerprint(actual) != self.slot['inputSha256']:
            raise ValueError('ANSWER_MESSAGES_CHANGED')
        self.ledger.reserve(dict(kind='chat', key=self.slot['key'], inputSha256=self.slot['inputSha256']))
        self.result['reserved'] = True
        body = dict(model=self._settings.model_name, messages=actual, temperature=self._settings.model_temperature,
            max_tokens=self._settings.model_max_tokens, stream=False)
        import time
        start = time.monotonic()
        try:
            async with asyncio.timeout(self._settings.rag_answer_timeout_seconds):
                self.result['attemptedSend'] = True
                async with self.client.stream('POST', self._settings.model_base_url.rstrip('/') + '/chat/completions',
                        headers={'Authorization':'Bearer ' + self._settings.model_api_key}, json=body) as response:
                    self.result['httpStatus'] = response.status_code
                    if response.status_code != 200:
                        raise ValueError('ANSWER_HTTP_FAILED')
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw) > 2 * 1024 * 1024:
                            raise ValueError('ANSWER_RESPONSE_TOO_LARGE')
            durable_json(RUN / (self.slot['key'].replace(':','-') + '-raw.json'),
                         dict(raw=raw.decode('utf8')), exclusive=True)
            payload = json.loads(raw)
            choices = payload.get('choices')
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('ANSWER_RESPONSE_INVALID')
            item = choices[0]
            reason = item.get('finish_reason')
            self.result['finishReason'] = reason if reason in ('stop','length','content_filter') else 'unknown'
            name = payload.get('model')
            self.result['responseModel'] = name if isinstance(name,str) and len(name) <= 128 and name.isprintable() else None
            self.result['usage'] = {k:v for k,v in payload.get('usage',{}).items()
                if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v >= 0}
            msg = item.get('message',{})
            if reason != 'stop' or not isinstance(msg.get('content'),str) or msg.get('tool_calls') or msg.get('function_call'):
                raise ValueError('ANSWER_RESPONSE_INVALID')
            return AIMessage(content=msg['content'])
        finally:
            self.result['latencyMs'] = round((time.monotonic()-start)*1000)


async def answer(settings, digest, answer_digest):
    plan = load_plan(settings, digest)
    frozen = read_json(RUN / 'answer-plan.json')
    if (fingerprint(frozen) != answer_digest or frozen['planSha256'] != digest
            or frozen['config'] != config(settings) or len(frozen['slots']) != 228
            or sha(RUN / 'retrieval-report.json') != frozen['retrievalReportSha256']
            or sha(RUN / 'authorization.jsonl') != frozen['retrievalLedgerSha256']):
        raise ValueError('STUDY_ANSWER_PLAN_CHANGED')
    report_file_hash = sha(RUN / 'answer-plan.json')
    expected_keys = schedule([dict(id=i) for i in plan['oldCaseIds']] + plan['newCases'], 'chat')
    if [s['key'] for s in frozen['slots']] != [s['key'] for s in expected_keys]:
        raise ValueError('STUDY_ANSWER_SCHEDULE_CHANGED')
    operations = [dict(kind='chat', key=s['key'], inputSha256=s['inputSha256']) for s in frozen['slots']]
    # 排他起跑标记先于append打开，任何中断均不得当作可恢复请求继续发送。
    durable_json(RUN / 'answer-start.json', dict(answerPlanSha256=answer_digest), exclusive=True)
    ledger = Ledger(RUN / 'authorization.jsonl', operations)
    report = dict(status='running', planSha256=digest, answerPlanSha256=answer_digest, humanReview='pending',
        results=[dict(key=s['key'], status='not_run', reserved=False, attemptedSend=False) for s in frozen['slots']])
    try:
        async with httpx.AsyncClient(timeout=settings.rag_answer_timeout_seconds, trust_env=False,
                follow_redirects=False, transport=httpx.AsyncHTTPTransport(retries=0)) as client:
            for slot, result in zip(frozen['slots'], report['results'], strict=True):
                try:
                    check_bindings(plan)
                    if sha(RUN / 'answer-plan.json') != report_file_hash:
                        raise ValueError('STUDY_ANSWER_PLAN_CHANGED')
                    model = AnswerModel(settings, client, ledger, slot, result)
                    value = await model.answer_customer_service(slot['question'],
                        [CustomerServiceContext(c['chunkId'],c['content']) for c in slot['contexts']])
                    result.update(status='completed', answer=asdict(value))
                    durable_json(RUN / (slot['key'].replace(':','-') + '-answer.json'), result, exclusive=True)
                    durable_json(RUN / 'answer-report.json', report)
                    print('answer_completed ' + slot['key'], flush=True)
                except BaseException as error:
                    result.update(status='failed', errorCode=error_code(error))
                    raise
        check_bindings(plan)
        report['status'] = 'completed'
    except BaseException as error:
        report.update(status='failed', errorCode=error_code(error))
        raise
    finally:
        report['reservedOperations'] = ledger.count
        ledger.close()
        durable_json(RUN / 'answer-report.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare','retrieve','freeze-answers','answer'])
    parser.add_argument('--plan-sha256')
    parser.add_argument('--answer-plan-sha256')
    args = parser.parse_args()
    settings = Settings(_env_file=ROOT / 'ai-service/.env')
    try:
        if args.phase == 'prepare':
            print(prepare(settings))
        elif not args.plan_sha256:
            parser.error('requires --plan-sha256')
        elif args.phase == 'retrieve':
            asyncio.run(retrieve(settings, args.plan_sha256))
        elif args.phase == 'freeze-answers':
            print(freeze_answers(settings, args.plan_sha256))
        else:
            asyncio.run(answer(settings, args.plan_sha256, args.answer_plan_sha256))
    except (Exception, KeyboardInterrupt):
        parser.exit(2, 'STUDY_FAILED_CHECK_LOCAL_REPORT\n')


if __name__ == '__main__':
    main()
