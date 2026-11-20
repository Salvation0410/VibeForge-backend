"""只注入本地故障，核对诊断而不改变回答策略或增加供应商调用。"""
import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from ai_service.models.base import CustomerServiceModelAnswer
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.models.openai_compatible import CustomerServiceModelOutputError
from ai_service.orchestration.customer_service_rag import NO_ANSWER_TEXT, SYSTEM_FALLBACK_TEXT
from ai_service.orchestration.customer_service_trace import CustomerServiceRagTrace, logger
from test_customer_service_rag import (
    FakeAnswerModel, FakeEmbeddings, FakeReranker, FakeStore, chunk, service, settings,
)
from test_knowledge_publication import env
from test_authoritative_knowledge import setup


PRIVATE = 'private question answer context exception token'


class ScopedStore(FakeStore):
    def __init__(self, *, empty=False, fail_at=None, error=None):
        super().__init__([chunk('c1', 'doc1', content=PRIVATE)])
        self.empty = empty
        self.fail_at = fail_at
        self.failure = error or RuntimeError(PRIVATE)
        self.checks = 0
        self.exits = 0

    @asynccontextmanager
    async def read_scope(self):
        if self.fail_at == 'enter':
            raise self.failure
        try:
            yield self
        finally:
            self.exits += 1
            if self.fail_at == 'exit':
                raise self.failure

    async def assert_current(self):
        self.checks += 1
        if self.fail_at == f'check{self.checks}':
            raise self.failure


def harness(store, *, embeddings=None, model=None):
    trace = CustomerServiceRagTrace('eval-failure', settings())
    model = model or FakeAnswerModel()
    embeddings = embeddings or FakeEmbeddings()
    return service(store=store, embeddings=embeddings, model=model), trace, embeddings, model


async def test_empty_authoritative_whitelist_is_normal_refusal_without_paid_calls():
    store = ScopedStore(empty=True)
    rag, trace, embeddings, model = harness(store)
    result = await rag.answer(PRIVATE, trace=trace)
    assert result.answer == NO_ANSWER_TEXT and not result.answered and not result.degraded
    assert trace.snapshot()['terminal']['reasonCode'] == 'KNOWLEDGE_PUBLICATION_EMPTY'
    assert trace.snapshot()['terminal']['outcome'] == 'refused'
    assert trace.snapshot()['terminal']['phase'] == 'publication_read'
    assert not embeddings.questions and not model.calls and not store.calls
    assert store.checks == 1 and store.exits == 1


@pytest.mark.parametrize('failure,empty,phase,calls', [
    ('enter', False, 'publication_read', 0),
    ('check1', True, 'publication_read', 0),
    ('check1', False, 'publication_before_model', 0),
    ('check2', False, 'publication_after_model', 1),
    ('exit', False, 'publication_scope_exit', 1),
    ('exit', True, 'publication_scope_exit', 0),
])
async def test_publication_failure_has_fixed_reason_and_actual_phase(failure, empty, phase, calls):
    store = ScopedStore(empty=empty, fail_at=failure)
    rag, trace, _, model = harness(store)
    result = await rag.answer(PRIVATE, trace=trace)
    assert result.answer == SYSTEM_FALLBACK_TEXT and not result.sources
    terminal = trace.snapshot()['terminal']
    assert terminal['reasonCode'] == 'KNOWLEDGE_PUBLICATION_UNAVAILABLE'
    assert terminal['phase'] == phase and terminal['outcome'] == 'service_error'
    assert len(model.calls) == calls
    assert PRIVATE not in json.dumps(trace.snapshot())


@pytest.mark.parametrize('failure,reason', [
    ('embedding', 'CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE'),
    ('retrieval', 'CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE'),
])
@pytest.mark.parametrize('exit_failure', [False, True])
async def test_scope_preserves_known_inner_failure_unless_exit_itself_fails(failure, reason, exit_failure):
    store = ScopedStore(fail_at='exit' if exit_failure else None)
    embeddings = FakeEmbeddings(error=RuntimeError(PRIVATE) if failure == 'embedding' else None)
    if failure == 'retrieval':
        store.error = RuntimeError(PRIVATE)
    rag, trace, _, model = harness(store, embeddings=embeddings)
    result = await rag.answer(PRIVATE, trace=trace)
    assert result.answer == SYSTEM_FALLBACK_TEXT and not model.calls
    terminal = trace.snapshot()['terminal']
    assert terminal['reasonCode'] == ('KNOWLEDGE_PUBLICATION_UNAVAILABLE' if exit_failure else reason)
    if exit_failure:
        assert terminal['phase'] == 'publication_scope_exit'
    assert len(embeddings.questions) == 1
    assert len(store.calls) == (failure == 'retrieval')


async def test_unknown_execution_error_uses_private_fixed_fallback(monkeypatch):
    rag, trace, _, model = harness(ScopedStore())
    async def unknown(*args, **kwargs):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(rag, '_answer', unknown)
    assert (await rag.answer(PRIVATE, trace=trace)).answer == SYSTEM_FALLBACK_TEXT
    assert trace.snapshot()['terminal']['reasonCode'] == 'CUSTOMER_SERVICE_UNAVAILABLE'
    assert not model.calls and PRIVATE not in json.dumps(trace.snapshot())


