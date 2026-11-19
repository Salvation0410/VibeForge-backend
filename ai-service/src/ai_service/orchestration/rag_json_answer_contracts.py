"""JSON 格式研究的冻结输入；不创建 SDK 或访问在线资源。"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from copy import deepcopy
from pathlib import Path

from ai_service.orchestration import rag_cap_contracts as cap

ROOT, encoded, fingerprint, file_hash, read_json = cap.ROOT, cap.encoded, cap.fingerprint, cap.file_hash, cap.read_json
STUDY_ID = 'rag-json-answer-20261119-v1'
ARMS = ('text', 'json_object')
SOURCE = ROOT / 'tmp/rag-cap-answer-20261118/prepared/live/report.json'
AUTHORIZATION = ROOT / 'tmp/rag-json-answer-20261119/json-answer-authorization.jsonl'
SOURCE_REPORT_SHA256 = 'c34af6aa6b5f96f654e3255ccd239d8da5474ace2db3fc3337b270dcb3dbb704'
INPUT_SHA256 = 'bf559c24a88cebf92e16f50f67fc1f71b47ed77670455f1444f6cb5710d30b1f'
MAX_RESPONSE_BYTES = 1024 * 1024


def fail(code):
    raise ValueError('RAG_JSON_ANSWER_' + code)


def schedule(entries):
    return [entry['id'] + ':' + arm for i, entry in enumerate(entries)
            for arm in (ARMS if i % 2 == 0 else ARMS[::-1])]


def sdk_manifest():
    return {name: version(name) for name in ('openai', 'langchain-openai', 'langchain-core', 'httpx', 'pydantic')}


def implementation_manifest():
    paths = cap.implementation_manifest()
    modules = Path(__file__).parent
    for path in sorted([*modules.glob('rag_json_answer_*.py'), modules / 'rag_blind_review.py']):
        paths[str(path.relative_to(ROOT)).replace('\\', '/')] = file_hash(path)
    return paths


def settings_manifest(cfg):
    return {**cap.settings_manifest(cfg), 'responseModes': list(ARMS), 'maxResponseBytes': MAX_RESPONSE_BYTES,
            'sdkRetries': 0, 'appRetries': 0, 'concurrency': 1,
            'textNonNormalTermination': 'reject_length_content_filter', 'jsonObjectTermination': 'sdk_parse'}


def messages(question, contexts):
    from ai_service.models.base import CustomerServiceContext
    from ai_service.prompts import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt
    return [{'role': 'system', 'content': CUSTOMER_SERVICE_SYSTEM_PROMPT},
            {'role': 'human', 'content': customer_service_user_prompt(question,
                [CustomerServiceContext(c['chunkId'], c['content']) for c in contexts])}]


@dataclass(frozen=True)
class FrozenInputs:
    base: object
    previous: dict
    common: list
    source_report_sha256: str

    def __getattr__(self, name):
        return getattr(self.base, name)


def load_frozen_inputs(cfg):
    # 旧契约复核完整语料、向量题序、候选身份及 BGE 顺序，而不是只校验进入上下文的块。
    base = cap.load_frozen_inputs(cfg)
    before = file_hash(SOURCE)
    previous = read_json(SOURCE)
    if (before != SOURCE_REPORT_SHA256 or previous.get('studyId') != cap.STUDY_ID
            or previous.get('schemaVersion') != 'rag-cap-experiment/v1'):
        fail('SOURCE_REPORT_CHANGED')
    cap.check_settings(cfg, previous['config'])
    if (previous.get('sourceReportSha256') != base.source_report_sha256
            or previous.get('sourceFilesSha256') != base.source_files
            or previous.get('rubricSha256') != fingerprint(base.rubric)
            or previous.get('mappingSha256') != fingerprint(base.mapping)):
        fail('SOURCE_IDENTITY_CHANGED')
    rows = previous.get('results', [])
    if len(rows) != 180 or len({r['id'] + ':' + r['arm'] for r in rows}) != 180:
        fail('SOURCE_RESULTS_INVALID')
    lookup = {r['id']: r for r in rows if r['arm'] == 'cap1'}
    if set(lookup) != {e['id'] for e in base.rubric['entries']}:
        fail('SOURCE_RESULTS_INVALID')
    common = []
    for entry in base.rubric['entries']:
        row = lookup[entry['id']]
        actual = messages(entry['question'].strip(), row['contexts'])
        if row.get('actualMessages') != actual:
            fail('SOURCE_MESSAGES_CHANGED')
        common.append({'id': entry['id'], 'question': entry['question'].strip(),
                       'contexts': deepcopy(row['contexts']), 'actualMessages': actual})
    if fingerprint(common) != INPUT_SHA256 or file_hash(SOURCE) != before:
        fail('SOURCE_INPUT_CHANGED')
    return FrozenInputs(base, previous, common, before)


async def prepare_common(frozen, cfg):
    from ai_service.orchestration.rag_cap_experiment import prepare_contexts
    contexts = await prepare_contexts(frozen.base, cfg)
    result = {}
    for common in frozen.common:
        if contexts[common['id'] + ':cap1'] != common['contexts']:
            fail('FORMAL_CONTEXT_CHANGED')
        for arm in ARMS:
            result[common['id'] + ':' + arm] = deepcopy(common['contexts'])
    return result


evaluation_bindings = cap.evaluation_bindings
code_diagnostics = cap.code_diagnostics
