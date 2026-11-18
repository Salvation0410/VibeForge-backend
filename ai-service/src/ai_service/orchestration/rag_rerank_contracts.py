"""重排对照的本地规范、来源核对与冻结清单；导入本模块不连接外部依赖。"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = ROOT / 'doc/knowledge/product'
RUBRIC = PRODUCT / 'evaluation/rubric-20261118-v1.json'
SPECIAL = PRODUCT / 'evaluation/special-boundaries-20261118-v1.json'
STUDY_ID = 'rag-rerank-20261118-first-round-v1'
AUTHORIZATION = ROOT / 'tmp/rag-verification-20261118/rerank-authorization.jsonl'
ARMS = ('rerank_off', 'rerank_on')
HF_REVISION = '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'


def fail(code):
    raise ValueError('RAG_RERANK_' + code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _inside(root, relative):
    if not isinstance(relative, str):
        fail('SOURCE_PATH_INVALID')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        fail('SOURCE_PATH_INVALID')
    return path


def _strings(values):
    return isinstance(values, list) and bool(values) and all(
        isinstance(value, str) and value.strip() for value in values)


def load_rubric(path=RUBRIC, *, product=PRODUCT, root=ROOT):
    """冻结前核对所有原始问题、历史标注及 Unicode 码点证据，不做语义打分。"""
    data = read_json(product / 'evaluation/dataset.json')
    old = read_json(product / 'evaluation/annotations.json')
    rubric = read_json(path)
    if (rubric.get('schemaVersion') != 'product-knowledge-rubric-agent-draft/v1'
            or rubric.get('sourceDatasetVersion') != data['datasetVersion']
            or rubric.get('reviewStatus') != 'agent-source-reviewed-human-pending'
            or rubric.get('humanGold') is not False
            or rubric.get('answerBasedRelabeling') is not False):
        fail('RUBRIC_SCHEMA_INVALID')
    for area, base in [('inputs', product), ('documents', product), ('sourceFiles', root)]:
        for item in rubric['freeze'][area]:
            if file_hash(_inside(base, item['path'])) != item['sha256']:
                fail('FROZEN_SOURCE_CHANGED')
    entries = rubric['entries']
    originals = {e['id']: e for e in data['entries']}
    labels = {e['id']: e for e in old['entries']}
    if (len(entries) != 90 or len({e['id'] for e in entries}) != 90
            or {e['id'] for e in entries} != set(originals)):
        fail('RUBRIC_CASES_INVALID')
    ids, counts = set(), Counter()
    for entry in entries:
        original = originals[entry['id']]
        if (any(entry[key] != original[key] for key in
                ('question', 'expectedAnswerable', 'expectedDocumentIds'))
                or type(entry['expectedAnswerable']) is not bool
                or entry['originalAnnotation'] != labels[entry['id']]
                or not entry.get('changeReason')
                or entry['reviewStatus'] != rubric['reviewStatus']):
            fail('ORIGINAL_CASE_CHANGED')
        for kind in ('requiredFacts', 'optionalFacts'):
            if not isinstance(entry[kind], list) or (kind == 'requiredFacts' and not entry[kind]):
                fail('FACT_INVALID')
            for fact in entry[kind]:
                key = fact['factId']
                if (not isinstance(key, str) or key in ids or not fact.get('statement')
                        or not _strings(fact.get('equivalentExpressions'))
                        or not _strings(fact.get('applicableConditions'))
                        or fact.get('reviewStatus') != rubric['reviewStatus']):
                    fail('FACT_INVALID')
                ids.add(key)
                counts[kind] += 1
                if fact['basisType'] == 'evaluation-protocol':
                    if entry['expectedAnswerable'] or fact['evidence'] or not fact.get('protocolReferences'):
                        fail('PROTOCOL_FACT_INVALID')
                    counts['protocol'] += 1
                    continue
                if fact['basisType'] != 'public-document' or not fact['evidence']:
                    fail('PUBLIC_FACT_INVALID')
                if kind == 'requiredFacts':
                    counts['publicRequired'] += 1
                for ref in fact['evidence']:
                    source = _inside(product, ref['documentPath'])
                    text = source.read_text(encoding='utf8')
                    start, end = ref['quoteStart'], ref['quoteEnd']
                    if (ref['offsetUnit'] != 'unicode-code-points'
                            or type(start) is not int or type(end) is not int
                            or not 0 <= start < end <= len(text)
                            or not ref['quote'] or text[start:end] != ref['quote']
                            or file_hash(source) != ref['documentSha256']
                            or not ref['documentPath'].startswith('documents/')
                            or source.stem != ref['documentId']
                            or ('## ' + ref['heading']) not in text[:start]):
                        fail('EVIDENCE_INVALID')
                    counts['quotes'] += 1
    if (sum(e['expectedAnswerable'] for e in entries) != 81
            or counts != Counter(requiredFacts=157, optionalFacts=29,
                                 protocol=27, publicRequired=130, quotes=159)):
        fail('FROZEN_COUNTS_INVALID')
    return rubric


def load_special(path=SPECIAL):
    """专项集只校验离线定义，不参与本轮 90 题及付费额度。"""
    data = read_json(path)
    if (data.get('schemaVersion') != 'rag-special-boundaries/v1'
            or data.get('paidRunAuthorized') is not False
            or data.get('humanGold') is not False
            or len(data['entries']) != 8
            or Counter(e['category'] for e in data['entries']) !=
            Counter(sla=2, false_premise=2, partial_answer=2, condition_mismatch=2)
            or len({e['id'] for e in data['entries']}) != 8):
        fail('SPECIAL_INVALID')
    for entry in data['entries']:
        if not all(entry.get(key) for key in
                   ('question', 'currentContractAssessment', 'intendedDiagnostic', 'evidence')):
            fail('SPECIAL_INVALID')
        for ref in entry['evidence']:
            source = _inside(PRODUCT, ref['documentPath'])
            text = source.read_text(encoding='utf8')
            if (file_hash(source) != ref['documentSha256']
                    or text[ref['quoteStart']:ref['quoteEnd']] != ref['quote']):
                fail('SPECIAL_EVIDENCE_INVALID')
    return data


def load_mapping(path, rubric):
    data = read_json(path)
    docs = {d['documentId']: d for d in rubric['freeze']['documents']}
    rows = data.get('documents', {})
    if (data.get('schemaVersion') != 'product-knowledge-document-map/v1'
            or set(rows) != set(docs)
            or len({r['documentId'] for r in rows.values()}) != 11):
        fail('MAP_INVALID')
    for logical, item in rows.items():
        if (not isinstance(item['documentId'], str) or not item['documentId']
                or type(item['documentVersion']) is not int
                or item['documentVersion'] != docs[logical]['version']
                or item['sha256'] != docs[logical]['sha256']):
            fail('MAP_INVALID')
    return data


def verify_corpus(snapshot, receipt, rows, mapping, settings, *, product=PRODUCT):
    """以真实身份核对本地分块内容，文件名及逻辑 ID 不参与 chunkId 等值比较。"""
    from ai_service.orchestration.document_etl import parse_document, split_sections
    by_id = {d.document_id: d for d in snapshot.documents}
    mapped = mapping['documents']
    if (not snapshot.enabled or set(by_id) != {m['documentId'] for m in mapped.values()}
            or {d.document_id for d in receipt.documents} != set(by_id)):
        fail('SNAPSHOT_DOCUMENTS_MISMATCH')
    chunks = []
    for logical, expected in mapped.items():
        identity = by_id[expected['documentId']]
        if (identity.document_version != expected['documentVersion']
                or identity.sha256 != expected['sha256']):
            fail('SNAPSHOT_IDENTITY_MISMATCH')
        source = product / f'documents/{logical}.md'
        local = split_sections(parse_document(source, 'md'),
            document_id=identity.document_id, document_version=identity.document_version,
            source_name=identity.file_name, settings=settings)
        actual = sorted((r for r in rows if r.get('recordType') == 'chunk'
                         and r['documentId'] == identity.document_id), key=lambda r: r['chunkIndex'])
        if len(actual) != len(local):
            fail('CORPUS_CONTENT_MISMATCH')
        for original, indexed in zip(local, actual, strict=True):
            if (original.chunk_index != indexed['chunkIndex']
                    or original.content != indexed['content']
                    or original.source_locator != indexed['sourceLocator']
                    or indexed['contentHash'] != hashlib.sha256(original.content.encode()).hexdigest()
                    or indexed['documentContentHash'] != expected['sha256']
                    or indexed['documentVersion'] != identity.document_version):
                fail('CORPUS_CONTENT_MISMATCH')
            chunks.append({**indexed, 'logicalDocumentId': logical})
    return chunks


def source_manifest(rubric):
    """固定代码与脏工作树指纹；不读取或输出凭据与供应商地址。"""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf8').strip()
    paths = set(git('diff', '--name-only', '--no-renames', 'HEAD').splitlines())
    paths.update(git('ls-files', '--others', '--exclude-standard').splitlines())
    files = {p: file_hash(ROOT / p) if (ROOT / p).is_file() else None for p in sorted(paths) if p}
    modules = Path(__file__).parent
    relevant = [*modules.glob('rag_rerank*.py'), modules / 'rag_blind_review.py',
                modules / 'customer_service_rag.py', modules / 'customer_service_trace.py',
                modules / 'document_etl.py', modules.parent / 'prompts/customer_service.py',
                modules.parent / 'models/openai_compatible.py', modules.parent / 'models/reranker.py',
                modules.parent / 'infrastructure/knowledge_publication_store.py',
                ROOT / 'ai-service/uv.lock', RUBRIC]
    return {'codeSha': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
            'worktreeStatus': git('status', '--porcelain'), 'dirtyFilesSha256': files,
            'implementationFiles': {str(p.relative_to(ROOT)).replace('\\', '/'): file_hash(p)
                                    for p in relevant if p.exists()},
            'datasetSha256': file_hash(PRODUCT / 'evaluation/dataset.json'),
            'annotationsSha256': file_hash(PRODUCT / 'evaluation/annotations.json'),
            'rubricSha256': fingerprint(rubric), 'python': platform.python_version(),
            'packages': {name: importlib.metadata.version(name) for name in
                         ('pymilvus', 'FlagEmbedding', 'torch', 'transformers',
                          'langchain-openai', 'openai', 'pydantic')}}


def chunk_json(chunk):
    return asdict(chunk)