@pytest.mark.parametrize('error,answer,reason,outcome', [
    (RuntimeError(PRIVATE), None, 'MODEL_CALL_FAILED', 'service_error'),
    (TimeoutError(PRIVATE), None, 'MODEL_CALL_FAILED', 'service_error'),
    (CustomerServiceModelOutputError(), None, 'MODEL_PARSE_FAILED', 'service_error'),
    (None, CustomerServiceModelAnswer(True, PRIVATE, ('unknown',)), 'CITATION_INVALID', 'service_error'),
    (None, CustomerServiceModelAnswer(False, '', ()), 'MODEL_REFUSED', 'refused'),
])
async def test_model_and_citation_reasons_remain_distinct(error, answer, reason, outcome):
    rag, trace, _, model = harness(ScopedStore(), model=FakeAnswerModel(answer, error))
    result = await rag.answer(PRIVATE, trace=trace)
    assert trace.snapshot()['terminal']['reasonCode'] == reason
    assert trace.snapshot()['terminal']['outcome'] == outcome
    assert result.answer == (NO_ANSWER_TEXT if outcome == 'refused' else SYSTEM_FALLBACK_TEXT)
    assert len(model.calls) == 1


@pytest.mark.parametrize('at', ['enter', 'check1', 'check2', 'exit', 'model'])
async def test_cancellation_propagates_with_no_retry_or_logging_failure(monkeypatch, at):
    store = ScopedStore(fail_at=at, error=asyncio.CancelledError())
    model = FakeAnswerModel(error=asyncio.CancelledError() if at == 'model' else None)
    rag, trace, _, _ = harness(store, model=model)
    messages = []
    def broken_log(message):
        messages.append(message)
        raise OSError(PRIVATE)
    monkeypatch.setattr(logger, 'info', broken_log)
    with pytest.raises(asyncio.CancelledError):
        await rag.answer(PRIVATE, trace=trace)
    trace.emit()
    trace.emit()
    assert trace.snapshot()['terminal']['reasonCode'] == 'CANCELLED'
    assert len(model.calls) == (at in {'check2', 'exit', 'model'})
    assert len(messages) == 1 and PRIVATE not in messages[0]


def test_unknown_reason_and_phase_are_not_logged():
    trace = CustomerServiceRagTrace('eval-fixed', settings())
    trace.finish('service_error', PRIVATE, phase=PRIVATE)
    assert trace.snapshot()['terminal']['reasonCode'] == 'CUSTOMER_SERVICE_UNAVAILABLE'
    assert PRIVATE not in json.dumps(trace.snapshot())


@pytest.mark.parametrize('code,expected', [
    ('KNOWLEDGE_PUBLICATION_CHANGED', 'KNOWLEDGE_PUBLICATION_CHANGED'),
    (PRIVATE, 'KNOWLEDGE_PUBLICATION_UNAVAILABLE'),
])
async def test_publication_adapter_only_admits_fixed_change_code(code, expected):
    rag, trace, _, model = harness(ScopedStore(fail_at='check1', error=MilvusKnowledgeError(code)))
    assert (await rag.answer(PRIVATE, trace=trace)).answer == SYSTEM_FALLBACK_TEXT
    assert trace.snapshot()['terminal']['reasonCode'] == expected
    assert not model.calls
    assert PRIVATE not in json.dumps(trace.snapshot())


async def test_provider_cannot_spoof_publication_change_code():
    error = RuntimeError(PRIVATE)
    error.code = 'KNOWLEDGE_PUBLICATION_CHANGED'
    rag, trace, _, _ = harness(ScopedStore(fail_at='check1', error=error))
    await rag.answer(PRIVATE, trace=trace)
    assert trace.snapshot()['terminal']['reasonCode'] == 'KNOWLEDGE_PUBLICATION_UNAVAILABLE'


@pytest.mark.parametrize('change,calls,phase', [
    ('empty', 0, 'publication_read'),
    ('before', 0, 'publication_before_model'),
    ('after', 1, 'publication_after_model'),
])
async def test_concrete_authoritative_store_trace_and_scope_cleanup(env, change, calls, phase):
    _, control, store, embeddings, model, rag = await setup(env)
    trace = CustomerServiceRagTrace('eval-concrete', env[0])
    if change == 'empty':
        control.current.documents = []
    elif change == 'before':
        original = store.search
        async def search(*args):
            result = await original(*args)
            control.current.epoch += 1
            return result
        store.search = search
    else:
        original = model.answer_customer_service
        async def answer(*args):
            result = await original(*args)
            control.current.epoch += 1
            return result
        model.answer_customer_service = answer
    result = await rag.answer(PRIVATE, trace=trace)
    assert result.answer == (NO_ANSWER_TEXT if change == 'empty' else SYSTEM_FALLBACK_TEXT)
    terminal = trace.snapshot()['terminal']
    assert terminal['reasonCode'] == ('KNOWLEDGE_PUBLICATION_EMPTY' if change == 'empty' else 'KNOWLEDGE_PUBLICATION_CHANGED')
    assert terminal['phase'] == phase and not result.sources
    assert len(model.calls) == calls
    assert len(embeddings.questions) == (change != 'empty')
    assert store._snapshot.get() is None


async def test_recheck_failure_keeps_degraded_fallback_and_private_log(monkeypatch):
    trace = CustomerServiceRagTrace('eval-log', settings())
    model = FakeAnswerModel(CustomerServiceModelAnswer(True, PRIVATE, ('c1',)))
    rag = service(store=ScopedStore(fail_at='check2'), model=model,
                  reranker=FakeReranker(error=RuntimeError(PRIVATE)))
    messages = []
    def broken_log(message):
        messages.append(message)
        raise OSError(PRIVATE)
    monkeypatch.setattr(logger, 'info', broken_log)
    result = await rag.answer(PRIVATE, trace=trace)
    trace.emit()
    assert result.answer == SYSTEM_FALLBACK_TEXT and result.degraded and not result.sources
    assert trace.snapshot()['terminal']['degraded']
    assert len(model.calls) == 1 and len(messages) == 1
    assert PRIVATE not in messages[0]
