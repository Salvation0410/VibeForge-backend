"""冻结输入的 cap1/cap2 回答实验；默认离线，批准文件只绑定本轮最多 180 次回答。"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ai_service.models.base import CustomerServiceModelAnswer
from ai_service.orchestration.rag_cap_budget import CapAuthorizationBudget
from ai_service.orchestration.rag_cap_contracts import (
    ROOT, AUTHORIZATION, ARMS, STUDY_ID,
    fail, fingerprint, read_json, load_frozen_inputs, settings_manifest, check_settings,
    implementation_manifest, code_diagnostics, evaluation_bindings, schedule,
)
from ai_service.orchestration.rag_rerank_budget import now, write_json
from ai_service.orchestration.rag_rerank_experiment import answer_once, _runtime_model, _finish_run, code

TMP = ROOT / 'tmp'
CONTEXT_FIELDS = ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')
PLAN_FIELDS = {'schemaVersion', 'studyId', 'createdAtUtc', 'sourceReportSha256', 'sourceReplaySha256',
    'rubricSha256', 'mappingSha256', 'sourceFilesSha256', 'sourceSnapshot', 'config',
    'implementationFiles', 'preparationCode', 'evaluationBindings', 'schedule', 'contexts', 'liveOutput', 'maxChatCalls'}


def settings():
    from ai_service.config import Settings
    return Settings(_env_file=ROOT / 'ai-service/.env')


def _project(rows):
    return [{key: row[key] for key in CONTEXT_FIELDS} for row in rows]


async def prepare_contexts(frozen, cfg, *, check_replay=True):
    """用正式路径产生实际 contexts，Fake 只替代最终模型，不能自行重写选择逻辑。"""
    class CaptureModel:
        calls = 0
        async def answer_customer_service(self, question, contexts):
            self.calls += 1
            self.question = question
            self.contexts = [{'chunkId': c.chunk_id, 'content': c.content} for c in contexts]
            return CustomerServiceModelAnswer(False, '', ())

    contexts = {}
    for entry, arm in schedule(frozen.rubric['entries']):
        model = CaptureModel()
        cached = frozen.prepared[entry['id']]
        row = await answer_once(cfg.model_copy(update={'rag_document_chunk_limit': int(arm[-1])}),
            entry, 'rerank_on', frozen.vectors[entry['id']], cached['retrieved'],
            cached['filtered'], cached['ranked'], model)
        if (row['status'] != 'ok' or row['degraded'] or model.calls != 1
                or model.question != entry['question'].strip()
                or model.contexts != [{'chunkId': c['chunkId'], 'content': c['content']} for c in row['contexts']]):
            fail('FORMAL_PREPARATION_FAILED')
        contexts[entry['id'] + ':' + arm] = row['contexts']
    if check_replay:
        cases = frozen.replay['cases']
        if ([c['id'] for c in cases] != [e['id'] for e in frozen.rubric['entries']]
                or len(cases) != 90):
            fail('REPLAY_CASES_CHANGED')
        for entry, case in zip(frozen.rubric['entries'], cases, strict=True):
            if case['question'] != entry['question'].strip() or set(case['arms']) != set(ARMS):
                fail('REPLAY_CASES_CHANGED')
            for arm in ARMS:
                if contexts[entry['id'] + ':' + arm] != _project(case['arms'][arm]):
                    fail('REPLAY_CONTEXT_CHANGED')
    return contexts


def _new_output(output):
    output = Path(output).resolve()
    if not output.is_relative_to(TMP.resolve()) or output.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    return output


async def prepare(output):
    """只生成 pending 请求；不创建供应商 SDK、正式授权账本或 live 目录。"""
    cfg = settings()
    frozen = load_frozen_inputs(cfg)
    contexts = await prepare_contexts(frozen, cfg)
    output = _new_output(output)
    plan = {'schemaVersion': 'rag-cap-plan/v1', 'studyId': STUDY_ID, 'createdAtUtc': now(),
        'sourceReportSha256': frozen.source_report_sha256,
        'sourceReplaySha256': fingerprint(frozen.replay), 'rubricSha256': fingerprint(frozen.rubric),
        'mappingSha256': fingerprint(frozen.mapping), 'sourceFilesSha256': frozen.source_files,
        'sourceSnapshot': frozen.report['snapshot'], 'config': settings_manifest(cfg),
        'evaluationBindings': evaluation_bindings(),
        'implementationFiles': implementation_manifest(), 'preparationCode': code_diagnostics(),
        'schedule': [e['id'] + ':' + arm for e, arm in schedule(frozen.rubric['entries'])],
        'contexts': contexts, 'liveOutput': str(output / 'live'), 'maxChatCalls': 180}
    request = {'schemaVersion': 'rag-cap-authorization/v1', 'studyId': STUDY_ID,
        'decision': 'pending', 'planSha256': fingerprint(plan), 'maxChatCalls': 180,
        'liveOutput': plan['liveOutput']}
    output.mkdir(parents=True)
    write_json(output / 'plan.json', plan)
    write_json(output / 'authorization-request.json', request)
    return plan


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schemaVersion: Literal['rag-cap-authorization/v1']
    studyId: Literal['rag-cap-20261118-answer-round-v1']
    decision: Literal['approved']
    planSha256: str = Field(pattern='^[0-9a-f]{64}$')
    maxChatCalls: StrictInt = Field(ge=180, le=180)
    liveOutput: str


def validate_approval(approval, plan):
    try:
        value = Approval.model_validate(approval)
        if value.planSha256 != fingerprint(plan) or value.liveOutput != plan.get('liveOutput'):
            fail('APPROVAL_INVALID')
    except Exception:
        fail('APPROVAL_INVALID')
    # 软件不能认证批准人；Agent 只能在收到明确用户授权后创建 approved 文件。
    return value.model_dump()


def validate_plan(plan, cfg):
    """HEAD 仅供诊断；关键文件、输入或配置变化才要求重新准备并重新批准。"""
    if (not isinstance(plan, dict) or set(plan) != PLAN_FIELDS
            or plan['schemaVersion'] != 'rag-cap-plan/v1' or plan['studyId'] != STUDY_ID
            or type(plan['maxChatCalls']) is not int or plan['maxChatCalls'] != 180):
        fail('PLAN_INVALID')
    frozen = load_frozen_inputs(cfg)
    if (plan['sourceReportSha256'] != frozen.source_report_sha256
            or plan['sourceReplaySha256'] != fingerprint(frozen.replay)
            or plan['rubricSha256'] != fingerprint(frozen.rubric)
            or plan['mappingSha256'] != fingerprint(frozen.mapping)
            or plan['sourceFilesSha256'] != frozen.source_files
            or plan['sourceSnapshot'] != frozen.report['snapshot']):
        fail('PLAN_INPUTS_CHANGED')
    if plan['implementationFiles'] != implementation_manifest():
        fail('IMPLEMENTATION_CHANGED')
    if plan['evaluationBindings'] != evaluation_bindings():
        fail('EVALUATION_BINDINGS_CHANGED')
    if plan['config'] != check_settings(cfg, frozen.report['config']):
        fail('SETTINGS_CHANGED')
    if plan['schedule'] != [e['id'] + ':' + arm for e, arm in schedule(frozen.rubric['entries'])]:
        fail('PLAN_SCHEDULE_CHANGED')
    _new_output(plan['liveOutput'])
    return frozen


class FrozenContextModel:
    """在底层预算和 SDK 之前比对完整模型输入；拒绝后不允许继续消费。"""
    def __init__(self, model, questions, contexts):
        self.model, self.questions, self.contexts = model, deepcopy(questions), deepcopy(contexts)
        self.invalid_context = False
        self.pre_send_failure = False

    @property
    def observation(self):
        return self.model.observation

    def begin(self, key):
        self.key = key
        self.model.begin(key)

    async def answer_customer_service(self, question, contexts):
        expected = self.contexts.get(self.key)
        actual = [{'chunkId': c.chunk_id, 'content': c.content} for c in contexts]
        if (self.invalid_context or expected is None
                or question != self.questions.get(self.key.rsplit(':', 1)[0])
                or actual != [{'chunkId': c['chunkId'], 'content': c['content']} for c in expected]):
            self.invalid_context = True
            fail('CONTEXT_MISMATCH')
        try:
            return await self.model.answer_customer_service(question, contexts)
        except Exception:
            # 正式服务会把模型异常折成可靠性 fallback；预占失败须另留标记并停止后续题目。
            if not self.observation.get('providerCalled'):
                self.pre_send_failure = True
            raise


def new_report(frozen, plan, approval):
    return {'schemaVersion': 'rag-cap-experiment/v1', 'studyId': STUDY_ID,
        'rubricSha256': fingerprint(frozen.rubric), 'startedAtUtc': now(), 'status': 'preparing',
        'humanReview': 'pending', 'productionWrites': 0, 'automaticRetries': 0, 'concurrency': 1,
        'onlineE2ETested': False, 'currentAuthoritativeSnapshotChecked': False,
        'sourceClaim': 'historical-frozen-publication-only',
        'newEmbeddingCalls': 0, 'newBgeCalls': 0, 'productionIndexWrites': 0,
        'sourceReportSha256': frozen.source_report_sha256, 'sourceReplaySha256': fingerprint(frozen.replay),
        'sourceFilesSha256': dict(frozen.source_files), 'sourceSnapshot': frozen.report['snapshot'],
        'sourceReceipt': frozen.report['receipt'], 'mappingSha256': fingerprint(frozen.mapping),
        'implementationFiles': plan['implementationFiles'], 'config': plan['config'],
        'evaluationBindings': plan['evaluationBindings'],
        'armConfig': {'cap1': {'perDocument': 1}, 'cap2': {'perDocument': 2}},
        'preparationCode': plan['preparationCode'], 'runtimeCode': code_diagnostics(),
        'planSha256': fingerprint(plan), 'approval': approval,
        'approvalSha256': fingerprint(approval), 'results': [
            {'id': e['id'], 'arm': arm, 'status': 'not_run', 'contexts': [],
             'answered': None, 'answer': None, 'sources': [], 'citedChunkIds': []}
            for e, arm in schedule(frozen.rubric['entries'])]}


async def execute_answers(report, frozen, cfg, model, save):
    """占位先落盘，单并发交替调用；错误和未运行项保留每组 90 分母。"""
    entries = {e['id']: e for e in frozen.rubric['entries']}
    streak = 0
    report['status'] = 'answering'
    save()
    for index, placeholder in enumerate(report['results']):
        entry, arm = entries[placeholder['id']], placeholder['arm']
        try:
            model.begin(entry['id'] + ':' + arm)
            cached = frozen.prepared[entry['id']]
            row = await answer_once(cfg.model_copy(update={'rag_document_chunk_limit': int(arm[-1])}),
                entry, 'rerank_on', frozen.vectors[entry['id']], cached['retrieved'],
                cached['filtered'], cached['ranked'], model)
            row.update(model.observation, arm=arm)
            report['results'][index] = row
            if model.observation.get('providerCalled'):
                streak = streak + 1 if row['status'] == 'error' else 0
            save()
            # 检索等故障可能在模型包装层前被正式服务折成错误结果，也必须立即停止。
            pre_send_error = row['status'] == 'error' and not model.observation.get('providerCalled')
            if model.invalid_context or model.pre_send_failure or pre_send_error or streak >= 3:
                report.update(status='stopped', stopReason='RAG_CAP_CONTEXT_MISMATCH'
                    if model.invalid_context else 'RAG_CAP_PRE_SEND_FAILED'
                    if model.pre_send_failure or pre_send_error else 'THREE_CONSECUTIVE_CALL_FAILURES')
                break
        except asyncio.CancelledError as error:
            report['results'][index].update(status='error', errorCode='CANCELLED', **model.observation)
            report.update(status='cancelled', stopReason='CANCELLED')
            try:
                save()
            except (Exception, asyncio.CancelledError):
                report.setdefault('reportSaveFailures', []).append('cancelled_result')
            raise error
        except Exception as error:
            # code() 仅识别稳定旧码；新实验固定码在此白名单保留，不输出异常正文。
            value = str(error)
            stable = value if value in {'RAG_CAP_CONTEXT_MISMATCH', 'RAG_CAP_LEDGER_UNAVAILABLE',
                'RAG_CAP_CHAT_BUDGET_INVALID', 'RAG_CAP_REPORT_SAVE_FAILED'} else code(error)
            report['results'][index].update(status='error', errorCode=stable, **model.observation)
            report.update(status='stopped', stopReason=stable)
            save()
            break
    else:
        report['status'] = 'complete'
    save()


async def run_live(plan_path, approval_path):
    # 顺序是批准 → 冻结/实现/配置/上下文 → 输出和固定锚点 → SDK，不能提前构造资源。
    plan = read_json(plan_path)
    approval = validate_approval(read_json(approval_path), plan)
    cfg = settings()
    frozen = validate_plan(plan, cfg)
    if await prepare_contexts(frozen, cfg) != plan['contexts']:
        fail('PLAN_CONTEXT_CHANGED')
    output = _new_output(plan['liveOutput'])
    if AUTHORIZATION.exists():
        fail('AUTHORIZATION_ALREADY_USED')
    report = new_report(frozen, plan, approval)
    output.mkdir(parents=True)
    budget = model = original_cancel = None

    def save():
        report['callsReserved'] = dict(budget.counts) if budget else {
            'chat': 0, 'embeddingRequests': 0, 'queryInputs': 0}
        try:
            write_json(output / 'report.json', report)
        except Exception:
            fail('REPORT_SAVE_FAILED')

    try:
        save()
        write_json(output / 'plan.json', plan)
        write_json(output / 'document-map.json', frozen.mapping)
        budget = CapAuthorizationBudget(output=output, plan_sha256=fingerprint(plan), schedule=plan['schedule'])
        model = _runtime_model(cfg, budget)
        guarded = FrozenContextModel(model,
            {e['id']: e['question'].strip() for e in frozen.rubric['entries']}, plan['contexts'])
        await execute_answers(report, frozen, cfg, guarded, save)
    except asyncio.CancelledError as error:
        original_cancel = error
        report.update(status='cancelled', stopReason='CANCELLED')
        raise
    except Exception as error:
        report.update(status='stopped', stopReason='RAG_CAP_REPORT_SAVE_FAILED'
            if str(error) == 'RAG_CAP_REPORT_SAVE_FAILED' else 'RAG_CAP_EXECUTION_FAILED')
    finally:
        report['finishedAtUtc'] = now()
        await _finish_run(report, save, budget=budget, model=model, original_cancel=original_cancel,
                          save_failure_code='RAG_CAP_REPORT_SAVE_FAILED')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--approval', type=Path)
    args = parser.parse_args()
    if args.live:
        if args.output or not args.plan or not args.approval:
            parser.error('--live 要求 --plan 和 --approval；输出位置由批准的计划绑定')
        report = asyncio.run(run_live(args.plan, args.approval))
        print({'studyId': STUDY_ID, 'status': report['status'], 'callsReserved': report['callsReserved']})
        if report['status'] != 'complete':
            raise SystemExit(2)
    elif args.prepare:
        if not args.output or args.plan or args.approval:
            parser.error('--prepare 只接受新的 --output，生成 pending 请求')
        plan = asyncio.run(prepare(args.output))
        print({'studyId': STUDY_ID, 'mode': 'prepare', 'planSha256': fingerprint(plan),
               'maxChatCalls': 180, 'decision': 'pending', 'providerCalls': 0})
    else:
        if args.output or args.plan or args.approval:
            parser.error('默认离线校验不创建输出；准备计划需显式 --prepare')
        frozen = load_frozen_inputs(settings())
        contexts = asyncio.run(prepare_contexts(frozen, settings()))
        print({'studyId': STUDY_ID, 'mode': 'offline', 'cases': len(frozen.rubric['entries']),
            'contexts': len(contexts), 'providerCalls': 0, 'newEmbeddingCalls': 0,
            'newBgeCalls': 0, 'productionIndexWrites': 0, 'humanReview': 'pending'})


if __name__ == '__main__':
    main()
