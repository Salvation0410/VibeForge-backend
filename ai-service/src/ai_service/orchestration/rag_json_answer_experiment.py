"""冻结 cap1 的两种 HTTP 输出格式实验；默认离线且不生成批准。"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ai_service.orchestration.rag_json_answer_contracts import (
    ROOT, AUTHORIZATION, ARMS, STUDY_ID, fail, fingerprint, read_json, load_frozen_inputs,
    settings_manifest, implementation_manifest, code_diagnostics, evaluation_bindings,
    sdk_manifest, schedule, prepare_common, messages,
)
from ai_service.orchestration.rag_json_answer_budget import JsonAnswerAuthorizationBudget
from ai_service.orchestration.rag_rerank_budget import now, write_json
from ai_service.orchestration.rag_rerank_experiment import answer_once

TMP = ROOT / 'tmp'
PLAN_FIELDS = {'schemaVersion', 'studyId', 'createdAtUtc', 'sourceReportSha256', 'inputSha256',
    'sourceFilesSha256', 'sourceSnapshot', 'rubricSha256', 'mappingSha256', 'config', 'sdkVersions',
    'implementationFiles', 'preparationCode', 'evaluationBindings', 'schedule', 'contexts', 'messages',
    'liveOutput', 'maxChatCalls'}


def settings():
    from ai_service.config import Settings
    return Settings(_env_file=ROOT / 'ai-service/.env')


def _new_output(output):
    path = Path(output).resolve()
    if not path.is_relative_to(TMP.resolve()) or path == TMP.resolve() or path.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    return path


def bindings(frozen, cfg, output, contexts):
    return {'sourceReportSha256': frozen.source_report_sha256, 'inputSha256': fingerprint(frozen.common),
        'sourceFilesSha256': frozen.source_files, 'sourceSnapshot': frozen.report['snapshot'],
        'rubricSha256': fingerprint(frozen.rubric), 'mappingSha256': fingerprint(frozen.mapping),
        'config': settings_manifest(cfg), 'sdkVersions': sdk_manifest(),
        'implementationFiles': implementation_manifest(), 'evaluationBindings': evaluation_bindings(),
        'schedule': schedule(frozen.rubric['entries']), 'contexts': contexts,
        'messages': {common['id'] + ':' + arm: deepcopy(common['actualMessages'])
                     for common in frozen.common for arm in ARMS},
        'liveOutput': str(output), 'maxChatCalls': 180}


async def prepare(output):
    cfg = settings()
    frozen = load_frozen_inputs(cfg)
    contexts = await prepare_common(frozen, cfg)
    output = _new_output(output)
    plan = {'schemaVersion': 'rag-json-answer-plan/v1', 'studyId': STUDY_ID, 'createdAtUtc': now(),
            'preparationCode': code_diagnostics(), **bindings(frozen, cfg, output / 'live', contexts)}
    request = {'schemaVersion': 'rag-json-answer-authorization/v1', 'studyId': STUDY_ID,
               'decision': 'pending', 'planSha256': fingerprint(plan), 'maxChatCalls': 180,
               'liveOutput': plan['liveOutput']}
    output.mkdir(parents=True)
    write_json(output / 'plan.json', plan)
    write_json(output / 'authorization-request.json', request)
    return plan


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schemaVersion: Literal['rag-json-answer-authorization/v1']
    studyId: Literal['rag-json-answer-20261119-v1']
    decision: Literal['approved']
    planSha256: str = Field(pattern='^[a-f0-9]{64}$')
    maxChatCalls: StrictInt = Field(ge=180, le=180)
    liveOutput: str


class CodeDiagnostics(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    codeSha: str = Field(pattern='^[a-f0-9]{40}$')
    dirty: bool


def validate_approval(approval, plan):
    try:
        value = Approval.model_validate(approval)
        if value.planSha256 != fingerprint(plan) or value.liveOutput != plan.get('liveOutput'):
            fail('APPROVAL_INVALID')
    except Exception:
        fail('APPROVAL_INVALID')
    # 软件只校验结构绑定；真实执行仍须可信用户明确批准额度。
    return value.model_dump()


async def validate_plan(plan, cfg):
    if (type(plan) is not dict or set(plan) != PLAN_FIELDS
            or plan.get('schemaVersion') != 'rag-json-answer-plan/v1' or plan.get('studyId') != STUDY_ID
            or type(plan.get('maxChatCalls')) is not int or plan['maxChatCalls'] != 180):
        fail('PLAN_INVALID')
    try:
        CodeDiagnostics.model_validate(plan['preparationCode'])
        if (type(plan['createdAtUtc']) is not str or
                datetime.fromisoformat(plan['createdAtUtc']).utcoffset() != timezone.utc.utcoffset(None)):
            fail('PLAN_INVALID')
    except Exception:
        fail('PLAN_INVALID')
    frozen = load_frozen_inputs(cfg)
    output = _new_output(plan['liveOutput'])
    expected = bindings(frozen, cfg, output, await prepare_common(frozen, cfg))
    if any(fingerprint(plan[name]) != fingerprint(value) for name, value in expected.items()):
        fail('PLAN_CHANGED')
    return frozen


async def _runtime_model(cfg, budget, plan, *, transport=None):
    import httpx
    from langchain_openai import ChatOpenAI
    from langsmith import tracing_context
    from ai_service.models.openai_compatible import OpenAICompatibleModel
    from ai_service.orchestration.rag_json_answer_transport import ObservedTransport

    class Adapter(OpenAICompatibleModel):
        def __init__(self, fixed_cfg, client, boundary):
            # 不调用生产构造器，避免创建第二个 SDK 或覆盖后遗留客户端。
            self._settings, self._client, self.boundary = fixed_cfg, client, boundary
            self._customer_service_prompt_max_bytes = fixed_cfg.rag_prompt_max_bytes

        async def _invoke(self, incoming, *, response_format=None):
            self._check_budget(incoming)
            actual = [{'role': m.type, 'content': m.content} for m in incoming]
            expected_mode = self._settings.rag_answer_response_format
            if (actual != self.boundary.expected_messages[self.boundary.key]
                    or response_format != ('json_object' if expected_mode == 'json_object' else None)):
                fail('MODEL_MESSAGES_CHANGED')
            started = time.perf_counter()
            try:
                with tracing_context(enabled=False):
                    kwargs = {'response_format': {'type': 'json_object'}} if response_format else {}
                    return await self._client.ainvoke(incoming, **kwargs)
            except BaseException:
                if self.boundary.cancelled_error is not None:
                    raise self.boundary.cancelled_error
                raise
            finally:
                self.boundary.observation['answerLatencyMs'] = round((time.perf_counter() - started) * 1000, 3)

    class Runtime:
        invalid_context = False
        pre_send_failure = False

        def __init__(self, client, boundary, http_client):
            self._client, self.boundary, self.http_client = client, boundary, http_client
            self.adapters = {arm: Adapter(cfg.model_copy(update={'rag_answer_response_format': arm}),
                                          client, boundary) for arm in ARMS}
            self.closed = False

        @property
        def observation(self):
            return self.boundary.observation

        def begin(self, key):
            self.boundary.begin(key)
            self.key = key

        async def answer_customer_service(self, question, contexts):
            expected = plan['contexts'][self.key]
            actual = [{'chunkId': c.chunk_id, 'content': c.content} for c in contexts]
            if (self.invalid_context or actual != [{'chunkId': c['chunkId'], 'content': c['content']} for c in expected]
                    or messages(question, expected) != plan['messages'][self.key]):
                self.invalid_context = True
                fail('CONTEXT_MISMATCH')
            try:
                return await self.adapters[self.key.rsplit(':', 1)[1]].answer_customer_service(question, contexts)
            except BaseException:
                if not self.observation.get('attemptedSend'):
                    self.pre_send_failure = True
                raise

        async def close(self):
            if self.closed:
                return []
            self.closed = True
            errors, cancellation = [], None
            for label, closer, asynchronous in (
                ('sdk_async', self._client.root_async_client.close, True),
                ('sdk_sync', self._client.root_client.close, False),
                ('http_async', self.http_client.aclose, True)):
                try:
                    if label == 'http_async' and self.http_client.is_closed:
                        continue
                    if asynchronous:
                        await closer()
                    else:
                        closer()
                except asyncio.CancelledError as error:
                    cancellation = cancellation or error
                    errors.append(label)
                except Exception:
                    errors.append(label)
            if cancellation:
                raise cancellation
            return errors

    boundary = ObservedTransport(transport or httpx.AsyncHTTPTransport(retries=0), cfg, budget, plan['messages'])
    http_client = httpx.AsyncClient(transport=boundary)
    try:
        client = ChatOpenAI(api_key=cfg.model_api_key, base_url=cfg.model_base_url, model=cfg.model_name,
            temperature=cfg.model_temperature, max_tokens=cfg.model_max_tokens, max_retries=0,
            timeout=cfg.rag_answer_timeout_seconds, http_async_client=http_client)
    except BaseException:
        # 构造失败仍先关闭已创建的 HTTP 资源；原构造错误不能被清理错误覆盖。
        try:
            await http_client.aclose()
        except BaseException:
            pass
        raise
    return Runtime(client, boundary, http_client)


def new_report(frozen, plan, approval):
    return {'schemaVersion': 'rag-json-answer-experiment/v1', 'studyId': STUDY_ID,
        'rubricSha256': fingerprint(frozen.rubric), 'status': 'preparing', 'startedAtUtc': now(),
        'humanReview': 'pending', 'productionWrites': 0, 'automaticRetries': 0, 'concurrency': 1,
        'onlineE2ETested': False, 'currentAuthoritativeSnapshotChecked': False,
        'sourceClaim': 'historical-frozen-publication-only', 'newEmbeddingCalls': 0, 'newBgeCalls': 0,
        'productionIndexWrites': 0, 'planSha256': fingerprint(plan), 'approval': approval,
        **{name: deepcopy(plan[name]) for name in ('sourceReportSha256', 'sourceFilesSha256', 'sourceSnapshot',
             'config', 'sdkVersions', 'implementationFiles', 'evaluationBindings')},
        'results': [{'id': key.rsplit(':', 1)[0], 'arm': key.rsplit(':', 1)[1], 'status': 'not_run',
                     'contexts': [], 'answered': None, 'answer': None, 'sources': [], 'citedChunkIds': [],
                     'reserved': False, 'attemptedSend': False, 'providerCalled': False} for key in plan['schedule']]}


def format_summary(report):
    return {arm: {'denominator': 90,
        'modelParseFailed': sum(r.get('errorCode') == 'MODEL_PARSE_FAILED' for r in rows),
        'callErrors': sum(r['status'] == 'error' and r.get('errorCode') != 'MODEL_PARSE_FAILED' for r in rows),
        'answered': sum(r['status'] == 'ok' and r['answered'] is True for r in rows),
        'refused': sum(r['status'] == 'ok' and r['answered'] is False for r in rows),
        'notRun': sum(r['status'] == 'not_run' for r in rows),
        'reserved': sum(r.get('reserved') is True for r in rows),
        'attemptedSend': sum(r.get('attemptedSend') is True for r in rows)}
        for arm in ARMS for rows in [[r for r in report['results'] if r['arm'] == arm]]}


async def execute_answers(report, frozen, cfg, model, save):
    entries = {e['id']: e for e in frozen.rubric['entries']}
    report['status'], streak = 'answering', 0
    save()
    for index, placeholder in enumerate(report['results']):
        entry = entries[placeholder['id']]
        arm = placeholder['arm']
        try:
            model.begin(entry['id'] + ':' + arm)
            cached = frozen.prepared[entry['id']]
            row = await answer_once(cfg, entry, 'rerank_on', frozen.vectors[entry['id']],
                cached['retrieved'], cached['filtered'], cached['ranked'], model)
            row.update(deepcopy(model.observation), arm=arm)
            report['results'][index] = row
            save()
            streak = streak + 1 if row['status'] == 'error' else 0
            if (model.invalid_context or model.pre_send_failure or
                    (row['status'] == 'error' and not row.get('attemptedSend')) or row.get('httpStatus') == 400
                    or model.observation.get('observationError') or streak >= 3):
                reason = ('HTTP_400' if row.get('httpStatus') == 400 else 'PRE_SEND_FAILED'
                          if model.pre_send_failure or not row.get('attemptedSend') else
                          model.observation.get('observationError') or 'THREE_CONSECUTIVE_CALL_FAILURES')
                report.update(status='stopped', stopReason=reason)
                break
        except asyncio.CancelledError:
            report['results'][index].update(status='error', errorCode='CANCELLED', **deepcopy(model.observation))
            report.update(status='cancelled', stopReason='CANCELLED')
            try:
                save()
            except BaseException:
                report.setdefault('reportSaveFailures', []).append('cancelled_result')
            raise
        except Exception:
            report.update(status='stopped', stopReason='EXECUTION_OR_REPORT_SAVE_FAILED')
            raise
    else:
        report['status'] = 'complete'
    save()


async def run_live(plan_path, approval_path):
    plan = read_json(plan_path)
    approval = validate_approval(read_json(approval_path), plan)
    cfg = settings()
    frozen = await validate_plan(plan, cfg)
    output = _new_output(plan['liveOutput'])
    if AUTHORIZATION.exists():
        fail('AUTHORIZATION_ALREADY_USED')
    report = new_report(frozen, plan, approval)
    output.mkdir(parents=True)
    budget = model = None
    original = None

    def save():
        report['callsReserved'] = dict(budget.counts) if budget else {'chat': 0, 'embeddingRequests': 0, 'queryInputs': 0}
        report['formatSummary'] = format_summary(report)
        write_json(output / 'report.json', report)

    try:
        save()
        write_json(output / 'plan.json', plan)
        write_json(output / 'document-map.json', frozen.mapping)
        budget = JsonAnswerAuthorizationBudget(output=output, plan_sha256=fingerprint(plan), schedule=plan['schedule'])
        model = await _runtime_model(cfg, budget, plan)
        await execute_answers(report, frozen, cfg, model, save)
    except BaseException as error:
        original = error
        report.update(status='cancelled' if isinstance(error, asyncio.CancelledError) else 'stopped',
                      stopReason='CANCELLED' if isinstance(error, asyncio.CancelledError) else 'EXECUTION_FAILED')
        raise
    finally:
        # 清理逐项独立，原始失败/取消优先，不因最后报告写回覆盖。
        cleanup_cancel = None
        if budget is not None:
            try:
                budget.close()
            except asyncio.CancelledError as error:
                cleanup_cancel = error
                report.setdefault('cleanupErrors', []).append('budget')
            except BaseException:
                report.setdefault('cleanupErrors', []).append('budget')
        if model is not None:
            try:
                report.setdefault('cleanupErrors', []).extend(await model.close())
            except asyncio.CancelledError as error:
                cleanup_cancel = cleanup_cancel or error
            except Exception:
                report.setdefault('cleanupErrors', []).append('model')
        report['finishedAtUtc'] = now()
        if cleanup_cancel is not None:
            report.update(status='cancelled', stopReason='CANCELLED')
        try:
            save()
        except BaseException:
            report.setdefault('reportSaveFailures', []).append('final_save')
            if original is None and cleanup_cancel is None:
                raise
        if original is None and cleanup_cancel is not None:
            raise cleanup_cancel
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
    if args.live and args.plan and args.approval and not args.output:
        result = asyncio.run(run_live(args.plan, args.approval))
        print({'studyId': STUDY_ID, 'status': result['status'], 'callsReserved': result['callsReserved']})
    elif args.prepare and args.output and not args.plan and not args.approval:
        plan = asyncio.run(prepare(args.output))
        print({'studyId': STUDY_ID, 'decision': 'pending', 'planSha256': fingerprint(plan), 'providerCalls': 0})
    elif not any((args.live, args.prepare, args.output, args.plan, args.approval)):
        frozen = load_frozen_inputs(settings())
        asyncio.run(prepare_common(frozen, settings()))
        print({'studyId': STUDY_ID, 'mode': 'offline', 'providerCalls': 0, 'cases': 90})
    else:
        parser.error('准备只接受新 --output；真实执行只接受 --plan 与 --approval')


if __name__ == '__main__':
    main()
