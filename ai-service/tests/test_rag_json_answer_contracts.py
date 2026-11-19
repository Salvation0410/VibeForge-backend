"""新研究契约独立于旧 cap 与重排研究。"""
from ai_service.orchestration import rag_json_answer_contracts as contracts
import pytest
from copy import deepcopy
from test_rag_cap_contracts import frozen_source, write
from test_rag_json_answer_experiment import prepared


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['bytes', 'message', 'question', 'contexts', 'model', 'mode'])
async def test_historical_inputs_and_baseline_reject_drift(frozen_source, tmp_path, monkeypatch, damage):
    cfg, _, _ = await prepared(frozen_source, tmp_path, monkeypatch)
    previous = contracts.read_json(contracts.SOURCE)
    if damage == 'bytes':
        contracts.SOURCE.write_bytes(contracts.SOURCE.read_bytes() + b' ')
    elif damage in ('mode', 'model'):
        cfg = cfg.model_copy(update={'rag_answer_response_format': 'json_object'} if damage == 'mode'
                             else {'model_name': 'other-model'})
    else:
        if damage == 'message': previous['results'][0]['actualMessages'][1]['content'] += '改动'
        elif damage == 'question':
            previous['results'][0]['id'] = 'other-question'
        else: previous['results'][0]['contexts'][0]['content'] += '改动'
        write(contracts.SOURCE, previous)
        monkeypatch.setattr(contracts, 'SOURCE_REPORT_SHA256', contracts.file_hash(contracts.SOURCE))
    with pytest.raises(ValueError): contracts.load_frozen_inputs(cfg)


def test_fixed_study_and_schedule():
    assert contracts.STUDY_ID == 'rag-json-answer-20261119-v1'
    assert contracts.ARMS == ('text', 'json_object')
    entries = [{'id': f'q{i}'} for i in range(90)]
    assert contracts.schedule(entries)[:4] == ['q0:text', 'q0:json_object', 'q1:json_object', 'q1:text']
