"""默认离线校验；显式--live后执行有额度的公共正文快照实验，不写Milvus。"""
from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

from ai_service.orchestration.rag_evaluation_workflow import (
    DEFAULT_DATASET, ROOT, aggregate, analyze_study, bounded_contexts, evidence_metrics,
    fingerprint, load_study, select_contexts, standard_samples,
)


class CallBudget:
    """单运行独占账本，发送前fsync预占；失败/取消不返还，不自动重试。"""

    def __init__(self, path: Path, *, max_chat: int, max_embedding: int = 8):
        if not 0 <= max_chat <= 160 or not 1 <= max_embedding <= 8:
            raise ValueError('RAG_EVAL_BUDGET_INVALID')
        self.path = path
        self.limits = {'chat': max_chat, 'embedding': max_embedding}
        self.counts = dict.fromkeys(self.limits, 0)
        # 拒绝复用旧账本，防止覆盖历史调用记录；整次重跑的成本须单独纳入授权。
        with path.open('x', encoding='utf8') as stream:
            stream.write(json.dumps({'type': 'limits', **self.limits}) + '\n')
            stream.flush(); os.fsync(stream.fileno())

    def reserve(self, kind: str, key: str):
        if kind not in self.limits or self.counts[kind] >= self.limits[kind]:
            raise ValueError('RAG_EVAL_CALL_BUDGET_EXCEEDED')
        with self.path.open('a', encoding='utf8') as stream:
            stream.write(json.dumps({'type': 'reserved', 'kind': kind, 'key': key}) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        self.counts[kind] += 1


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / 'tmp').resolve()) or resolved.exists():
        raise ValueError('RAG_EVAL_OUTPUT_MUST_BE_NEW_UNDER_TMP')
    return resolved


def error_code(error: Exception) -> str:
    # 禁止将供应商异常正文、地址、令牌写入评估报告。
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return 'TIMEOUT'
    if isinstance(error, ValueError) and re.fullmatch(r'RAG_EVAL_[A-Z_]+', str(error)):
        return str(error)
    return 'EVALUATION_CALL_FAILED'


