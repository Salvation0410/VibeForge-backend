"""新授权只能消费固定 chat 计划；所有账本均使用 pytest 隔离目录。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ai_service.orchestration import rag_cap_budget as module


def keys():
    return [f'q{i}:{arm}' for i in range(90)
            for arm in (('cap1', 'cap2') if i % 2 == 0 else ('cap2', 'cap1'))]


def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'anchor.jsonl')
    return module.CapAuthorizationBudget(output=tmp_path / 'live', plan_sha256='a' * 64,
                                          schedule=keys())


def test_fixed_exclusive_chat_only_and_nonrefundable(tmp_path, monkeypatch):
    budget = ledger(tmp_path, monkeypatch)
    assert not hasattr(budget, 'reserve_embedding')
    for key in keys():
        budget.reserve_chat(key)
    assert budget.counts == {'chat': 180, 'embeddingRequests': 0, 'queryInputs': 0}
    for key in (keys()[0], 'unknown', 'q90:cap1'):
        with pytest.raises(ValueError, match='CHAT_BUDGET_INVALID'):
            budget.reserve_chat(key)
    budget.close()
    with pytest.raises(ValueError, match='LEDGER_UNAVAILABLE'):
        budget.reserve_chat('unknown')
    with pytest.raises(FileExistsError):
        module.CapAuthorizationBudget(output=tmp_path / 'different', plan_sha256='a' * 64,
                                      schedule=keys())
    rows = [json.loads(line) for line in module.AUTHORIZATION.read_text().splitlines()]
    assert len(rows) == 181 and rows[-1]['key'] == keys()[-1]
    assert rows[0]['limits'] == {'chat': 180, 'embeddingRequests': 0, 'queryInputs': 0}


def test_fsync_uncertainty_poisoned_before_any_send(tmp_path, monkeypatch):
    budget = ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(module.os, 'fsync', lambda _: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        budget.reserve_chat(keys()[0])
    assert budget.counts['chat'] == 0
    with pytest.raises(ValueError, match='LEDGER_UNAVAILABLE'):
        budget.reserve_chat(keys()[1])
    budget.close()


@pytest.mark.parametrize('schedule', [[], keys()[:-1], keys()[:-1] + [keys()[0]],
                                    [f'other{i}' for i in range(180)],
                                    [keys()[1], keys()[0], *keys()[2:]]])
def test_schedule_must_be_complete_fixed_alternation(tmp_path, monkeypatch, schedule):
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'never.jsonl')
    with pytest.raises(ValueError, match='AUTHORIZATION_PLAN_INVALID'):
        module.CapAuthorizationBudget(output=tmp_path, plan_sha256='a' * 64, schedule=schedule)
    assert not module.AUTHORIZATION.exists()


def test_process_exit_keeps_reservation_and_refuses_restart(tmp_path):
    script = '''
import os, sys
from pathlib import Path
from ai_service.orchestration import rag_cap_budget as b
b.AUTHORIZATION = Path(sys.argv[1])
keys = [f'q{i}:{arm}' for i in range(90) for arm in (('cap1','cap2') if i%2==0 else ('cap2','cap1'))]
ledger = b.CapAuthorizationBudget(output=Path(sys.argv[2]), plan_sha256='a'*64, schedule=keys)
ledger.reserve_chat(keys[0])
os._exit(79)
'''
    anchor = tmp_path / 'exit.jsonl'
    result = subprocess.run([sys.executable, '-c', script, str(anchor), str(tmp_path / 'live')],
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')},
        timeout=30, capture_output=True)
    assert result.returncode == 79, result.stderr
    assert json.loads(anchor.read_text().splitlines()[-1])['key'] == keys()[0]


def test_complete_reversed_question_order_is_legal_for_budget_only(tmp_path, monkeypatch):
    # 账本验证配对形状；具体冻结题序由 validate_plan 绑定，不能把它当题目排序器。
    monkeypatch.setattr(module, 'AUTHORIZATION', tmp_path / 'reversed.jsonl')
    budget = module.CapAuthorizationBudget(output=tmp_path, plan_sha256='a' * 64, schedule=keys()[::-1])
    budget.close()
