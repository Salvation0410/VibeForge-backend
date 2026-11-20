"""只读核对已捕获片段；未知记录不作否定证据，原文存活不代表语义充分。"""
from __future__ import annotations

from ai_service.orchestration.customer_service_rag import _clean_text

STAGES = ('retrieved', 'filtered', 'reranked', 'thresholded', 'selected',
          'characterBudget', 'byteBudget')
IDENTITY = ('chunkId', 'documentId', 'documentVersion')
KEPT_STAGES = frozenset(STAGES) - {'retrieved', 'reranked'}


def _identity(row):
    return tuple(row.get(k) for k in IDENTITY)


def _valid_chunks(rows, mapping):
    if not isinstance(rows, list) or len(rows) >= 100:
        return False
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return False
        key = row.get('chunkId')
        if not isinstance(row.get('documentId'), str):
            return False
        doc = mapping.get(row.get('documentId'))
        if (not isinstance(key, str) or not key or key in seen or not doc
                or type(row.get('documentVersion')) is not int or row['documentVersion'] != doc[1]
                or not isinstance(row.get('content'), str) or not isinstance(row.get('locator'), str)
                or row.get('truncated', False) is not False):
            return False
        seen.add(key)
    return True


def _observed_stage(row, name, mapping, available):
    trace, stages = row.get('trace', {}), row.get('stages', {})
    if (not mapping or not isinstance(trace, dict) or trace.get('schemaVersion') != 'customer-service-rag-trace/v1'
            or not isinstance(stages, dict) or row.get('stagesTruncated', False) is not False):
        return None
    metadata = trace.get(name)
    if not isinstance(metadata, list) or len(metadata) >= 100:
        return None
    # filtered 历史上只有元数据；从已完整核对的 retrieved 取原文，不能从后续排序反推。
    if name == 'filtered':
        source = available.get('retrieved')
        if source is None:
            return None
        by_id = {c['chunkId']: c for c in source}
        if any(not isinstance(m, dict) or not isinstance(m.get('chunkId'), str)
               or m['chunkId'] not in by_id for m in metadata):
            return None
        if len(metadata) != len(source):
            return None
        chunks = [by_id[m['chunkId']] for m in metadata if m.get('kept') is True]
    else:
        chunks = stages.get(name)
    if not _valid_chunks(chunks, mapping):
        return None
    if any(not isinstance(m, dict) for m in metadata):
        return None
    if name in KEPT_STAGES and any(type(m.get('kept')) is not bool for m in metadata):
        return None
    identities = [_identity(m) for m in metadata]
    if (any(not isinstance(k[0], str) or not isinstance(k[1], str) or type(k[2]) is not int
            for k in identities) or len(set(identities)) != len(identities)):
        return None
    retained = [m for m in metadata if name not in KEPT_STAGES or m['kept']]
    if [_identity(m) for m in retained] != [_identity(c) for c in chunks]:
        return None
    for meta, chunk in zip(retained, chunks, strict=True):
        if type(meta.get('length')) is not int or meta['length'] != len(chunk['content']):
            return None
        if name in {'characterBudget', 'byteBudget'} and (type(meta.get('afterChars')) is not int
                                                          or meta['afterChars'] != len(chunk['content'])):
            return None
    # 同一片段在非预算阶段不得换正文/身份；缺前阶段时只认可本阶段独立记录。
    prior_name = STAGES[STAGES.index(name) - 1] if name != 'retrieved' else None
    prior = available.get(prior_name)
    if prior is not None:
        previous = {c['chunkId']: c for c in prior}
        if name in KEPT_STAGES and set(identities) != {_identity(c) for c in prior}:
            return None
        for chunk in chunks:
            old = previous.get(chunk['chunkId'])
            if old is None or _identity(old) != _identity(chunk) or old['locator'] != chunk['locator']:
                return None
            content = old['content']
            if name in {'characterBudget', 'byteBudget'}:
                content = _clean_text(content, len(content) + 1)
                if not content.startswith(chunk['content']):
                    return None
            elif content != chunk['content']:
                return None
    if name == 'byteBudget':
        final = row.get('contexts')
        fields = (*IDENTITY, 'locator', 'content')
        if (not _valid_chunks(final, mapping)
                or [{k: c[k] for k in fields} for c in final] !=
                   [{k: c[k] for k in fields} for c in chunks]):
            return None
    return chunks


def observe_evidence(row, entry, mapping):
    """报告标注支持片段的首次可观测丢失；缺任一前置阶段时不假定首次原因。"""
    available = {}
    for stage in STAGES:
        available[stage] = _observed_stage(row, stage, mapping, available)
    facts = []
    for fact in entry['requiredFacts']:
        if fact['basisType'] != 'public-document':
            continue
        survival = {}
        for stage, chunks in available.items():
            survival[stage] = None if chunks is None else any(
                mapping[c['documentId']][0] == ref['documentId']
                and _clean_text(ref['quote'], len(ref['quote']) + 1) in
                    _clean_text(c['content'], len(c['content']) + 1)
                for c in chunks for ref in fact['evidence'])
        first, previous, gap = None, None, False
        for stage, present in survival.items():
            if present is None:
                gap = True
            elif not present and first is None:
                first = 'UNKNOWN' if gap else ('NOT_IN_RETRIEVED' if stage == 'retrieved' else
                                             stage if previous is True else 'UNKNOWN')
            previous = present
        if first is None and gap:
            first = 'UNKNOWN'
        selection_reasons = []
        if first == 'selected':
            supported_ids = {c['chunkId'] for c in available['thresholded']
                if any(mapping[c['documentId']][0] == ref['documentId']
                       and _clean_text(ref['quote'], len(ref['quote']) + 1) in
                           _clean_text(c['content'], len(c['content']) + 1)
                       for ref in fact['evidence'])}
            for meta in row['trace']['selected']:
                if meta['chunkId'] in supported_ids and meta['kept'] is False:
                    raw = meta.get('dropReason')
                    selection_reasons.append(raw if isinstance(raw, str) and raw in
                                             {'duplicate_document', 'document_chunk_limit', 'top_k'} else 'UNKNOWN')
        facts.append({'factId': fact['factId'], 'snippetPresent': survival,
                      'firstObservedLoss': first, 'finalSnippetPresent': survival['byteBudget'],
                      'selectionDropReasons': sorted(set(selection_reasons))})
    return {'stages': {name: {'state': 'UNKNOWN' if rows is None else 'OBSERVED',
                             'retainedChunks': None if rows is None else len(rows)}
                       for name, rows in available.items()}, 'facts': facts,
            'meaning': '标注整段支持片段存活；缺失不证明语义事实缺失，存在不证明回答正确'}
