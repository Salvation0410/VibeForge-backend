"""第26轮答案单元离线实验：只构造候选和条件性来源投影，不调用模型。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import re
from types import SimpleNamespace

from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.base import CustomerServiceContext
from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration.customer_service_rag import _bounded_contexts, _clean_text
from ai_service.orchestration.document_etl import parse_document, split_sections
from ai_service.orchestration.rag_answer_first_evaluation import block_network
from ai_service.orchestration.rag_qa_linked_cap_evaluation import load_candidates, safe_path, validate_output
from ai_service.orchestration.rag_qa_linked_corpus import PRODUCT, ROOT, read_json, sha, write_json
from ai_service.prompts.customer_service import customer_service_user_prompt, fit_customer_service_contexts

VERSION = 'answer-unit-20261120-v1'
SOURCE_HASH = '738aa71190bc4ea87d18798ddd1c53d66221e2aa86f2c3d0f84047fba9222133'
RESULT_HASH = '24341db820dad9dfa75c1d72d071f8b5faea9631e65a429ad0675d228aa1bdfc'
RUBRIC_HASH = '51bb77aeab3c8bd1635aa3bd23dcfcecf4ace0ded72bb42aef3098ddb9edae68'
ARMS = ('cap1', 'cap2')
FIELDS = ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')
FROZEN_GROUPS = {
    '04-generation-types': [dict(title='三类生成方式与适用边界【当前功能与推荐规范】', headings=[
        'HTML 单文件【当前功能与推荐规范】', 'MULTI_FILE 多文件【当前功能与推荐规范】',
        'VUE_PROJECT Vue 工程【当前功能与推荐规范】'])],
    '07-deployment-download': [dict(title='部署前提、产物与失败排查【当前功能与推荐规范】', headings=[
        '部署前提与步骤【当前功能】', '三类产物【当前功能】', '失败排查【推荐规范】'])],
    '08-community-features': [dict(title='发帖、审核状态与查看权限【当前功能】', headings=[
        '发布帖子【当前功能】', '我的帖子与他人主页【当前功能】'])],
}
BOUNDARY = ('本报告仅为来源映射敏感性条件投影，不是新候选召回、排序、真实选择或回答效果。'
            '整段引文字面可用不等于语义覆盖；逻辑版本3不是Spring线上版本；人工复核待完成。')


def fail(code):
    raise ValueError('ANSWER_UNIT_' + code)


def clean(value):
    return _clean_text(value, len(value) + 1)


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        fail('SCHEMA')


def _sections(text):
    """仅接受已冻结语料使用的H1/H2，不推测代码围栏或未知标题结构。"""
    if not isinstance(text, str) or '\r' in text.replace('\r\n', ''):
        fail('MARKDOWN_STRUCTURE')
    lines = text.splitlines(keepends=True)
    if not lines or not re.fullmatch(r'# [^\r\n#]+\r?\n', lines[0]):
        fail('MARKDOWN_STRUCTURE')
    title = lines[0][2:].strip()
    headings, bodies, prefix = [], [], lines[0]
    for line in lines[1:]:
        match = re.fullmatch(r'## ([^\r\n#]+)\r?\n?', line)
        if match:
            heading = match[1]
            if heading != heading.strip() or heading in headings or heading == title:
                fail('DUPLICATE_HEADING')
            headings.append(heading)
            bodies.append('')
        else:
            if re.match(r'^\s*(?:#|```|~~~)', line):
                fail('MARKDOWN_STRUCTURE')
            if bodies:
                bodies[-1] += line
            elif line.strip():
                fail('MARKDOWN_STRUCTURE')
            else:
                prefix += line
    if not headings or any(not b.strip() for b in bodies):
        fail('MARKDOWN_STRUCTURE')
    return title, prefix, headings, dict(zip(headings, bodies, strict=True))


def merge_sections(text, groups):
    """纯转换：在首个来源位置插入合并段，来源标签及正文逐字保留。"""
    title, prefix, headings, bodies = _sections(text)
    if not isinstance(groups, list):
        fail('SCHEMA')
    used, targets, normalized = set(), set(), []
    for group in groups:
        _fields(group, 'title headings')
        target, sources = group['title'], group['headings']
        if (not isinstance(target, str) or not target.strip() or target != target.strip()
                or any(c in target for c in '\r\n#') or target in headings or target == title
                or target in targets or not isinstance(sources, list) or len(sources) < 2
                or any(not isinstance(h, str) or h not in bodies for h in sources)
                or len(set(sources)) != len(sources) or used.intersection(sources)):
            fail('GROUP_INVALID')
        used.update(sources)
        targets.add(target)
        ordered = [h for h in headings if h in sources]
        normalized.append((target, ordered))
    if not groups:
        return text, [dict(targetHeading=h, sourceHeadings=[h]) for h in headings]
    newline = '\r\n' if '\r\n' in text else '\n'
    starts = {sources[0]: (target, sources) for target, sources in normalized}
    output, provenance = prefix, []
    for heading in headings:
        if heading in starts:
            target, sources = starts[heading]
            output += '## ' + target + newline + newline
            for source in sources:
                output += source + newline + bodies[source]
                # 无结尾换行时补分隔符，不改变任何来源正文字符。
                if not output.endswith(newline):
                    output += newline
            provenance.append(dict(targetHeading=target, sourceHeadings=sources))
        elif heading not in used:
            output += '## ' + heading + newline + bodies[heading]
            provenance.append(dict(targetHeading=heading, sourceHeadings=[heading]))
    return output, provenance


def record(chunk):
    return dict(chunkId=chunk['chunk_id'], documentId=chunk['document_id'],
        documentVersion=chunk['document_version'], locator=chunk['source_locator'], content=chunk['content'])


def map_chunks(old, new, provenance):
    """用文档身份、来源小节和完整正文共同确定映射，拒绝歧义与丢失。"""
    mapping = {}
    for source in old:
        identity = source['document_id']
        old_title, separator, heading = source['source_locator'].rpartition(' / ')
        if not separator:
            fail('MAPPING_LOCATOR')
        targets = [p['targetHeading'] for p in provenance.get(identity, []) if heading in p['sourceHeadings']]
        if len(targets) != 1:
            fail('MAPPING_PROVENANCE')
        matches = [c for c in new if c['document_id'] == identity and c['document_version'] == 3
            and c['source_locator'] == old_title + ' / ' + targets[0] and source['content'] in c['content']]
        if len(matches) != 1 or source['chunk_id'] in mapping:
            fail('MAPPING_NOT_UNIQUE')
        mapping[source['chunk_id']] = matches[0]['chunk_id']
    if len(mapping) != len(old):
        fail('MAPPING_INCOMPLETE')
    return mapping


def validate_rules(rules):
    _fields(rules, 'schemaVersion candidateVersion sourceManifestSha256 humanReview documents')
    if (rules['schemaVersion'] != 'rag-answer-unit-rules/v1' or rules['candidateVersion'] != VERSION
            or rules['sourceManifestSha256'] != SOURCE_HASH or rules['humanReview'] != 'pending'
            or not isinstance(rules['documents'], list)):
        fail('RULES_INVALID')
    seen = set()
    for row in rules['documents']:
        _fields(row, 'documentId groups')
        if row['documentId'] in seen or row['documentId'] not in FROZEN_GROUPS:
            fail('RULE_IDENTITY')
        seen.add(row['documentId'])
        if row['groups'] != FROZEN_GROUPS[row['documentId']]:
            fail('RULE_GROUPS')
    if len(seen) != 3:
        fail('RULE_IDENTITY')


def build_candidate(corpus, rules, output, *, expected_documents=11, expected_chunks=63):
    """候选构造不接受题目或rubric；正式解析和分块仅消费两个固定配置。"""
    corpus, output = Path(corpus), Path(output)
    validate_rules(rules)
    manifest_path = safe_path(corpus / 'manifest.json', corpus)
    if sha(manifest_path) != rules['sourceManifestSha256']:
        fail('SOURCE_HASH')
    source_manifest = read_json(manifest_path)
    old = load_candidates(corpus, expected_documents=expected_documents, expected_chunks=expected_chunks)
    groups = {r['documentId']: r['groups'] for r in rules['documents']}
    options = SimpleNamespace(rag_chunk_size=1000, rag_chunk_overlap=150)
    documents, new, provenance = [], [], {}
    output.mkdir(parents=True, exist_ok=False)
    (output / 'documents').mkdir()
    for row in source_manifest['documents']:
        identity, metadata = row['documentId'], row['sourceMetadata']
        source = safe_path(corpus / metadata['path'], corpus)
        original = source.read_bytes()
        if sha(source) != row['candidateSha256']:
            fail('SOURCE_HASH')
        text = original.decode('utf8')
        converted, provenance[identity] = merge_sections(text, groups.get(identity, []))
        _, _, original_headings, original_bodies = _sections(text)
        if any(body not in converted for body in original_bodies.values()):
            fail('SOURCE_BODY_LOST')
        questions = re.findall(r'常见问法：[^\r\n]*', text)
        if any(converted.count(question) != text.count(question) for question in set(questions)):
            fail('SOURCE_QUESTION_CHANGED')
        destination = output / metadata['path']
        with destination.open('xb') as stream:
            stream.write(converted.encode('utf8') if identity in groups else original)
        sections = parse_document(destination, 'md')
        chunks = [asdict(c) for c in split_sections(sections, document_id=identity,
            document_version=3, source_name=destination.name, settings=options)]
        for group in groups.get(identity, []):
            target_sections = [s for s in sections if s.source_locator.split(' / ')[-1] == group['title']]
            target_chunks = [c for c in chunks if c['source_locator'].split(' / ')[-1] == group['title']]
            if (len(target_sections) != 1 or len(target_sections[0].content) > 1000
                    or len(target_chunks) != 1 or target_chunks[0]['content'] != target_sections[0].content):
                fail('GROUP_EXCEEDS_SINGLE_CHUNK')
        new.extend(chunks)
        documents.append(dict(documentId=identity, sourceMetadata=deepcopy(metadata),
            sourceSha256=sha(source), candidateSha256=sha(destination),
            changed=original != destination.read_bytes(), provenance=provenance[identity],
            fidelity=dict(allSourceBodiesPreserved=True, sourceSectionCount=len(original_headings),
                questionLineCountBefore=len(questions),
                questionLineCountAfter=len(re.findall(r'常见问法：[^\r\n]*', converted)))))
    mapping = map_chunks(old, new, provenance)
    manifest = dict(schemaVersion='rag-answer-unit-candidate-manifest/v1', candidateVersion=VERSION,
        identityScope='offline-logical-identities-not-spring-versions', documentVersion=3,
        humanReview='pending', sourceManifestSha256=sha(manifest_path),
        documents=documents, oldChunkCount=len(old), newChunkCount=len(new), oldToNew=mapping,
        chunkConfig=dict(size=1000, overlap=150))
    write_json(output / 'manifest.json', manifest)
    return manifest, old, new


def validate_contexts(contexts, old):
    known, seen = {c['chunk_id']: c for c in old}, set()
    if not isinstance(contexts, list) or len(contexts) > 3:
        fail('FINAL_CONTEXT_SCHEMA')
    for context in contexts:
        _fields(context, ' '.join(FIELDS))
        key = context['chunkId']
        if key not in known or key in seen:
            fail('FINAL_CONTEXT_IDENTITY')
        expected = record(known[key])
        expected['content'] = clean(expected['content'])
        if context != expected or type(context['documentVersion']) is not int:
            fail('FINAL_CONTEXT_DRIFT')
        seen.add(key)


def _metrics(contexts, question):
    payload = customer_service_user_prompt(question,
        [CustomerServiceContext(c['chunkId'], c['content']) for c in contexts], max_bytes=16384)
    return dict(chunkCount=len(contexts), documentCount=len({c['documentId'] for c in contexts}),
        chars=sum(len(c['content']) for c in contexts),
        contentBytes=sum(len(c['content'].encode('utf8')) for c in contexts), promptBytes=len(payload.encode('utf8')))


def project_contexts(question, before, mapping, new):
    """原finalContext顺序映射并去重，不补位、不重新执行选择和排序。"""
    known, selected, seen = {c['chunk_id']: c for c in new}, [], set()
    for context in before:
        key = mapping[context['chunkId']]
        if key not in seen:
            selected.append(known[key])
            seen.add(key)
    ranked = []
    for c in selected:
        retrieved = RetrievedChunk(chunk_id=c['chunk_id'], document_id=c['document_id'],
            document_version=c['document_version'], chunk_index=c['chunk_index'], content=c['content'],
            file_name=c['source_name'], file_type='MD', source_locator=c['source_locator'],
            content_hash='', distance=0.0, score=0.0)
        # 固定0仅满足正式预算函数的容器类型；不排序、不输出、不冒充真实分数。
        ranked.append(RerankedChunk(retrieved, 0.0))
    bounded, _ = _bounded_contexts(ranked)
    fitted = fit_customer_service_contexts(question, bounded, max_bytes=16384)
    if fitted is None:
        fail('PROMPT_BUDGET')
    after = [record(known[key]) | dict(content=content) for key, content in fitted]
    character = [record(known[c.chunk_id]) | dict(content=c.content) for c in bounded]
    return dict(after=after, mappedBeforeBudget=[record(c) for c in selected], characterBudget=character,
        deduplicatedCount=len(before) - len(selected),
        characterTruncated=[(c['chunk_id'], clean(c['content'])) for c in selected] !=
            [(c.chunk_id, c.content) for c in bounded],
        byteTruncated=[(c.chunk_id, c.content) for c in bounded] != fitted)


def project_comparison(comparison, questions, old, new, mapping, *, expected_cases=90):
    """只接收ID和题干；先完整核对旧上下文，再执行全部条件性投影。"""
    if (comparison.get('schemaVersion') != 'rag-qa-linked-cap-selection/v1'
            or comparison.get('sourceStudyId') != 'qa-linked-20261120-v1'):
        fail('COMPARISON_SCHEMA')
    ids = [q['id'] for q in questions]
    if len(ids) != expected_cases or len(set(ids)) != len(ids):
        fail('QUESTION_SET')
    rows = comparison.get('cases')
    if not isinstance(rows, list) or len(rows) != len(ids):
        fail('COMPARISON_CASES')
    by_id = {}
    for row in rows:
        if row['id'] in by_id or row['id'] not in ids or set(row['arms']) != set(ARMS):
            fail('COMPARISON_CASES')
        by_id[row['id']] = row
        for arm in ARMS:
            contexts = row['arms'][arm]['stages']['finalContext']
            if contexts != row['arms'][arm]['contexts']:
                fail('FINAL_CONTEXT_DRIFT')
            validate_contexts(contexts, old)
    cases = []
    for question in questions:
        row = by_id[question['id']]
        if row['question'] != question['question']:
            fail('QUESTION_DRIFT')
        arms = {}
        for arm in ARMS:
            before = deepcopy(row['arms'][arm]['stages']['finalContext'])
            projection = project_contexts(question['question'], before, mapping, new)
            first, second = _metrics(before, question['question']), _metrics(projection['after'], question['question'])
            arms[arm] = projection | dict(before=before, beforeMetrics=first, afterMetrics=second,
                delta={key: second[key] - first[key] for key in first})
        cases.append(dict(id=question['id'], question=question['question'], arms=arms))
    return cases


def _availability(evidence, contexts):
    values = []
    for ref in evidence:
        if not isinstance(ref.get('quote'), str) or not clean(ref['quote']):
            fail('EMPTY_QUOTE')
        values.append(any(c['documentId'] == ref['documentId'] and clean(ref['quote']) in clean(c['content'])
                          for c in contexts))
    if not values:
        fail('EMPTY_EVIDENCE')
    return dict(referenceWholeQuoteAvailability=values, anyWholeQuoteAvailable=any(values),
                allWholeQuotesAvailable=all(values))


def observe_facts(facts, old, new, arms):
    observed = []
    for fact in facts:
        if fact['basisType'] != 'public-document':
            continue
        evidence = fact['evidence']
        observed.append(dict(factId=fact['factId'], evidence=deepcopy(evidence),
            corpusBefore=_availability(evidence, [record(c) for c in old]),
            corpusAfter=_availability(evidence, [record(c) for c in new]),
            arms={arm: {side: _availability(evidence, values[side]) for side in ('before', 'after')}
                  for arm, values in arms.items()}))
    return observed


def evaluate(comparison, rubric, old, new, mapping, *, expected_cases=90, expected_facts=130):
    questions = [{k: e[k] for k in ('id', 'question')} for e in rubric['entries']]
    cases = project_comparison(comparison, questions, old, new, mapping, expected_cases=expected_cases)
    # 候选和所有投影均完成后，才使用事实标签观察全库和上下文的字面可用性。
    seen_facts = set()
    for case, entry in zip(cases, rubric['entries'], strict=True):
        for fact in entry['requiredFacts']:
            identity = fact['factId']
            if not isinstance(identity, str) or not identity or identity in seen_facts:
                fail('FACT_IDENTITY')
            seen_facts.add(identity)
        case['facts'] = observe_facts(entry['requiredFacts'], old, new, case['arms'])
        case['unobservedNonPublicFacts'] = [dict(factId=f['factId'], basisType=f['basisType'],
            reason='非公共文档事实，未生成回答，不能判断拒答或协议执行')
            for f in entry['requiredFacts'] if f['basisType'] != 'public-document']
    count = sum(len(c['facts']) for c in cases)
    if count != expected_facts:
        fail('PUBLIC_FACT_COUNT')
    return dict(schemaVersion='rag-answer-unit-projection/v1', candidateVersion=VERSION,
        boundary=BOUNDARY, cases=cases, publicFactCount=count, baselineExactMatches=len(cases),
        liveRetrievalMetrics=None, humanReview='pending',
        usage=dict(chat=0, embedding=0, bge=0, index=0, productionWrites=0),
        newProviderCalls=0, productionWrites=0,
        config=dict(chunkSize=1000, chunkOverlap=150, maxContextChars=1200,
                    maxTotalContextChars=3000, maxPromptBytes=16384))


def summarize(result):
    summary = {k: deepcopy(v) for k, v in result.items() if k != 'cases'}
    facts = [f for c in result['cases'] for f in c['facts']]
    summary['corpus'] = {side: dict(anyWholeQuoteAvailable=sum(f[side]['anyWholeQuoteAvailable'] for f in facts),
        allWholeQuotesAvailable=sum(f[side]['allWholeQuotesAvailable'] for f in facts))
        for side in ('corpusBefore', 'corpusAfter')}
    summary['arms'] = {}
    for arm in ARMS:
        values = [c['arms'][arm] for c in result['cases']]
        summary['arms'][arm] = dict(
            beforeWholeQuoteAvailable=sum(f['arms'][arm]['before']['anyWholeQuoteAvailable'] for f in facts),
            afterWholeQuoteAvailable=sum(f['arms'][arm]['after']['anyWholeQuoteAvailable'] for f in facts),
            quoteGains=sum(not f['arms'][arm]['before']['anyWholeQuoteAvailable'] and
                          f['arms'][arm]['after']['anyWholeQuoteAvailable'] for f in facts),
            quoteLosses=sum(f['arms'][arm]['before']['anyWholeQuoteAvailable'] and
                           not f['arms'][arm]['after']['anyWholeQuoteAvailable'] for f in facts),
            characterTruncatedCases=sum(v['characterTruncated'] for v in values),
            byteTruncatedCases=sum(v['byteTruncated'] for v in values),
            deduplicatedContexts=sum(v['deduplicatedCount'] for v in values),
            totals={side: {key: sum(v[side][key] for v in values) for key in values[0][side]}
                    for side in ('beforeMetrics', 'afterMetrics', 'delta')})
    return summary


def run(output, *, network_audit=None):
    output = validate_output(output)
    corpus = PRODUCT / 'experiments/qa-linked-20261120-v1'
    rules_path = PRODUCT / f'experiments/{VERSION}/merge-rules.json'
    comparison_path = ROOT / 'tmp/rag-qa-linked-cap-20261120/run-final/comparison.json'
    result_path = PRODUCT / 'evaluation/results/qa-linked-cap-selection-20261120-v1.json'
    rubric_path = PRODUCT / 'evaluation/rubric-20261118-v1.json'
    for path in (rules_path, comparison_path, result_path, rubric_path, corpus/'manifest.json'):
        safe_path(path, ROOT)
    if sha(result_path) != RESULT_HASH:
        fail('RESULT_HASH')
    if sha(rubric_path) != RUBRIC_HASH:
        fail('RUBRIC_HASH')
    binding, rules = read_json(result_path), read_json(rules_path)
    if sha(comparison_path) != binding['comparisonSha256']:
        fail('COMPARISON_HASH')
    rubric = read_json(rubric_path)
    protected_paths = {rules_path, comparison_path, result_path, rubric_path, Path(__file__), corpus/'manifest.json'}
    protected_paths.update({ROOT/'docs/superpowers/specs/2026-11-20-rag-answer-unit-design.md',
        ROOT/'docs/superpowers/plans/2026-11-20-rag-answer-unit-offline.md',
        ROOT/'ai-service/tests/test_rag_answer_unit_evaluation.py'})
    manifest = read_json(corpus/'manifest.json')
    protected_paths.update(safe_path(corpus / row['sourceMetadata']['path'], corpus) for row in manifest['documents'])
    # rubric冻结清单及本次实际依赖源码均纳入前后哈希保护，不读.env。
    for area, base in [('inputs', PRODUCT), ('documents', PRODUCT), ('sourceFiles', ROOT)]:
        protected_paths.update(safe_path(base / item['path'], ROOT) for item in rubric['freeze'][area])
    for name in ('document_etl.py', 'customer_service_rag.py', 'rag_qa_linked_cap_evaluation.py',
                 'rag_qa_linked_corpus.py', 'rag_answer_first_evaluation.py'):
        protected_paths.add(Path(__file__).with_name(name))
    protected_paths.add(Path(__file__).parents[1] / 'prompts/customer_service.py')
    protected = {str(safe_path(path, ROOT)): sha(path) for path in protected_paths}
    output.mkdir(parents=True, exist_ok=False)
    candidate, old, new = build_candidate(corpus, rules, output/'candidate')
    result = evaluate(read_json(comparison_path), rubric, old, new, candidate['oldToNew'])
    result['bindings'] = dict(sourceManifestSha256=SOURCE_HASH, capResultSha256=RESULT_HASH,
        comparisonSha256=binding['comparisonSha256'], rulesSha256=sha(rules_path),
        rubricSha256=sha(rubric_path), moduleSha256=sha(Path(__file__)),
        candidateManifestSha256=sha(output/'candidate/manifest.json'))
    result['candidate'] = dict(oldChunkCount=len(old), newChunkCount=len(new),
        changedDocumentIds=[d['documentId'] for d in candidate['documents'] if d['changed']])
    result['networkAudit'] = deepcopy(network_audit) if network_audit is not None else dict(enabled=False, attempts=None)
    if network_audit is not None and network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')
    write_json(output/'report.json', result)
    write_json(output/'summary.json', summarize(result))
    template = dict(schemaVersion='rag-answer-unit-human-review/v1', humanReview='pending',
        reviewer=None, reportSha256=sha(output/'report.json'), boundary=BOUNDARY,
        cases=[dict(id=c['id'], status='pending', semanticSupport=None, adjacentEvidenceNoise=None,
            lostEvidenceRisk=None, missingImportantInformation=None, documentDiversityRisk=None,
            facts=[dict(factId=f['factId'], judgment=None, notes='') for f in c['facts']], notes='')
            for c in result['cases']])
    write_json(output/'reviews-template.json', template)
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')
    write_json(output/'input-integrity.json', dict(unchanged=True, fileCount=len(protected), sha256=protected))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        with block_network() as audit:
            result = run(args.output, network_audit=audit)
        print(f'cases={len(result["cases"])} newProviderCalls=0 humanReview=pending')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # 多文件输出不是事务；失败残片保留，调用者必须选择新目录重跑。
        parser.exit(2, 'ANSWER_UNIT_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
