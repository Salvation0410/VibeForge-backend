"""独立 chat 发送前账本；失败、取消及进程退出均不返还预占。"""
from __future__ import annotations

from pathlib import Path
import os
import re
import threading

from ai_service.orchestration.rag_cap_contracts import AUTHORIZATION, STUDY_ID, encoded, fail
from ai_service.orchestration.rag_rerank_budget import now


class CapAuthorizationBudget:
    """固定锚点使用独占创建；没有 Embedding 接口或恢复/返还接口。"""

    def __init__(self, *, output, plan_sha256, schedule):
        # 允许冻结 rubric 的实际 ID，配对与题序交替则必须完整且唯一。
        if (not isinstance(schedule, list) or len(schedule) != 180
                or any(not isinstance(key, str) for key in schedule)
                or len(set(schedule)) != 180 or not isinstance(plan_sha256, str)
                or not re.fullmatch('[0-9a-f]{64}', plan_sha256)):
            fail('AUTHORIZATION_PLAN_INVALID')
        ids = []
        for i in range(90):
            arms = ('cap1', 'cap2') if i % 2 == 0 else ('cap2', 'cap1')
            pair = [key.rsplit(':', 1) for key in schedule[i * 2:i * 2 + 2]]
            if (any(len(parts) != 2 or not parts[0] or parts[1] != arm
                    for parts, arm in zip(pair, arms, strict=True)) or pair[0][0] != pair[1][0]):
                fail('AUTHORIZATION_PLAN_INVALID')
            ids.append(pair[0][0])
        if len(set(ids)) != 90:
            fail('AUTHORIZATION_PLAN_INVALID')
        self._schedule, self._reserved = set(schedule), set()
        self._lock = threading.Lock()
        self._poisoned = False
        self.counts = {'chat': 0, 'embeddingRequests': 0, 'queryInputs': 0}
        AUTHORIZATION.parent.mkdir(parents=True, exist_ok=True)
        self._stream = AUTHORIZATION.open('xb')
        try:
            self._append({'type': 'authorization', 'studyId': STUDY_ID,
                'planSha256': plan_sha256, 'output': str(Path(output).resolve()),
                'createdAtUtc': now(), 'schedule': schedule,
                'limits': {'chat': 180, 'embeddingRequests': 0, 'queryInputs': 0}})
        except BaseException:
            self._stream.close()
            raise

    def _available(self):
        if self._poisoned or self._stream.closed:
            fail('LEDGER_UNAVAILABLE')

    def _append(self, record):
        self._available()
        try:
            self._stream.write(encoded(record) + b'\n')
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except BaseException:
            # 写入是否落盘已不确定，当前进程禁止再次预占，更不能发送。
            self._poisoned = True
            raise

    def reserve_chat(self, key):
        with self._lock:
            self._available()
            if not isinstance(key, str) or key not in self._schedule or key in self._reserved or self.counts['chat'] >= 180:
                fail('CHAT_BUDGET_INVALID')
            self._append({'type': 'reserved', 'kind': 'chat', 'key': key, 'atUtc': now()})
            self._reserved.add(key)
            self.counts['chat'] += 1

    def close(self):
        self._stream.close()
