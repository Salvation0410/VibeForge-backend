from __future__ import annotations

import copy
import json
import logging
import math
import time
import uuid
from contextlib import contextmanager

from ai_service.config import Settings


# 专用日志仅由显式配置的 handler 接收，避免元数据意外流入通用应用日志。
logger = logging.getLogger('customer_service.rag.trace')
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.NullHandler())


def create_trace(request_id: str, settings: Settings) -> CustomerServiceRagTrace | None:
    prefix = settings.rag_diagnostics_request_prefix
    if settings.rag_diagnostics_enabled and prefix and request_id.startswith(prefix):
        return CustomerServiceRagTrace(request_id, settings)
    return None


class CustomerServiceRagTrace:
    """单请求、白名单元数据诊断；不保存问题、正文、答案或异常对象。"""

    def __init__(self, request_id: str, settings: Settings) -> None:
        self._emitted = False
        self._data = {
            'schemaVersion': 'customer-service-rag-trace/v1',
            'requestId': request_id[:128],
            'traceId': str(uuid.uuid4()),
            'configFingerprint': {
                'retrievalTopK': settings.rag_retrieval_top_k,
                'finalTopK': min(settings.rag_final_top_k, 3),
                'minRerankScore': settings.rag_min_rerank_score,
                'promptMaxBytes': settings.rag_prompt_max_bytes,
                'maxContextChars': 1200, 'maxTotalContextChars': 3000,
                'model': settings.model_name[:256],
                'rerankerProvider': settings.rag_reranker_provider,
                'rerankerModel': settings.rag_reranker_model[:256],
                'embeddingModel': settings.rag_embedding_model[:256],
                'embeddingDimension': settings.rag_embedding_dimension,
                'alias': settings.milvus_collection_alias[:256],
            },
            'stageDurationsMs': {},
            **{stage: [] for stage in (
                'retrieved', 'filtered', 'reranked', 'thresholded', 'selected',
                'characterBudget', 'byteBudget',
            )},
            'model': {'called': False, 'answered': None},
            'citations': {'passed': None, 'citedChunkIds': []},
            'terminal': {'outcome': 'service_error', 'reasonCode': 'CUSTOMER_SERVICE_UNAVAILABLE', 'degraded': False},
        }

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            durations = self._data['stageDurationsMs']
            durations[name] = round(durations.get(name, 0) + (time.perf_counter() - started) * 1000, 3)

    def record(self, stage: str, items, *, score_source: str | None = None, kept_ids=None, drop_reason=None) -> None:
        records = []
        for rank, item in enumerate(items):
            if rank >= 100:
                break
            chunk = getattr(item, 'chunk', item)
            record = self.metadata(chunk, rank + 1, score=getattr(item, 'score', None))
            if score_source:
                record['scoreSource'] = score_source
            if kept_ids is not None:
                record['kept'] = getattr(chunk, 'chunk_id', None) in kept_ids
                if not record['kept']:
                    record['dropReason'] = drop_reason
            records.append(record)
        self._data[stage] = records

    @staticmethod
    def metadata(chunk, rank: int, *, score=None, drop_reason=None, length=None) -> dict:
        record = {'rank': rank}
        for source, target in (('chunk_id', 'chunkId'), ('document_id', 'documentId')):
            value = getattr(chunk, source, None)
            if isinstance(value, str):
                record[target] = value if len(value) <= 512 else value[:512]
        version = getattr(chunk, 'document_version', None)
        if type(version) is int and 0 <= version <= 2**63 - 1:
            record['documentVersion'] = version
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            try:
                if math.isfinite(float(score)):
                    record['score'] = float(score)
            except (ValueError, OverflowError):
                pass
        if drop_reason:
            record['dropReason'] = drop_reason
        content = getattr(chunk, 'content', None)
        if length is not None or isinstance(content, str):
            record['length'] = length if length is not None else len(content)
        return record

    def filter_records(self, records: list[dict]) -> None:
        for record in records:
            record['kept'] = 'dropReason' not in record
        self._data['filtered'] = records[:100]

    def contexts(self, stage: str, before, after, chunks) -> None:
        # 预算阶段保留同一身份信息，但只记录裁剪后的长度，绝不保存正文。
        retained = {item.chunk_id: item.content for item in after}
        records = []
        for rank, item in enumerate(before[:100], 1):
            content = retained.get(item.chunk_id, '')
            record = self.metadata(chunks[item.chunk_id], rank, length=len(content))
            record.update(beforeChars=len(item.content), afterChars=len(content), kept=bool(content))
            if stage == 'byteBudget':
                record.update(beforeBytes=len(item.content.encode('utf-8')), afterBytes=len(content.encode('utf-8')))
            if not content:
                record['dropReason'] = 'context_empty'
            records.append(record)
        self._data[stage] = records

    def model_called(self) -> None:
        self._data['model']['called'] = True

    def model_answered(self, answered: bool) -> None:
        self._data['model']['answered'] = bool(answered)

    def citations(self, passed: bool, ids) -> None:
        self._data['citations'] = {
            'passed': passed,
            'citedChunkIds': [item[:512] for item in ids[:100] if isinstance(item, str)],
        }

    def set_degraded(self, degraded: bool) -> None:
        self._data['terminal']['degraded'] = degraded

    def finish(self, outcome: str, reason: str, *, degraded: bool | None = None) -> None:
        # 自定义服务异常的 code 也不受信任，拒绝把任意 provider 内容写入日志。
        allowed = {
            'CUSTOMER_SERVICE_UNAVAILABLE', 'CUSTOMER_SERVICE_INVALID_REQUEST',
            'CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE', 'CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE',
            'RAW_RETRIEVAL_EMPTY', 'FILTERED_EMPTY', 'RERANK_EMPTY', 'THRESHOLD_EMPTY',
            'RERANK_UNAVAILABLE_WITH_THRESHOLD', 'CHAR_CONTEXT_EMPTY',
            'PROMPT_BUDGET_EXHAUSTED', 'BYTE_CONTEXT_EMPTY', 'MODEL_REFUSED',
            'MODEL_CALL_FAILED', 'MODEL_PARSE_FAILED', 'CITATION_INVALID', 'ANSWERED',
            'TIMEOUT', 'CLIENT_DISCONNECTED', 'CANCELLED',
        }
        if reason not in allowed:
            reason = 'CUSTOMER_SERVICE_UNAVAILABLE'
        if degraded is None:
            degraded = self._data['terminal']['degraded']
        self._data['terminal'] = {'outcome': outcome, 'reasonCode': reason, 'degraded': degraded}

    def snapshot(self) -> dict:
        return copy.deepcopy(self._data)

    def emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        try:
            # JSON 转义请求标识中的换行；日志失败不能改变业务回答或取消语义。
            logger.info(json.dumps(self._data, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
        except Exception:
            pass
