"""第23轮排他预占账本；不提供恢复、返还额度或替换锚点的入口。"""
import hashlib
import json
import os
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Budget:
    def __init__(self, anchor, plan):
        self.schedule = plan['schedule']
        if (sum(x['kind'] == 'embedding' for x in self.schedule) != 8
                or sum(x['kind'] == 'bge' for x in self.schedule) != 180
                or sum(x.get('inputs', 0) for x in self.schedule) != 225
                or len({x['key'] for x in self.schedule}) != 188):
            raise ValueError('QA_BUDGET_PLAN_INVALID')
        self.count = 0
        self.poisoned = False
        self.stream = Path(anchor).open('x', encoding='utf8')
        try:
            self._append(dict(type='authorization', planSha256=fingerprint(plan),
                embeddingLimit=8, embeddingInputsLimit=225, bgeLimit=180, chatLimit=0))
        except BaseException:
            self.stream.close()
            raise

    def _append(self, value):
        if self.poisoned:
            raise ValueError('QA_BUDGET_PERSISTENCE_UNKNOWN')
        try:
            self.stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.poisoned = True
            raise

    def reserve(self, item):
        if self.count >= len(self.schedule) or item != self.schedule[self.count]:
            raise ValueError('QA_BUDGET_SCHEDULE_MISMATCH')
        self._append(dict(type='reserved', ordinal=self.count, **item))
        self.count += 1

    def close(self):
        self.stream.close()