async def run_live(data: dict, chunks: list[dict], output: Path, *, max_chat: int,
                   judge: bool, case_ids: list[str] | None = None,
                   judge_case_limit: int = 12) -> dict:
    import httpx
    import numpy as np
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from langsmith import tracing_context
    from openai import AsyncOpenAI
    from ai_service.config import Settings
    from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
    from ai_service.models.base import CustomerServiceContext
    from ai_service.models.openai_compatible import OpenAICompatibleModel
    from ai_service.models.reranker import LocalCrossEncoderReranker
    from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT
    from ai_service.orchestration.rag_evaluation_judge import (
        JUDGE_PROMPT, judge_payload, parse_judgment,
    )

    cfg = Settings(_env_file=ROOT / 'ai-service/.env')
    # 此入口的基线固定为单文档单块，不能继承正式服务的可选配额。
    if (cfg.rag_retrieval_top_k != 8 or cfg.rag_final_top_k != 3
            or cfg.rag_document_chunk_limit != 1
            or cfg.rag_min_rerank_score is not None
            or cfg.rag_reranker_provider != 'local_cross_encoder'):
        raise ValueError('RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG')
    entries = data['entries']
    if case_ids:
        if set(case_ids) - {e['id'] for e in entries}:
            raise ValueError('RAG_EVAL_UNKNOWN_CASE')
        entries = [e for e in entries if e['id'] in case_ids]
    if not 1 <= judge_case_limit <= 48:
        raise ValueError('RAG_EVAL_JUDGE_LIMIT_INVALID')
    judge_count = min(len(entries), judge_case_limit) if judge else 0
    # 发送前确定等距诊断抽样，两组评同一批；不是全量语义成绩或随机盲测。
    judge_ids = {entries[round(i*(len(entries)-1)/max(1,judge_count-1))]['id']
                 for i in range(judge_count)}
    required = 2 * (len(entries) + len(judge_ids))
    if required > max_chat:
        raise ValueError('RAG_EVAL_BUDGET_TOO_SMALL')
    experiment_data = {**data, 'entries': entries}
    output.mkdir(parents=True, exist_ok=False)
    budget = CallBudget(output / 'calls.jsonl', max_chat=max_chat)
    report = {'schemaVersion': 'rag-snapshot-experiment/v1', 'mode': 'live-exact-cosine-snapshot',
              'startedAtUtc': datetime.now(timezone.utc).isoformat(),
              'implementationSha256': fingerprint({name: (Path(__file__).parent/name).read_text(encoding='utf8')
                  for name in ('rag_snapshot_experiment.py', 'rag_evaluation_workflow.py', 'rag_evaluation_judge.py')}),
              'datasetVersion': data['datasetVersion'], 'datasetSha256': fingerprint(data),
              'corpusSha256': fingerprint(data['corpusHashes']), 'chunkCount': len(chunks),
              'expectedCaseIds': [e['id'] for e in entries],
              'judgeExpectedCaseIds': sorted(judge_ids),
              'diagnosticSubset': len(entries) != len(data['entries']),
              'productionMilvusTested': False, 'productionIndexWrites': 0,
              'humanReview': 'pending', 'cases': [], 'embeddingUsage': [],
              'config': {'embeddingModel': cfg.rag_embedding_model,
                         'embeddingDimension': cfg.rag_embedding_dimension,
                         'reranker': cfg.rag_reranker_model, 'device': cfg.rag_reranker_device,
                         'model': cfg.model_name, 'temperature': cfg.model_temperature,
                         'modelMaxTokens': cfg.model_max_tokens,
                         'retrievalTopK': 8, 'finalTopK': 3,
                         'promptMaxBytes': cfg.rag_prompt_max_bytes,
                         'answerPromptSha256': hashlib.sha256(CUSTOMER_SERVICE_SYSTEM_PROMPT.encode()).hexdigest(),
                         'judgePromptSha256': hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest() if judge else None,
                         'judgeSameModelAsAnswer': judge,
                         'maxChatCalls': max_chat, 'automaticRetries': 0},
              'status': 'running'}
    def save():
        report['callsReserved'] = dict(budget.counts)
        report['metrics'] = aggregate(experiment_data, report['cases'])
        temporary = output / 'report.partial.json'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
        temporary.replace(output / 'report.json')
    save()
    usage: ContextVar[dict] = ContextVar('rag_eval_usage', default={})
    class MeteredModel(OpenAICompatibleModel):
        async def _invoke(self, messages):
            self._check_budget(messages)
            with tracing_context(enabled=False):
                response = await self._client.ainvoke(messages)
            usage.set(response.usage_metadata or {})
            return response
    reranker = embedding_client = model = judge_model = None
    try:
        # 先确保GPU可用，再消耗Embedding或回答额度；失败不得静默改为无重排实验。
        reranker = await LocalCrossEncoderReranker.create(cfg)
        model = MeteredModel(cfg)
        model._client = ChatOpenAI(api_key=cfg.model_api_key, base_url=cfg.model_base_url,
                                   model=cfg.model_name, temperature=cfg.model_temperature,
                                   max_tokens=cfg.model_max_tokens, max_retries=0,
                                   timeout=cfg.rag_answer_timeout_seconds)
        if judge:
            judge_model = ChatOpenAI(api_key=cfg.model_api_key, base_url=cfg.model_base_url,
                                     model=cfg.model_name, temperature=0, max_tokens=3072,
                                     max_retries=0, timeout=cfg.rag_answer_timeout_seconds)
        embedding_client = AsyncOpenAI(api_key=cfg.closeai_api_key,
                                        base_url=cfg.closeai_base_url, max_retries=0,
                                        http_client=httpx.AsyncClient(trust_env=False, timeout=60))
        texts = [c['content'] for c in chunks] + [e['question'] for e in entries]
        vectors = []
        for start in range(0, len(texts), 32):
            budget.reserve('embedding', f'batch-{start//32}')
            save()
            response = await embedding_client.embeddings.create(
                model=cfg.rag_embedding_model.removeprefix('openai:'), input=texts[start:start+32])
            rows = sorted(response.data, key=lambda item: item.index)
            if len(rows) != len(texts[start:start+32]):
                raise ValueError('RAG_EVAL_EMBEDDING_COUNT_INVALID')
            vectors.extend(item.embedding for item in rows)
            report['embeddingUsage'].append(response.usage.model_dump())
        matrix = np.asarray(vectors, dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1)
        if (matrix.shape != (len(texts), cfg.rag_embedding_dimension)
                or not np.isfinite(matrix).all() or (norms == 0).any()):
            raise ValueError('RAG_EVAL_EMBEDDING_INVALID')
        matrix /= norms[:, None]
        report['vectorSnapshotSha256'] = hashlib.sha256(matrix.tobytes()).hexdigest()
        # 保存只读向量快照以复查ANN以外的因素，不上传公共报告。
        np.savez_compressed(output/'vectors.npz', vectors=matrix)
        gate = asyncio.Semaphore(2)
        tasks = []
        failure_streak = 0
        async def answer_case(entry, arm, selected, metrics, retrieval_ms, rerank_ms):
            nonlocal failure_streak
            result = {'id': entry['id'], 'groupId': entry['groupId'], 'arm': arm,
                      'status': 'not_run', 'contexts': selected, 'metrics': metrics,
                      'retrievalLatencyMs': retrieval_ms, 'rerankLatencyMs': rerank_ms}
            async with gate:
                if failure_streak >= 3:
                    raise RuntimeError('RAG_EVAL_REPEATED_CALL_FAILURE')
                started = time.perf_counter()
                try:
                    budget.reserve('chat', entry['id'] + ':' + arm + ':answer')
                    save()
                    usage.set({})
                    answer = await model.answer_customer_service(entry['question'], [
                        CustomerServiceContext(r['chunkId'], r['content']) for r in selected])
                    result.update(status='ok', answered=answer.answered, answer=answer.answer,
                                  citedChunkIds=list(answer.cited_chunk_ids),
                                  answerUsage=usage.get())
                    failure_streak = 0
                except Exception as error:
                    failure_streak += 1
                    result.update(status='error', errorCode=error_code(error))
                result['answerLatencyMs'] = (time.perf_counter()-started)*1000
                if entry['id'] in judge_ids and result['status'] == 'ok':
                    try:
                        budget.reserve('chat', entry['id'] + ':' + arm + ':judge')
                        save()
                        with tracing_context(enabled=False):
                            response = await judge_model.ainvoke([
                                SystemMessage(content=JUDGE_PROMPT),
                                HumanMessage(content=json.dumps(judge_payload(entry, result), ensure_ascii=False))])
                        result['judgeUsage'] = response.usage_metadata or {}
                        result['judge'] = parse_judgment(str(response.content), entry, result)
                    except Exception as error:
                        result['judge'] = {'status': 'error', 'errorCode': error_code(error)}
                report['cases'].append(result)
                save()
                print(json.dumps({'completedArms': len(report['cases']), 'totalArms': 2*len(entries),
                                  'chatCalls': budget.counts['chat'], 'status': result['status']}), flush=True)
        for index, entry in enumerate(entries):
            start = time.perf_counter()
            scores = matrix[:len(chunks)] @ matrix[len(chunks)+index]
            order = np.argsort(-scores, kind='stable')[:8]
            retrieved = [{**chunks[int(i)], 'score': float(scores[int(i)])} for i in order]
            retrieval_ms = (time.perf_counter()-start)*1000
            candidates = [RetrievedChunk(chunk_id=r['chunkId'], document_id=r['documentId'],
                          document_version=1, chunk_index=i, content=r['content'],
                          file_name=r['documentId']+'.md', file_type='md', source_locator=r['locator'],
                          content_hash=hashlib.sha256(r['content'].encode()).hexdigest(),
                          distance=r['score'], score=r['score'], is_active=True,
                          current_document_version=1) for i,r in enumerate(retrieved)]
            start = time.perf_counter()
            ranked = await reranker.rerank(entry['question'], candidates, top_n=len(candidates))
            rerank_ms = (time.perf_counter()-start)*1000
            by_id = {r['chunkId']: r for r in retrieved}
            ranked_rows = [{**by_id[r.chunk.chunk_id], 'rerankScore': r.score} for r in ranked]
            for arm, maximum in [('baseline',1), ('two_chunks',2)]:
                selected = select_contexts(ranked_rows, per_document=maximum)
                final = bounded_contexts(entry['question'], selected, cfg.rag_prompt_max_bytes)
                stages = {'retrieved8': (retrieved,8), 'reranked3': (ranked_rows,3),
                          'selected3': (selected,3), 'final3': (final,3)}
                metrics = {key: evidence_metrics(entry, rows, k=k, corpus_chunks=chunks)
                           for key,(rows,k) in stages.items()}
                tasks.append(asyncio.create_task(answer_case(entry, arm, final, metrics, retrieval_ms, rerank_ms)))
            # 保存共享候选，既可定位阶段损失，也可用同一向量快照复算选择策略。
            with (output/'retrieval.jsonl').open('a',encoding='utf8') as stream:
                stream.write(json.dumps({'id':entry['id'],'retrieved':retrieved,'reranked':ranked_rows},ensure_ascii=False)+'\n')
        await asyncio.gather(*tasks)
        report['status'] = 'completed'
    except BaseException as error:
        report['status'] = 'failed-or-interrupted'
        report['failureReason'] = error_code(error) if isinstance(error,Exception) else 'INTERRUPTED'
        # 中断时先排空/取消本运行任务，避免报告完成后仍产生未记录调用。
        for task in locals().get('tasks', []):
            if not task.done(): task.cancel()
        if locals().get('tasks'):
            await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        report['finishedAtUtc'] = datetime.now(timezone.utc).isoformat()
        save()
        if embedding_client is not None: await embedding_client.close()
        if reranker is not None: await reranker.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--analyze', type=Path, help='只读重算已有快照报告并导出标准单轮字段')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--judge', action='store_true')
    parser.add_argument('--judge-case-limit', type=int, default=12)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-chat-calls', type=int, default=0)
    parser.add_argument('--case-ids', nargs='+')
    args = parser.parse_args()
    data, chunks = load_study(args.dataset)
    if args.analyze:
        source = args.analyze.resolve()
        if not source.is_relative_to((ROOT/'tmp').resolve()):
            parser.error('--analyze input must be under tmp')
        report = json.loads(source.read_text(encoding='utf8'))
        if report.get('datasetSha256') != fingerprint(data):
            parser.error('dataset fingerprint mismatch')
        expected = report.get('expectedCaseIds', [])
        if not expected or set(expected) - {e['id'] for e in data['entries']}:
            parser.error('invalid experiment case ids')
        selected = {**data, 'entries': [e for e in data['entries'] if e['id'] in expected]}
        analysis = analyze_study(selected, report['cases'])
        (source.parent/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        samples = standard_samples(selected,report['cases'])
        (source.parent/'samples.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in samples),encoding='utf8')
        print(json.dumps({'mode':'offline-analysis','samples':len(samples),
                          'pairedChanges':analysis['pairedChanges'],'modelCalls':0},ensure_ascii=False))
        return
    if not args.live:
        print(json.dumps({'mode':'offline-validation','cases':len(data['entries']),
                          'facts':sum(len(e['requiredFacts']) for e in data['entries']),
                          'chunks':len(chunks),'datasetSha256':fingerprint(data),
                          'modelCalls':0,'semanticMetrics':None}))
        return
    if args.output is None:
        parser.error('--live requires --output')
    output = safe_output(args.output)
    try:
        report = asyncio.run(run_live(data,chunks,output,max_chat=args.max_chat_calls,
                                       judge=args.judge,case_ids=args.case_ids,
                                       judge_case_limit=args.judge_case_limit))
    except Exception:
        print(json.dumps({'status':'failed','reason':'RAG_EVAL_RUN_FAILED','output':str(output)}))
        raise SystemExit(1) from None
    print(json.dumps({'status':report['status'],'callsReserved':report['callsReserved'],
                      'metrics':report['metrics']},ensure_ascii=False))


if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
