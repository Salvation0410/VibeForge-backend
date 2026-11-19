"""固定锚点独占预占；尝试不等于供应商严格计费一次。"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from ai_service.orchestration.rag_json_answer_contracts import AUTHORIZATION, STUDY_ID, ARMS, encoded, fail
from ai_service.orchestration.rag_rerank_budget import now


class JsonAnswerAuthorizationBudget:
    def __init__(self, *, output, plan_sha256, schedule):
        if (not isinstance(schedule, list) or len(schedule) != 180
                or any(type(k) is not str for k in schedule) or len(set(schedule)) != 180
                or type(plan_sha256) is not str or not re.fullmatch('[a-f0-9]{64}', plan_sha256)):
            fail('AUTHORIZATION_PLAN_INVALID')
        ids = []
        for i in range(90):
            pair = [k.rsplit(':', 1) for k in schedule[2*i:2*i+2]]
            arms = ARMS if i % 2 == 0 else ARMS[::-1]
            if (any(len(p) != 2 or not p[0] or p[1] != a for p, a in zip(pair, arms, strict=True))
                    or pair[0][0] != pair[1][0]):
                fail('AUTHORIZATION_PLAN_INVALID')
            ids.append(pair[0][0])
        if len(set(ids)) != 90:
            fail('AUTHORIZATION_PLAN_INVALID')
        self._schedule = tuple(schedule)
        self._lock = threading.Lock()
        self._poisoned = False
        self.counts = {'chat': 0, 'embeddingRequests': 0, 'queryInputs': 0}
        AUTHORIZATION.parent.mkdir(parents=True, exist_ok=True)
        self._stream = AUTHORIZATION.open('xb')
        try:
            self._append({'type': 'authorization', 'studyId': STUDY_ID, 'planSha256': plan_sha256,
                          'output': str(Path(output).resolve()), 'schedule': schedule,
                          'limits': {'chat': 180, 'embeddingRequests': 0, 'queryInputs': 0}, 'atUtc': now()})
        except BaseException:
            self._stream.close()
            raise

    def _available(self):
        if self._poisoned or self._stream.closed:
            fail('LEDGER_UNAVAILABLE')

    def _append(self, record):
        self._available()
        try:
            payload = encoded(record) + b'\n'
            if self._stream.write(payload) != len(payload):
                raise OSError('不完整写入')
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except BaseException:
            # 不确定写入不能重试；重启也不恢复固定锚点的余额。
            self._poisoned = True
            raise

    def reserve_chat(self, key):
        with self._lock:
            self._available()
            index = self.counts['chat']
            if index >= 180 or key != self._schedule[index]:
                fail('CHAT_BUDGET_INVALID')
            self._append({'type': 'reserved', 'key': key, 'atUtc': now()})
            self.counts['chat'] += 1

    def close(self):
        self._stream.close()
