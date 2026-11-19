"""独立固定锚点的发送前预占。"""
import pytest
import os
import json
from ai_service.orchestration import rag_json_answer_budget as module
from ai_service.orchestration.rag_json_answer_contracts import schedule


def test_exclusive_anchor_and_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'anchor.jsonl')
    keys = schedule([{'id': f'q{i}'} for i in range(90)])
    budget = module.JsonAnswerAuthorizationBudget(output=tmp_path / 'live', plan_sha256='a' * 64, schedule=keys)
    budget.reserve_chat(keys[0])
    with pytest.raises(ValueError):
        budget.reserve_chat(keys[0])
    budget.close()
    with pytest.raises(FileExistsError):
        module.JsonAnswerAuthorizationBudget(output=tmp_path / 'other', plan_sha256='a' * 64, schedule=keys)


def make_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'anchor.jsonl')
    keys = schedule([{'id': f'q{i}'} for i in range(90)])
    return module.JsonAnswerAuthorizationBudget(output=tmp_path / 'live', plan_sha256='a'*64, schedule=keys), keys


def test_all_keys_flush_and_fsync_before_count(tmp_path, monkeypatch):
    budget, keys = make_budget(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(os, 'fsync', lambda fd: calls.append(budget.counts['chat']))
    for i, key in enumerate(keys):
        budget.reserve_chat(key)
        assert calls[-1] == i and budget.counts['chat'] == i+1
    budget.close()
    records = [json.loads(line) for line in module.AUTHORIZATION.read_text().splitlines()]
    assert len(records) == 181 and records[0]['output'] == str((tmp_path / 'live').resolve())
    with pytest.raises(ValueError): budget.reserve_chat(keys[-1])


@pytest.mark.parametrize('damage', ['fsync', 'flush', 'write'])
def test_uncertain_write_poison(tmp_path, monkeypatch, damage):
    budget, keys = make_budget(tmp_path, monkeypatch)
    original = budget._stream
    class Stream:
        closed = False
        def write(self, content):
            if damage == 'write': raise OSError
            return original.write(content)
        def flush(self):
            if damage == 'flush': raise OSError
            original.flush()
        def fileno(self): return original.fileno()
        def close(self): original.close()
    budget._stream = Stream()
    if damage == 'fsync': monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError): budget.reserve_chat(keys[0])
    assert budget.counts['chat'] == 0
    with pytest.raises(ValueError, match='LEDGER_UNAVAILABLE'): budget.reserve_chat(keys[0])
    budget.close()


@pytest.mark.parametrize('damage', ['duplicate', 'order', 'missing', 'id', 'hash'])
def test_invalid_schedule_no_anchor(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'anchor.jsonl')
    keys = schedule([{'id': f'q{i}'} for i in range(90)])
    sha = 'a'*64
    if damage == 'duplicate': keys[-1] = keys[0]
    if damage == 'order': keys[0], keys[1] = keys[1], keys[0]
    if damage == 'missing': keys.pop()
    if damage == 'id': keys[0] = ':text'
    if damage == 'hash': sha = 'invalid'
    with pytest.raises(ValueError):
        module.JsonAnswerAuthorizationBudget(output=tmp_path / 'live', plan_sha256=sha, schedule=keys)
    assert not module.AUTHORIZATION.exists()
