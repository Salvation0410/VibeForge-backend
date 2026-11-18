"""固定输入答案实验的离线冻结契约；导入及校验均不初始化外部客户端。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import subprocess

from ai_service.orchestration.rag_rerank_contracts import (
    ROOT, PRODUCT, encoded, file_hash, fingerprint, read_json, load_mapping, load_rubric,
    verify_corpus,
)

STUDY_ID = 'rag-cap-20261118-answer-round-v1'
SOURCE = ROOT / 'tmp/rag-verification-20261118/live'
REPLAY = ROOT / 'tmp/rag-cap-evidence-20261118/replay.json'
AUTHORIZATION = ROOT / 'tmp/rag-cap-answer-20261118/cap-authorization.jsonl'
SOURCE_REPORT_SHA256 = 'ad21903b50000f50d077af1c93b137e77bff1da6ca47d4ca04617f8c33781ee1'
REPLAY_SHA256 = '0acd669af2f70ea03035341fb410b7413808b9908b4473c318aad44d8d9fcbca'
ARMS = ('cap1', 'cap2')
ANNOTATIONS = PRODUCT / 'evaluation/annotations.json'
PAYMENT_CLARIFICATION = PRODUCT / 'evaluation/payment-clarification-20261118-v1.json'
SOURCE_NAMES = ('report.json', 'document-map.json', 'publication-rows.json',
                'query-vectors.json', 'retrieval-freeze.json')
SETTING_NAMES = (
    'rag_embedding_model', 'rag_embedding_dimension', 'rag_chunk_size', 'rag_chunk_overlap',
    'rag_retrieval_top_k', 'rag_final_top_k', 'rag_min_rerank_score', 'rag_prompt_max_bytes',
    'rag_reranker_provider', 'rag_reranker_model', 'rag_reranker_batch_size',
    'rag_reranker_timeout_seconds', 'rag_reranker_workers', 'rag_reranker_max_concurrency',
    'model_name', 'model_temperature', 'model_max_tokens', 'model_context_window_tokens',
    'model_context_overhead_tokens', 'rag_answer_timeout_seconds',
)


def fail(code):
    raise ValueError('RAG_CAP_' + code)


def schedule(entries):
    return [(entry, arm) for i, entry in enumerate(entries)
            for arm in (ARMS if i % 2 == 0 else ARMS[::-1])]


def settings_manifest(cfg):
    """只保存参数及地址指纹，不输出地址或凭据。"""
    from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT
    from ai_service.orchestration.customer_service_rag import (
        MAX_CONTEXTS, MAX_CONTEXT_CHARS, MAX_TOTAL_CONTEXT_CHARS,
    )
    return {**{name: getattr(cfg, name) for name in SETTING_NAMES},
        'answerPromptSha256': hashlib.sha256(CUSTOMER_SERVICE_SYSTEM_PROMPT.encode()).hexdigest(),
        'modelProviderSha256': hashlib.sha256(cfg.model_base_url.encode()).hexdigest(),
        'embeddingProviderSha256': hashlib.sha256(cfg.closeai_base_url.encode()).hexdigest(),
        'perDocument': 1, 'maxContexts': MAX_CONTEXTS, 'maxContextChars': MAX_CONTEXT_CHARS,
        'maxTotalContextChars': MAX_TOTAL_CONTEXT_CHARS,
        'search': {'metric_type': 'COSINE', 'params': {}, 'consistency_level': 'Strong'}}


def check_settings(cfg, original):
    current = settings_manifest(cfg)
    if (not cfg.rag_publication_enabled or cfg.rag_document_chunk_limit != 1
            or current != {key: original.get(key) for key in current}
            or cfg.rag_retrieval_top_k != 8 or cfg.rag_final_top_k != 3
            or cfg.rag_min_rerank_score is not None or cfg.rag_prompt_max_bytes != 16384
            or cfg.rag_reranker_provider != 'local_cross_encoder'
            or (current['maxContexts'], current['maxContextChars'], current['maxTotalContextChars'])
                != (3, 1200, 3000)):
        fail('SETTINGS_CHANGED')
    return current


def implementation_manifest():
    """仅关键执行代码参与漂移拒绝，HEAD 和无关脏文件另作诊断。"""
    modules = Path(__file__).parent
    paths = [*modules.glob('rag_cap_*.py'), modules / 'rag_rerank_contracts.py',
        modules / 'rag_rerank_budget.py', modules / 'rag_rerank_experiment.py',
        modules / 'customer_service_rag.py', modules / 'customer_service_trace.py',
        modules / 'document_etl.py', modules.parent / 'config.py',
        modules.parent / 'models/base.py', modules.parent / 'models/openai_compatible.py',
        modules.parent / 'models/reranker.py', modules.parent / 'prompts/customer_service.py',
        modules.parent / 'infrastructure/milvus_knowledge.py',
        modules.parent / 'api/knowledge_publication_schemas.py', ROOT / 'ai-service/uv.lock']
    return {str(p.relative_to(ROOT)).replace('\\', '/'): file_hash(p) for p in sorted(paths)}


def code_diagnostics():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf8').strip()
    return {'codeSha': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}


def evaluation_bindings():
    """后置支付澄清单独绑定，不静默覆盖原标注或把澄清发布成知识语料。"""
    return {name: {'byteSha256': file_hash(path), 'canonicalSha256': fingerprint(read_json(path))}
            for name, path in [('originalAnnotations', ANNOTATIONS),
                               ('paymentClarification', PAYMENT_CLARIFICATION)]}


@dataclass
class FrozenInputs:
    rubric: dict
    report: dict
    mapping: dict
    rows: list
    vectors: dict
    prepared: dict
    replay: dict
    source_files: dict
    source_report_sha256: str


def _load(cfg):
    from ai_service.api.knowledge_publication_schemas import ReadSnapshot, CandidateReceipt
    from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
    from ai_service.models.reranker import RerankedChunk
    from ai_service.orchestration.customer_service_rag import _filter_and_dedupe, _validate_ranked
    from ai_service.orchestration.rag_rerank_contracts import chunk_json

    files = {name: file_hash(SOURCE / name) for name in SOURCE_NAMES}
    files['replay.json'] = file_hash(REPLAY)
    report = read_json(SOURCE / 'report.json')
    # 来源 pin 来自已提交结论，不能信任报告自报或调用者传入的 hash。
    if (fingerprint(report) != SOURCE_REPORT_SHA256
            or report.get('schemaVersion') != 'rag-rerank-experiment/v1'
            or report.get('studyId') != 'rag-rerank-20261118-first-round-v1'):
        fail('SOURCE_REPORT_CHANGED')
    rubric = load_rubric()
    mapping = load_mapping(SOURCE / 'document-map.json', rubric)
    rows = read_json(SOURCE / 'publication-rows.json')
    matrix = read_json(SOURCE / 'query-vectors.json')
    retrieval = read_json(SOURCE / 'retrieval-freeze.json')
    check_settings(cfg, report['config'])
    if (fingerprint(rubric) != report['rubricSha256']
            or fingerprint(mapping) != report['mappingSha256']
            or fingerprint(rows) != report['corpusRowsSha256']
            or fingerprint(retrieval) != report['retrievalFreezeSha256']
            or retrieval != report['retrieval']):
        fail('SOURCE_HASH_MISMATCH')
    ids = [e['id'] for e in rubric['entries']]
    if (len(ids) != 90 or len(set(ids)) != 90 or len(matrix) != 90
            or set(retrieval) != set(ids)
            or any(not isinstance(v, list) or len(v) != cfg.rag_embedding_dimension
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)
                or not any(v) for v in matrix)):
        fail('VECTORS_INVALID')
    # 向量文件是题序矩阵；旧 hash 绑定 ID→向量，不能直接 hash 矩阵冒充核对。
    vectors = dict(zip(ids, matrix, strict=True))
    if fingerprint(vectors) != report['queryVectorsSha256']:
        fail('QUERY_VECTOR_MAPPING_CHANGED')
    snapshot = ReadSnapshot.model_validate(report['snapshot'], by_alias=True, by_name=False)
    receipt = CandidateReceipt.model_validate(report['receipt'], by_alias=True, by_name=False)
    if (snapshot.publication_id != receipt.publication_id
            or snapshot.collection_name != receipt.candidate_collection
            or snapshot.snapshot_hash != receipt.snapshot_hash
            or snapshot.epoch <= receipt.base_epoch
            or snapshot.embedding_dimension != cfg.rag_embedding_dimension
            or receipt.embedding_dimension != cfg.rag_embedding_dimension
            or snapshot.embedding_model_version != cfg.rag_embedding_model
            or receipt.embedding_model_version != cfg.rag_embedding_model
            or [d.model_dump(exclude={'chunk_count'}) for d in receipt.documents]
                != [d.model_dump() for d in snapshot.documents]):
        fail('PUBLICATION_IDENTITY_CHANGED')
    corpus = verify_corpus(snapshot, receipt, rows, mapping, cfg)
    by_id = {c['chunkId']: c for c in corpus}
    if (len(corpus) != 72 or len(by_id) != len(corpus) or report['corpusChunks'] != 72
            or any(sum(c['documentId'] == d.document_id for c in corpus) != d.chunk_count
                   for d in receipt.documents)):
        fail('CORPUS_INVALID')
    prepared = {}
    attributes = {'chunk_id': 'chunkId', 'document_id': 'documentId', 'document_version': 'documentVersion',
        'chunk_index': 'chunkIndex', 'content': 'content', 'file_name': 'fileName', 'file_type': 'fileType',
        'source_locator': 'sourceLocator', 'content_hash': 'contentHash', 'index_revision': 'indexRevision',
        'activation_revision': 'activationRevision', 'document_content_hash': 'documentContentHash'}
    for question_id in ids:
        cached = retrieval[question_id]
        retrieved = [RetrievedChunk(**c) for c in cached['retrieved']]
        if len(retrieved) != 8 or len({c.chunk_id for c in retrieved}) != 8:
            fail('RETRIEVAL_IDENTITIES_INVALID')
        previous = math.inf
        for c in retrieved:
            original = by_id.get(c.chunk_id)
            if (original is None or any(getattr(c, name) != original[key] for name, key in attributes.items())
                    or c.current_document_version != c.document_version or c.is_active is not True
                    or type(c.score) not in (int, float) or not math.isfinite(c.score)
                    or type(c.distance) not in (int, float) or not math.isfinite(c.distance)
                    or c.score > previous):
                fail('RETRIEVAL_IDENTITY_CHANGED')
            previous = c.score
        filtered = _filter_and_dedupe(retrieved)
        if not filtered or [chunk_json(c) for c in filtered] != cached['filtered']:
            fail('FILTERED_CHANGED')
        allowed = {c.chunk_id: c for c in filtered}
        ranked = _validate_ranked([RerankedChunk(allowed[r['chunkId']], r['score'])
                                  for r in cached['ranked']], filtered, len(filtered))
        if (len(ranked) != len(filtered)
                or [{'chunkId': r.chunk.chunk_id, 'score': r.score} for r in ranked] != cached['ranked']):
            fail('RANKED_CHANGED')
        prepared[question_id] = {'retrieved': retrieved, 'filtered': filtered, 'ranked': ranked}
    replay = read_json(REPLAY)
    if (fingerprint(replay) != REPLAY_SHA256
            or replay.get('schemaVersion') != 'rag-cap-evidence-replay/v1'
            or replay.get('sourceReportFingerprint') != SOURCE_REPORT_SHA256
            or replay.get('rubricFingerprint') != fingerprint(rubric)):
        fail('REPLAY_CHANGED')
    if files != {**{name: file_hash(SOURCE / name) for name in SOURCE_NAMES},
                  'replay.json': file_hash(REPLAY)}:
        fail('SOURCE_CHANGED_DURING_LOAD')
    return FrozenInputs(rubric, report, mapping, rows, vectors, prepared, replay, files, fingerprint(report))


def load_frozen_inputs(cfg):
    """只读历史 snapshot，不访问或声称复核当前权威 publication。"""
    try:
        return _load(cfg)
    except Exception as error:
        if isinstance(error, ValueError) and str(error).startswith('RAG_CAP_'):
            raise
        fail('FROZEN_INPUTS_INVALID')
