"""本轮固定授权的发送前账本；失败、取消、进程退出均不返还预占。"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading

from ai_service.orchestration.rag_rerank_contracts import STUDY_ID, encoded, fail, fingerprint


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    """先持久化完整临时文件再替换，读取方不会看到半份 JSON。"""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.partial')
    with temporary.open('wb') as stream:
        stream.write(encoded(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class AuthorizationBudget:
    """锚点路径由正式入口固定；新 study 不继承本轮授权，旧锚点永不覆盖。"""

    def __init__(self, anchor, *, output, specification, questions, schedule):
        if (len(questions) != 90 or len(schedule) != 180
                or len(set(schedule)) != 180 or len(set(questions)) != 90):
            fail('AUTHORIZATION_PLAN_INVALID')
        self._schedule = set(schedule)
        self._questions = list(questions)
        self._reserved = set()
        self._batches = set()
        self._poisoned = False
        self._lock = threading.Lock()
        self.counts = {'embeddingRequests': 0, 'queryInputs': 0, 'chat': 0}
        anchor = Path(anchor)
        anchor.parent.mkdir(parents=True, exist_ok=True)
        # x 模式既拒绝同目录重启，也拒绝换输出目录以同一授权再次消费。
        self._stream = anchor.open('xb')
        try:
            self._append({'type': 'authorization', 'studyId': STUDY_ID,
                'output': str(Path(output).resolve()), 'specificationSha256': fingerprint(specification),
                'createdAtUtc': now(), 'limits': {'embeddingRequests': 3, 'queryInputs': 90, 'chat': 180},
                'schedule': schedule, 'questionInputsSha256': fingerprint(questions)})
        except BaseException:
            self._stream.close()
            raise

    def _append(self, record):
        if self._poisoned or self._stream.closed:
            fail('LEDGER_UNAVAILABLE')
        try:
            self._stream.write(encoded(record) + b'\n')
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except BaseException:
            # 持久化结果不确定时禁止本实例继续发送，不能靠重试预占恢复。
            self._poisoned = True
            raise

    def reserve_embedding(self, batch_index, questions):
        with self._lock:
            if (type(batch_index) is not int or not 0 <= batch_index < 3
                    or batch_index in self._batches
                    or batch_index != len(self._batches)
                    or questions != self._questions[batch_index * 32:(batch_index + 1) * 32]):
                fail('EMBEDDING_BUDGET_INVALID')
            self._append({'type': 'reserved', 'kind': 'embedding', 'batch': batch_index,
                          'inputs': len(questions), 'inputsSha256': fingerprint(questions), 'atUtc': now()})
            self._batches.add(batch_index)
            self.counts['embeddingRequests'] += 1
            self.counts['queryInputs'] += len(questions)

    def reserve_chat(self, key):
        with self._lock:
            if (key not in self._schedule or key in self._reserved
                    or self.counts['chat'] >= 180 or self.counts['queryInputs'] != 90):
                fail('CHAT_BUDGET_INVALID')
            self._append({'type': 'reserved', 'kind': 'chat', 'key': key, 'atUtc': now()})
            self._reserved.add(key)
            self.counts['chat'] += 1

    def close(self):
        self._stream.close()
