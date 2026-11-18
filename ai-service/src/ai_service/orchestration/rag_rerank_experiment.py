"""固定 90 题的重排开关对照；默认离线，只有 --live 可以消费本轮唯一授权。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import math
import os
from pathlib import Path
import re
import time

from ai_service.orchestration.rag_rerank_budget import AuthorizationBudget, now, write_json
from ai_service.orchestration.rag_rerank_contracts import (
    ARMS, AUTHORIZATION, HF_REVISION, PRODUCT, ROOT, STUDY_ID, chunk_json, encoded,
    fail, file_hash, fingerprint, load_mapping, load_rubric, load_special, source_manifest,
    verify_corpus,
)


def schedule(entries):
    return [(entry, arm) for index, entry in enumerate(entries)
            for arm in (ARMS if index % 2 == 0 else ARMS[::-1])]


def new_report(rubric):
    return {'schemaVersion': 'rag-rerank-experiment/v1', 'studyId': STUDY_ID,
        'rubricSha256': fingerprint(rubric), 'startedAtUtc': now(), 'status': 'preparing',
        'humanReview': 'pending', 'productionWrites': 0, 'automaticRetries': 0,
        'concurrency': 1, 'onlineE2ETested': False, 'results': [
            {'id': entry['id'], 'arm': arm, 'status': 'not_run', 'contexts': [],
             'answered': None, 'answer': None, 'sources': [], 'citedChunkIds': []}
            for entry, arm in schedule(rubric['entries'])], 'retrieval': {}, 'embeddingUsage': []}


def code(error):
    value = getattr(error, 'code', str(error))
    if isinstance(value, str) and re.fullmatch(r'(RAG_RERANK|CUSTOMER_SERVICE|KNOWLEDGE)_[A-Z_]+', value):
        return value
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return 'TIMEOUT'
    return 'RAG_RERANK_EXECUTION_FAILED'


def context_json(contexts, chunks):
    return [{'chunkId': c.chunk_id, 'documentId': chunks[c.chunk_id].document_id,
             'documentVersion': chunks[c.chunk_id].document_version,
             'locator': chunks[c.chunk_id].source_locator, 'content': c.content} for c in contexts]


class CachedEmbeddings:
    def __init__(self, question, vector):
        self.question, self.vector = question, vector

    async def embed_query(self, question):
        if question != self.question:
            fail('CACHED_QUERY_MISMATCH')
        return list(self.vector)


class FrozenStore:
    """相同候选只读回放，同时保留正式模型前后的权威快照复核。"""
    def __init__(self, vector, candidates, *, snapshot=None, client=None):
        self.vector_hash, self.candidates = fingerprint(vector), candidates
        self.snapshot, self.client = snapshot, client
        self.invalid = False

    @property
    def empty(self):
        return False

    async def assert_current(self):
        if self.client is not None:
            try:
                if await self.client.snapshot() != self.snapshot:
                    fail('SNAPSHOT_CHANGED')
            except BaseException:
                self.invalid = True
                raise

    @asynccontextmanager
    async def read_scope(self):
        await self.assert_current()
        yield self

    async def search(self, vector, limit):
        if limit != 8 or fingerprint(vector) != self.vector_hash:
            fail('FROZEN_RETRIEVAL_MISMATCH')
        return list(self.candidates)


class FixedReranker:
    def __init__(self, question, candidates, ranked):
        self.question = question
        self.candidates = fingerprint([chunk_json(c) for c in candidates])
        self.ranked, self.calls = ranked, 0

    async def rerank(self, question, chunks, *, top_n):
        self.calls += 1
        if (question != self.question or top_n != len(chunks)
                or fingerprint([chunk_json(c) for c in chunks]) != self.candidates):
            fail('FROZEN_RERANK_MISMATCH')
        return list(self.ranked)


class ForbiddenReranker:
    def __init__(self):
        self.calls = 0

    async def rerank(self, *args, **kwargs):
        self.calls += 1
        fail('OFF_CALLED_RERANKER')


def snippet_survival(entry, stages, logical_by_actual):
    """整段引文存活只作可追踪代理；片段不完整不能自动推出必要事实丢失。"""
    from ai_service.orchestration.customer_service_rag import _clean_text
    result = {}
    for name, contexts in stages.items():
        matches = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            survived = any(logical_by_actual.get(c['documentId']) == ref['documentId']
                and _clean_text(ref['quote'], len(ref['quote']) + 1) in
                    _clean_text(c['content'], len(c['content']) + 1)
                for c in contexts for ref in fact['evidence'])
            matches.append({'factId': fact['factId'], 'annotatedSupportSnippetSurvived': survived})
        result[name] = matches
    return {'meaning': '整段标注支持片段存活代理，非语义事实覆盖；未存活不证明必要事实丢失',
            'stages': result}


async def answer_once(settings, entry, arm, vector, retrieved, filtered, ranked, model,
                      *, snapshot=None, client=None):
    from ai_service.orchestration.customer_service_rag import CustomerServiceRagService
    from ai_service.orchestration.customer_service_trace import CustomerServiceRagTrace

    cfg = settings.model_copy(update={'rag_reranker_provider':
                              'disabled' if arm == ARMS[0] else 'local_cross_encoder'})
    reranker = ForbiddenReranker() if arm == ARMS[0] else FixedReranker(entry['question'].strip(), filtered, ranked)
    store = FrozenStore(vector, retrieved, snapshot=snapshot, client=client)
    chunks = {c.chunk_id: c for c in retrieved}
    class CapturingTrace(CustomerServiceRagTrace):
        def __init__(self):
            super().__init__('rerank-experiment', cfg)
            self.contents = {}

        def record(self, stage, items, **kwargs):
            super().record(stage, items, **kwargs)
            kept = kwargs.get('kept_ids')
            values = [getattr(item, 'chunk', item) for item in items]
            values = [c for c in values if kept is None or c.chunk_id in kept]
            from ai_service.models.base import CustomerServiceContext
            self.contents[stage] = context_json([CustomerServiceContext(c.chunk_id, c.content)
                                                for c in values], chunks)

        def contexts(self, stage, before, after, original):
            super().contexts(stage, before, after, original)
            self.contents[stage] = context_json(after, chunks)

    trace = CapturingTrace()
    service = CustomerServiceRagService(cfg, CachedEmbeddings(entry['question'].strip(), vector), store, reranker, model)
    started = time.perf_counter()
    answer = await service.answer(entry['question'], trace=trace)
    diagnostic = trace.snapshot()
    terminal = diagnostic['terminal']
    row = {'id': entry['id'], 'arm': arm,
        'status': 'error' if terminal['outcome'] == 'service_error' or store.invalid else 'ok',
        'errorCode': 'RAG_RERANK_SNAPSHOT_CHANGED_OR_UNAVAILABLE' if store.invalid else
                     terminal['reasonCode'] if terminal['outcome'] == 'service_error' else None,
        'answered': answer.answered, 'answer': answer.answer,
        'sources': [asdict(source) for source in answer.sources],
        'citedChunkIds': [source.chunk_id for source in answer.sources],
        'contexts': trace.contents.get('byteBudget', []), 'stages': trace.contents,
        'trace': diagnostic, 'degraded': answer.degraded,
        'replayServiceLatencyMs': round((time.perf_counter() - started) * 1000, 3),
        'rerankerReplayCalls': reranker.calls, 'snapshotInvalid': store.invalid}
    if arm == ARMS[0] and reranker.calls:
        fail('OFF_CALLED_RERANKER')
    if arm == ARMS[1] and (answer.degraded or (filtered and reranker.calls != 1)):
        row.update(status='error', errorCode='RAG_RERANK_ON_DEGRADED')
    return row


async def execute_answers(report, rubric, settings, prepared, vectors, model, save,
                          *, snapshot=None, client=None, logical_by_actual=None):
    """预先存在全部占位；顺序调用和三连失败使故障不会继续消耗剩余额度。"""
    entries = {e['id']: e for e in rubric['entries']}
    streak = 0
    report['status'] = 'answering'
    save()
    for index, placeholder in enumerate(report['results']):
        entry, arm = entries[placeholder['id']], placeholder['arm']
        model.begin(entry['id'] + ':' + arm)
        cached = prepared[entry['id']]
        try:
            row = await answer_once(settings, entry, arm, vectors[entry['id']],
                cached['retrieved'], cached['filtered'], cached['ranked'], model,
                snapshot=snapshot, client=client)
            row.update(model.observation)
            if logical_by_actual is not None:
                row['supportSnippetDiagnostic'] = snippet_survival(entry, row['stages'], logical_by_actual)
            report['results'][index] = row
            if model.observation.get('providerCalled'):
                streak = streak + 1 if row['status'] == 'error' else 0
            save()
            if row['snapshotInvalid'] or streak >= 3:
                report.update(status='stopped', stopReason='SNAPSHOT_CHANGED_OR_UNAVAILABLE'
                              if row['snapshotInvalid'] else 'THREE_CONSECUTIVE_CALL_FAILURES')
                break
        except asyncio.CancelledError:
            placeholder.update(status='error', errorCode='CANCELLED', **model.observation)
            report.update(status='cancelled', stopReason='CANCELLED')
            save()
            raise
        except Exception as error:
            placeholder.update(status='error', errorCode=code(error), **model.observation)
            report.update(status='stopped', stopReason=code(error))
            save()
            break
    else:
        report['status'] = 'complete'
    save()


async def _finish_run(report, save, *, budget=None, resources=(), model=None,
                      gpu_owner=None, original_cancel=None):
    """报告写盘与逐项清理解耦；这里只保证尝试，不宣称失败资源已强制关闭。"""
    cleanup_cancel = None

    def remember_cancel(error, label):
        nonlocal cleanup_cancel
        if cleanup_cancel is None:
            cleanup_cancel = error
        report.setdefault('cleanupErrors', []).append(label)
        report.update(status='cancelled', stopReason='CANCELLED')

    def final_save(label):
        try:
            save()
        except asyncio.CancelledError as error:
            remember_cancel(error, label)
        except Exception:
            report.setdefault('reportSaveFailures', []).append(label)
            if report['status'] == 'complete':
                report.update(status='stopped', stopReason='RAG_RERANK_REPORT_SAVE_FAILED')

    def close_sync(label, closer):
        try:
            closer()
        except asyncio.CancelledError as error:
            remember_cancel(error, label)
        except Exception:
            report.setdefault('cleanupErrors', []).append(label)

    async def close_async(label, closer):
        try:
            await closer()
        except asyncio.CancelledError as error:
            remember_cancel(error, label)
        except Exception:
            report.setdefault('cleanupErrors', []).append(label)

    # 外层写盘和任一 closer 均不能跳过更内层 finally 的剩余清理及 GPU 所有权释放。
    try:
        final_save('before_cleanup')
    finally:
        try:
            if budget is not None:
                close_sync('budget', lambda: budget.close())
            for label, resource in resources:
                if resource is not None:
                    await close_async(label, lambda: resource.close())
            if model is not None:
                await close_async('answer_model_async', lambda: model._client.root_async_client.close())
                close_sync('answer_model_sync', lambda: model._client.root_client.close())
        finally:
            if gpu_owner is not None:
                close_sync('gpu_owner', lambda: gpu_owner.release())
            final_save('after_cleanup')
    # 已在传播的原取消由调用方原样重抛，收尾新收到的取消也不能被观察失败吞掉。
    if original_cancel is None and cleanup_cancel is not None:
        raise cleanup_cancel


def _runtime_model(settings, budget):
    from langchain_openai import ChatOpenAI
    from langsmith import tracing_context
    from ai_service.models.openai_compatible import OpenAICompatibleModel
    class MeteredModel(OpenAICompatibleModel):
        def __init__(self, settings):
            # 基类当前只依赖这三项；评测直接建立唯一零重试客户端，避免先构造再覆盖泄漏。
            self._settings = settings
            self._customer_service_prompt_max_bytes = settings.rag_prompt_max_bytes
            self._client = ChatOpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url,
                model=settings.model_name, temperature=settings.model_temperature,
                max_tokens=settings.model_max_tokens, max_retries=0, timeout=settings.rag_answer_timeout_seconds)

        def begin(self, key):
            self.key = key
            self.observation = {'providerCalled': False, 'usage': None, 'answerLatencyMs': None,
                                'actualMessages': [], 'responseMetadata': None, 'rawResponse': None}

        async def _invoke(self, messages):
            self._check_budget(messages)
            self.observation['actualMessages'] = [{'role': m.type, 'content': m.content} for m in messages]
            budget.reserve_chat(self.key)
            self.observation['providerCalled'] = True
            started = time.perf_counter()
            try:
                with tracing_context(enabled=False):
                    response = await self._client.ainvoke(messages)
                metadata = response.response_metadata or {}
                # 仅保留版本/结束原因白名单，避免把 SDK 的请求头带入原始产物。
                self.observation.update(usage=response.usage_metadata or {},
                    responseMetadata={k: metadata[k] for k in
                        ('model_name', 'model', 'system_fingerprint', 'finish_reason', 'stop_reason') if k in metadata},
                    rawResponse=str(response.content))
                return response
            finally:
                self.observation['answerLatencyMs'] = round((time.perf_counter() - started) * 1000, 3)
    return MeteredModel(settings)


def _local_model_manifest(settings):
    from huggingface_hub import snapshot_download
    import torch
    if settings.rag_reranker_model != 'BAAI/bge-reranker-v2-m3' or not torch.cuda.is_available():
        fail('LOCAL_BGE_REQUIRED')
    # 本地缓存缺失即失败；加载固定目录，不能让模型名在运行时解析到新修订。
    directory = Path(snapshot_download(settings.rag_reranker_model,
        revision=HF_REVISION, local_files_only=True))
    files = {str(p.relative_to(directory)).replace('\\', '/'): file_hash(p)
             for p in sorted(directory.rglob('*')) if p.is_file()}
    if not any('tokenizer' in name for name in files) or 'config.json' not in files:
        fail('LOCAL_BGE_CACHE_INCOMPLETE')
    return directory, {'model': settings.rag_reranker_model, 'revision': HF_REVISION,
        'filesSha256': files, 'gpu': torch.cuda.get_device_name(0),
        'cudaRuntime': torch.version.cuda, 'torch': torch.__version__,
        'gpuTotalBytes': torch.cuda.get_device_properties(0).total_memory,
        'precision': 'fp16', 'normalization': False}


def check_settings(cfg):
    if (not cfg.rag_publication_enabled or cfg.rag_retrieval_top_k != 8
            or cfg.rag_final_top_k != 3 or cfg.rag_min_rerank_score is not None
            or cfg.rag_document_chunk_limit != 1
            or cfg.rag_reranker_provider != 'local_cross_encoder'
            or cfg.rag_embedding_dimension != 3072
            or cfg.rag_embedding_model != 'openai:text-embedding-3-large'
            or cfg.rag_chunk_size != 1000 or cfg.rag_chunk_overlap != 150):
        fail('UNSUPPORTED_BASELINE_SETTINGS')


def publication_client(settings):
    from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient
    return SpringKnowledgePublicationClient(gateway_base_url=str(settings.spring_gateway_base_url),
        bearer_token=settings.spring_gateway_bearer_token)


async def run_live(output, mapping_path):
    import httpx
    from openai import AsyncOpenAI
    from ai_service.config import Settings
    from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore
    from ai_service.infrastructure.knowledge_publication_store import PublicationStore
    from ai_service.models.reranker import LocalCrossEncoderReranker
    from ai_service.orchestration.customer_service_rag import _filter_and_dedupe, _validate_ranked
    from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT

    rubric = load_rubric()
    load_special()
    mapping = load_mapping(mapping_path, rubric)
    cfg = Settings(_env_file=ROOT / 'ai-service/.env')
    # 固定实验只有单文档单块授权，必须在资源或账本初始化之前拒绝变更。
    check_settings(cfg)
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / 'tmp').resolve()) or output.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    # 即使换 output 也必须命中同一固定锚点；不存在自动续跑或新 study 的继承授权。
    if AUTHORIZATION.exists():
        fail('AUTHORIZATION_ALREADY_USED')
    report = new_report(rubric)
    report['freeze'] = source_manifest(rubric)
    report['mappingSha256'] = fingerprint(mapping)
    report['config'] = {k: getattr(cfg, k) for k in (
        'rag_embedding_model', 'rag_embedding_dimension', 'rag_chunk_size', 'rag_chunk_overlap',
        'rag_retrieval_top_k', 'rag_final_top_k', 'rag_min_rerank_score', 'rag_prompt_max_bytes',
        'rag_reranker_provider', 'rag_reranker_model', 'rag_reranker_batch_size',
        'rag_reranker_timeout_seconds', 'rag_reranker_workers', 'rag_reranker_max_concurrency',
        'model_name', 'model_temperature', 'model_max_tokens',
        'model_context_window_tokens', 'model_context_overhead_tokens', 'rag_answer_timeout_seconds')}
    report['config'].update(answerPromptSha256=hashlib.sha256(CUSTOMER_SERVICE_SYSTEM_PROMPT.encode()).hexdigest(),
        search={'metric_type': 'COSINE', 'params': {}, 'consistency_level': 'Strong'},
        perDocument=1, maxContexts=3, maxContextChars=1200, maxTotalContextChars=3000,
        answerOrder='题序交替off/on、on/off', embeddingBatchSizes=[32, 32, 26])
    report['config'].update(modelProviderSha256=hashlib.sha256(cfg.model_base_url.encode()).hexdigest(),
        embeddingProviderSha256=hashlib.sha256(cfg.closeai_base_url.encode()).hexdigest())
    output.mkdir(parents=True)
    write_json(output / 'document-map.json', mapping)
    budget = None
    client = store = embedding = reranker = model = gpu_owner = original_cancel = None
    def save():
        report['callsReserved'] = dict(budget.counts) if budget else {'embeddingRequests': 0, 'queryInputs': 0, 'chat': 0}
        write_json(output / 'report.json', report)
    save()
    try:
        directory, report['localBge'] = _local_model_manifest(cfg)
        client = publication_client(cfg)
        store = MilvusKnowledgeStore(cfg)
        publications = PublicationStore(cfg, store)
        snapshot = await client.snapshot()
        receipt, body = await publications.completed(snapshot.collection_name)
        await publications.validate_publication(snapshot)
        corpus = verify_corpus(snapshot, receipt, body, mapping, cfg)
        if await client.snapshot() != snapshot:
            fail('SNAPSHOT_CHANGED')
        report['snapshot'] = snapshot.model_dump(by_alias=True)
        report['receipt'] = receipt.model_dump(by_alias=True)
        report['corpusRowsSha256'] = fingerprint(body)
        report['corpusChunks'] = len(corpus)
        indexes = await store._call('list_indexes', snapshot.collection_name)
        report['indexes'] = [await store._call('describe_index', snapshot.collection_name, index_name=name) for name in indexes]
        report['collectionDescription'] = await store._describe_collection(snapshot.collection_name)
        write_json(output / 'publication-rows.json', body)
        save()
        # 只设置当前 runner 的离线加载环境，避免 BGE 子进程隐式访问 HF。
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        from ai_service.models.reranker import _GpuOwnerLock
        # 固定本地目录会改变内层锁名，外层仍占正式模型名，防止与正式 owner 并存。
        gpu_owner = _GpuOwnerLock.acquire(cfg.rag_reranker_model, cfg.rag_reranker_device)
        reranker_cfg = cfg.model_copy(update={'rag_reranker_model': str(directory)})
        reranker = await LocalCrossEncoderReranker.create(reranker_cfg)
        from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
        warm = corpus[0]
        warm_chunk = RetrievedChunk(warm['chunkId'], warm['documentId'], warm['documentVersion'],
            warm['chunkIndex'], warm['content'], warm['fileName'], warm['fileType'],
            warm['sourceLocator'], warm['contentHash'], 0.0, 0.0, True, warm['documentVersion'])
        started = time.perf_counter()
        await reranker.rerank('产品公开功能说明', [warm_chunk], top_n=1)
        report['warmup'] = {'kind': 'local-only-single-pair', 'count': 1,
            'inputSha256': fingerprint(['产品公开功能说明', warm['content']]),
            'latencyMs': round((time.perf_counter() - started) * 1000, 3)}
        questions = [e['question'].strip() for e in rubric['entries']]
        specification = {'studyId': STUDY_ID, 'rubric': report['rubricSha256'],
            'mapping': report['mappingSha256'], 'snapshot': report['snapshot'],
            'config': report['config'], 'code': report['freeze'], 'localBge': report['localBge']}
        budget = AuthorizationBudget(AUTHORIZATION, output=output, specification=specification,
            questions=questions, schedule=[e['id'] + ':' + arm for e, arm in schedule(rubric['entries'])])
        write_json(output / 'specification.json', specification)
        embedding = AsyncOpenAI(api_key=cfg.closeai_api_key, base_url=cfg.closeai_base_url,
            max_retries=0, http_client=httpx.AsyncClient(trust_env=False, timeout=60))
        vectors = []
        for batch_index in range(3):
            batch = questions[32 * batch_index:32 * (batch_index + 1)]
            budget.reserve_embedding(batch_index, batch)
            save()
            started = time.perf_counter()
            response = await embedding.embeddings.create(model=cfg.rag_embedding_model.removeprefix('openai:'), input=batch)
            rows = sorted(response.data, key=lambda item: item.index)
            if ([r.index for r in rows] != list(range(len(batch)))
                    or any(len(r.embedding) != cfg.rag_embedding_dimension
                    or any(type(v) not in {int, float} or not math.isfinite(v) for v in r.embedding)
                    or not any(r.embedding) for r in rows)):
                fail('QUERY_EMBEDDING_INVALID')
            vectors.extend(r.embedding for r in rows)
            report['embeddingUsage'].append({'batch': batch_index, 'inputs': len(batch),
                'usage': response.usage.model_dump() if response.usage else None,
                'model': response.model, 'latencyMs': round((time.perf_counter() - started) * 1000, 3)})
            # 每批结果即刻持久化，后续失败也不隐藏已消耗的查询向量。
            write_json(output / 'query-vectors.json', vectors)
            save()
        query_vectors = {e['id']: vector for e, vector in zip(rubric['entries'], vectors, strict=True)}
        report['queryVectorsSha256'] = fingerprint(query_vectors)
        prepared = {}
        for entry in rubric['entries']:
            if await client.snapshot() != snapshot:
                fail('SNAPSHOT_CHANGED')
            started = time.perf_counter()
            retrieved = await publications.search_publication(query_vectors[entry['id']], 8, snapshot)
            retrieval_ms = round((time.perf_counter() - started) * 1000, 3)
            filtered = _filter_and_dedupe(retrieved)
            if not filtered:
                fail('PREPARATION_EMPTY_CANDIDATES')
            started = time.perf_counter()
            ranked = _validate_ranked(await reranker.rerank(entry['question'].strip(), filtered,
                top_n=len(filtered)), filtered, len(filtered))
            if {r.chunk.chunk_id for r in ranked} != {c.chunk_id for c in filtered}:
                fail('PREPARATION_RERANK_INCOMPLETE')
            rerank_ms = round((time.perf_counter() - started) * 1000, 3)
            prepared[entry['id']] = {'retrieved': retrieved, 'filtered': filtered, 'ranked': ranked}
            report['retrieval'][entry['id']] = {'sharedRetrievalLatencyMs': retrieval_ms,
                'realBgeLatencyMs': rerank_ms, 'retrieved': [chunk_json(c) for c in retrieved],
                'filtered': [chunk_json(c) for c in filtered],
                'ranked': [{'chunkId': r.chunk.chunk_id, 'score': r.score} for r in ranked]}
            save()
        if await client.snapshot() != snapshot:
            fail('SNAPSHOT_CHANGED')
        report['retrievalFreezeSha256'] = fingerprint(report['retrieval'])
        write_json(output / 'retrieval-freeze.json', report['retrieval'])
        # 回答阶段只回放真实 BGE 分数；提前释放 GPU owner，不能再次推理或降级。
        await reranker.close()
        reranker = None
        gpu_owner.release()
        gpu_owner = None
        model = _runtime_model(cfg, budget)
        await execute_answers(report, rubric, cfg, prepared, query_vectors, model, save,
            snapshot=snapshot, client=client,
            logical_by_actual={m['documentId']: logical for logical, m in mapping['documents'].items()})
    except asyncio.CancelledError as error:
        original_cancel = error
        report.update(status='cancelled', stopReason='CANCELLED')
        raise
    except Exception as error:
        report.update(status='stopped', stopReason=code(error))
    finally:
        report['finishedAtUtc'] = now()
        await _finish_run(report, save, budget=budget, resources=(('reranker', reranker),
            ('embedding', embedding), ('client', client), ('store', store)), model=model,
            gpu_owner=gpu_owner, original_cancel=original_cancel)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--document-map', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.live:
        if not args.document_map or not args.output:
            parser.error('--live 要求 --document-map 与新的 --output；同一固定授权只准启动一次')
        report = asyncio.run(run_live(args.output, args.document_map))
        print({'studyId': STUDY_ID, 'status': report['status'], 'callsReserved': report['callsReserved'],
               'humanReview': 'pending', 'results': len(report['results'])})
        if report['status'] != 'complete':
            raise SystemExit(2)
    else:
        if args.output:
            parser.error('--output 仅用于显式 --live；离线校验不会创建运行目录')
        rubric = load_rubric()
        special = load_special()
        if args.document_map:
            load_mapping(args.document_map, rubric)
        print({'mode': 'offline', 'cases': len(rubric['entries']), 'answerable': 81, 'refusal': 9,
            'publicRequiredFacts': 130, 'protocolFacts': 27, 'optionalFacts': 29,
            'specialOfflineCases': len(special['entries']), 'providerCalls': 0, 'humanReview': 'pending'})


if __name__ == '__main__':
    main()
