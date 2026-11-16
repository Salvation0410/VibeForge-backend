"""客服RAG评估的数据校验与确定性指标；不初始化外部依赖。"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = ROOT / 'ai-service/evaluation/customer-service-boundaries-v2.json'
CORPUS = ROOT / 'doc/knowledge/product/documents'


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def load_study(path: Path = DEFAULT_DATASET, *, corpus: Path = CORPUS):
    """校验每条原文证据、来源哈希和问题分组；错误发生在任何付费调用之前。"""
    from ai_service.config import Settings
    from ai_service.orchestration.document_etl import parse_document, split_sections

    data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schemaVersion') != 'rag-evidence-eval/v2' or not data.get('datasetVersion'):
        raise ValueError('RAG_EVAL_SCHEMA_INVALID')
    cfg = Settings(_env_file=None, internal_bearer_token='offline',
                   spring_gateway_base_url='http://localhost:1',
                   spring_gateway_bearer_token='offline', customer_service_rag_enabled=False,
                   rag_chunk_size=1000, rag_chunk_overlap=150)
    chunks = []
    for name, digest in data['corpusHashes'].items():
        if not isinstance(name, str) or '/' in name or '\\' in name or '..' in name:
            raise ValueError('RAG_EVAL_SOURCE_INVALID')
        source = corpus / f'{name}.md'
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('RAG_EVAL_CORPUS_CHANGED')
        for chunk in split_sections(parse_document(source, 'md'), document_id=name,
                                    document_version=1, source_name=source.name, settings=cfg):
            chunks.append({'chunkId': chunk.chunk_id, 'documentId': name,
                           'locator': chunk.source_locator, 'content': chunk.content})
    seen, questions, partitions = set(), set(), {}
    for entry in data['entries']:
        if (not entry.get('id') or entry['id'] in seen or not entry.get('question')
                or entry['question'] in questions or type(entry.get('expectedAnswerable')) is not bool
                or not entry.get('groupId') or not entry.get('partition')
                or not entry.get('reviewStatus')
                or bool(entry.get('requiredFacts')) != entry['expectedAnswerable']):
            raise ValueError('RAG_EVAL_ENTRY_INVALID')
        seen.add(entry['id']); questions.add(entry['question'])
        partitions.setdefault(entry['groupId'], set()).add(entry['partition'])
        fact_ids = set()
        for fact in entry['requiredFacts']:
            if (not fact.get('id') or fact['id'] in fact_ids or not fact.get('statement')
                    or not fact.get('evidence')):
                raise ValueError('RAG_EVAL_FACT_INVALID')
            fact_ids.add(fact['id'])
            for ref in fact['evidence']:
                if not ref.get('quote') or not any(matches(chunk, ref) for chunk in chunks):
                    raise ValueError('RAG_EVAL_EVIDENCE_INVALID')
    if not seen or any(len(values) != 1 for values in partitions.values()):
        raise ValueError('RAG_EVAL_SPLIT_INVALID')
    return data, chunks


def matches(chunk: dict, evidence: dict) -> bool:
    return (chunk['documentId'] == evidence['documentId']
            and chunk['locator'] == evidence['locator']
            and evidence['quote'] in chunk['content'])


def evidence_metrics(entry: dict, rows: list[dict], *, k: int = 3,
                     corpus_chunks: list[dict] | None = None) -> dict:
    """精确span覆盖用于诊断证据存活，不把字面命中当成答案忠实度。"""
    facts = entry['requiredFacts']
    if not facts:
        return {'applicable': False, 'requiredFacts': 0}
    # 重复身份只能占一个排名，不能通过重复相同片段提高AP或召回。
    unique, seen = [], set()
    for row in rows:
        if row['chunkId'] not in seen:
            unique.append(row); seen.add(row['chunkId'])
    unique = unique[:k]
    universe = corpus_chunks if corpus_chunks is not None else rows
    relevant = {row['chunkId'] for row in universe
                if any(matches(row, ref) for fact in facts for ref in fact['evidence'])}
    flags = [row['chunkId'] in relevant for row in unique]
    hits = sum(flags)
    covered = [fact['id'] for fact in facts if any(matches(row, ref)
               for ref in fact['evidence'] for row in unique)]
    precision = hits / k
    recall = hits / len(relevant) if relevant else 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank, hit in enumerate(flags, 1) if hit)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    ap = sum(sum(flags[:rank]) / rank for rank, hit in enumerate(flags, 1) if hit)
    return {'applicable': True, 'k': k, 'annotatedPrecisionAtK': precision,
            'annotatedRecallAtK': recall,
            'annotatedF1AtK': 2 * precision * recall / (precision + recall) if precision + recall else 0,
            'mrrAtK': next((1 / rank for rank, hit in enumerate(flags, 1) if hit), 0),
            'apAtK': ap / len(relevant) if relevant else 0,
            'ndcgAtK': dcg / ideal if ideal else 0,
            'requiredFacts': len(facts), 'coveredFactIds': covered,
            'evidenceCoverage': len(covered) / len(facts),
            'allRequiredEvidence': len(covered) == len(facts)}


def select_contexts(ranked: list[dict], *, per_document: int, limit: int = 3) -> list[dict]:
    """实验选择器只改变单文档配额；不得借标注事实或答案参与选择。"""
    selected, counts, seen = [], Counter(), set()
    for row in ranked:
        doc = row['documentId']
        if row['chunkId'] in seen or counts[doc] >= per_document:
            continue
        selected.append(row); counts[doc] += 1; seen.add(row['chunkId'])
        if len(selected) >= limit:
            break
    return selected


def bounded_contexts(question: str, selected: list[dict], max_bytes: int = 16384) -> list[dict]:
    from ai_service.models.base import CustomerServiceContext
    from ai_service.orchestration.customer_service_rag import _clean_text
    from ai_service.prompts.customer_service import fit_customer_service_contexts

    before, remaining = [], 3000
    for row in selected[:3]:
        text = _clean_text(row['content'], min(1200, remaining))
        if text:
            before.append(CustomerServiceContext(row['chunkId'], text))
            remaining -= len(text)
    fitted = fit_customer_service_contexts(question, before, max_bytes=max_bytes)
    lookup = {row['chunkId']: row for row in selected}
    return [{**lookup[key], 'content': text} for key, text in (fitted or []) if text]


def aggregate(data: dict, cases: list[dict]) -> dict:
    """分母由预定样本决定；服务错误不算正确拒答，缺失评分单独计数。"""
    lookup = {(row['id'], row['arm']): row for row in cases}
    result = {}
    for arm in ('baseline', 'two_chunks'):
        entries = data['entries']
        answerable = [e for e in entries if e['expectedAnswerable']]
        refusals = [e for e in entries if not e['expectedAnswerable']]
        rows = [lookup.get((e['id'], arm), {}) for e in entries]
        stages = {}
        for stage in ('retrieved8', 'reranked3', 'selected3', 'final3'):
            measured = [lookup.get((e['id'], arm), {}).get('metrics', {}).get(stage) for e in answerable]
            valid = [m for m in measured if m and m.get('applicable')]
            stages[stage] = {'measured': len(valid), 'expected': len(answerable),
                             'meanEvidenceCoverage': fmean(m['evidenceCoverage'] for m in valid) if valid else None,
                             'completeEvidenceCases': sum(m['allRequiredEvidence'] for m in valid)}
            for metric in ('annotatedPrecisionAtK', 'annotatedRecallAtK', 'mrrAtK', 'apAtK', 'ndcgAtK'):
                stages[stage][metric] = fmean(m[metric] for m in valid) if valid else None
        good_answer = lambda e: lookup.get((e['id'], arm), {}).get('status') == 'ok'
        answered = sum(good_answer(e) and lookup[(e['id'], arm)]['answered'] for e in answerable)
        refused = sum(good_answer(e) and not lookup[(e['id'], arm)]['answered'] for e in refusals)
        semantic = [r['judge'] for r in rows if r.get('judge', {}).get('status') == 'ok']
        supported = sum(j['supportedClaims'] for j in semantic)
        claims = sum(j['claimCount'] for j in semantic)
        latencies = sorted(r['answerLatencyMs'] for r in rows if r.get('status') == 'ok')
        result[arm] = {'samples': len(entries), 'missingResults': sum(not r for r in rows),
                       'errors': sum(r.get('status') == 'error' for r in rows),
                       'answersNotRun': sum(r.get('status') == 'not_run' for r in rows),
                       'answerableAnswered': answered, 'answerableDenominator': len(answerable),
                       'correctRefusal': refused, 'refusalDenominator': len(refusals),
                       'stages': stages, 'semanticJudged': len(semantic),
                       'faithfulnessClaimsMicro': supported / claims if claims else None,
                       'answerRelevanceMean': fmean(j['answerRelevance'] for j in semantic) if semantic else None,
                       'requiredFactCoverageMean': fmean(j['requiredFactCoverage'] for j in semantic
                                                       if j['requiredFactCoverage'] is not None)
                          if any(j['requiredFactCoverage'] is not None for j in semantic) else None,
                       'judgeMissingOrError': len(entries) - len(semantic),
                       'meanAnswerLatencyMs': fmean(latencies) if latencies else None,
                       'p95AnswerLatencyMs': latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None}
    return result


def analyze_study(data: dict, cases: list[dict]) -> dict:
    """逐题配对及问题组切片；单轮诊断差异不作显著性或泛化结论。"""
    lookup = {(r['id'], r['arm']): r for r in cases}
    changes = {'evidenceWins': [], 'evidenceLosses': [], 'answerChanges': []}
    bad = []
    for entry in data['entries']:
        key = entry['id']
        left, right = lookup.get((key, 'baseline')), lookup.get((key, 'two_chunks'))
        if not left or not right:
            continue
        if entry['expectedAnswerable']:
            a = left['metrics']['final3']['evidenceCoverage']
            b = right['metrics']['final3']['evidenceCoverage']
            if a != b:
                changes['evidenceWins' if b > a else 'evidenceLosses'].append(
                    {'id': key, 'before': a, 'after': b})
            if min(a, b) < 1 or not left.get('answered') or not right.get('answered'):
                bad.append({'id': key, 'groupId': entry['groupId'],
                            'reason': 'evidence-or-answer-incomplete',
                            'baselineCoverage': a, 'candidateCoverage': b})
        elif any(r.get('status') == 'ok' and r.get('answered') for r in (left, right)):
            bad.append({'id': key, 'groupId': entry['groupId'], 'reason': 'refusal-contract-failed'})
        if left.get('answered') != right.get('answered'):
            changes['answerChanges'].append(key)
    return {'metrics': aggregate(data, cases), 'pairedChanges': changes, 'badCases': bad,
            'byGroup': {group: aggregate({**data, 'entries': [e for e in data['entries']
                                                            if e['groupId'] == group]}, cases)
                        for group in sorted({e['groupId'] for e in data['entries']})},
            'semanticReview': 'model-judged-human-review-pending',
            'metricBoundary': '原文span与不完备qrels仅诊断证据，不是Ragas语义分数或人工正确率'}


def standard_samples(data: dict, cases: list[dict]) -> list[dict]:
    """导出Ragas常见单轮字段；保留契约与适用性标签，不把失败伪装成空回答。"""
    entries = {e['id']: e for e in data['entries']}
    return [{'user_input': entries[r['id']]['question'],
             'retrieved_contexts': [c['content'] for c in r['contexts']],
             'response': r.get('answer') if r.get('status') == 'ok' else None,
             'reference': entries[r['id']]['referenceAnswer'],
             'metadata': {'id': r['id'], 'arm': r['arm'], 'status': r['status'],
                          'expectedAnswerable': entries[r['id']]['expectedAnswerable'],
                          'answered': r.get('answered'),
                          'datasetVersion': data['datasetVersion'],
                          'contextsAreActualFinalInputs': True,
                          'reviewStatus': entries[r['id']]['reviewStatus']}}
            for r in cases if r['id'] in entries]
