import json
import logging
import uuid

import pytest

from test_customer_service_rag import chunk, service, settings, FakeAnswerModel
from ai_service.models.base import CustomerServiceModelAnswer
from ai_service.orchestration.customer_service_trace import CustomerServiceRagTrace, create_trace


def test_diagnostics_requires_switch_and_nonempty_matching_prefix():
    assert create_trace('eval-1', settings()) is None
    assert create_trace('eval-1', settings(rag_diagnostics_enabled=True)) is None
    config = settings(rag_diagnostics_enabled=True, rag_diagnostics_request_prefix='eval-')
    assert create_trace('other', config) is None
    assert create_trace('eval-1', config) is not None


@pytest.mark.parametrize('limit', [1, 2])
def test_trace_config_fingerprint_captures_document_chunk_limit_without_content(limit):
    config = settings(rag_document_chunk_limit=limit)
    trace = CustomerServiceRagTrace('eval-cap', config)
    config.rag_document_chunk_limit = 3 - limit
    data = trace.snapshot()
    assert data['configFingerprint']['documentChunkLimit'] == limit
    assert data['configFingerprint']['finalTopK'] == 3
    assert 'content' not in json.dumps(data)


@pytest.mark.asyncio
async def test_trace_is_bounded_private_and_answer_strategy_unchanged():
    trace = CustomerServiceRagTrace('eval-\n"injected', settings())
    model = FakeAnswerModel(CustomerServiceModelAnswer(True, 'private answer', ('c1',)))
    result = await service(chunks=[chunk('c1', 'doc1', content='private context')], model=model).answer(
        'private question', trace=trace,
    )
    data = trace.snapshot()
    assert result.answered
    assert data['schemaVersion'] == 'customer-service-rag-trace/v1'
    uuid.UUID(data['traceId'])
    assert data['terminal']['reasonCode'] == 'ANSWERED'
    assert data['model'] == {'called': True, 'answered': True}
    assert data['citations'] == {'passed': True, 'citedChunkIds': ['c1']}
    serialized = json.dumps(data, allow_nan=False)
    for private in ('private question', 'private context', 'private answer', 'internal-token', 'closeai-key'):
        assert private not in serialized
    assert '\n' not in serialized


@pytest.mark.asyncio
async def test_empty_retrieval_records_reason_without_model_call():
    trace = CustomerServiceRagTrace('eval-empty', settings())
    result = await service().answer('question', trace=trace)
    assert not result.answered
    assert trace.snapshot()['terminal']['reasonCode'] == 'RAW_RETRIEVAL_EMPTY'
    assert trace.snapshot()['model'] == {'called': False, 'answered': None}


def test_emit_only_once_and_logging_failure_is_contained(monkeypatch):
    trace = CustomerServiceRagTrace('x' * 200, settings())
    calls = []
    def broken(message):
        calls.append(message)
        raise OSError('private filesystem failure')
    monkeypatch.setattr(logging.getLogger('customer_service.rag.trace'), 'info', broken)
    trace.emit()
    trace.emit()
    assert len(calls) == 1
    assert len(trace.snapshot()['requestId']) == 128


@pytest.mark.asyncio
async def test_concurrent_traces_keep_full_chunk_identity_and_local_state():
    import asyncio
    chunk_id = 'c' * 512
    async def run(request_id, chunk_id):
        trace = CustomerServiceRagTrace(request_id, settings())
        model = FakeAnswerModel(CustomerServiceModelAnswer(True, 'answer', (chunk_id,)))
        await service(chunks=[chunk(chunk_id, request_id)], model=model).answer('private', trace=trace)
        return trace.snapshot()
    first, second = await asyncio.gather(run('eval-1', chunk_id), run('eval-2', 'c2'))
    assert first['citations']['citedChunkIds'] == [chunk_id]
    assert second['citations']['citedChunkIds'] == ['c2']
    assert first['traceId'] != second['traceId']
    assert first['characterBudget'][0]['beforeChars'] >= first['characterBudget'][0]['afterChars']
    assert first['byteBudget'][0]['beforeChars'] == first['characterBudget'][0]['afterChars']


def test_standard_dictconfig_filehandler_collects_single_safe_json(tmp_path):
    import logging.config
    destination = tmp_path / 'trace.jsonl'
    logger = logging.getLogger('customer_service.rag.trace')
    old_handlers = logger.handlers[:]
    try:
        logging.config.dictConfig({
            'version': 1, 'disable_existing_loggers': False,
            'formatters': {'json': {'format': '%(message)s'}},
            'handlers': {'trace': {'class': 'logging.FileHandler', 'filename': str(destination),
                                    'formatter': 'json', 'encoding': 'utf-8'}},
            'loggers': {'customer_service.rag.trace': {'handlers': ['trace'], 'level': 'INFO', 'propagate': False}},
        })
        trace = CustomerServiceRagTrace('eval-\n"malicious', settings())
        trace.emit()
        trace.emit()
        lines = destination.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])['requestId'] == 'eval-\n"malicious'
        assert not logger.propagate
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = old_handlers


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,reason', [('rerank', 'RERANK_EMPTY'), ('byte', 'BYTE_CONTEXT_EMPTY')])
async def test_empty_intermediate_stage_has_distinct_reason(stage, reason, monkeypatch):
    from test_customer_service_rag import FakeReranker
    import ai_service.orchestration.customer_service_rag as module
    trace = CustomerServiceRagTrace('eval-empty-stage', settings())
    class EmptyReranker(FakeReranker):
        async def rerank(self, question, chunks, *, top_n):
            return []
    if stage == 'byte':
        monkeypatch.setattr(module, 'fit_customer_service_contexts', lambda *args, **kwargs: [])
    await service(chunks=[chunk('c1', 'doc1')],
                  reranker=EmptyReranker() if stage == 'rerank' else FakeReranker()).answer('q', trace=trace)
    assert trace.snapshot()['terminal']['reasonCode'] == reason
    if stage == 'byte':
        assert trace.snapshot()['byteBudget'][0]['afterChars'] == 0
