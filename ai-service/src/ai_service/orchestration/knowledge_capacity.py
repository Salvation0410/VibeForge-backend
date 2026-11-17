"""手动只读容量观测；没有已观察事实不构成可删除证明。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

from ai_service.config import get_settings
from ai_service.infrastructure.knowledge_publication_store import PublicationStore, KIND, _encoded
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeStore, MilvusKnowledgeError
from ai_service.infrastructure.spring_knowledge_publication import SpringKnowledgePublicationClient


def age_seconds(created_timestamp, now):
    # Milvus Hybrid Timestamp的高位为UTC毫秒，低18位为逻辑时钟；未知值不猜测。
    if type(created_timestamp) is not int or created_timestamp <= 0:
        return None
    seconds = (created_timestamp >> 18) / 1000
    return int(now - seconds) if 1_577_836_800 <= seconds <= now else None


async def observe(settings, client, store, *, measure_json=False, max_candidates=1000,
                  total_json_budget=256 * 1024 * 1024):
    if type(max_candidates) is not int or not 1 <= max_candidates <= 1000:
        raise ValueError('invalid candidate bound')
    before = await client.snapshot()
    control, facts = await client.inventory()
    if control != (before.enabled, before.epoch, before.publication_id):
        raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_CHANGED')
    names = await store._call('list_collections')
    pattern = re.compile(re.escape(store._alias[:210]) + r'_pub_[0-9a-f]{32}')
    candidates = sorted(name for name in names if pattern.fullmatch(name))
    if len(candidates) > max_candidates:
        raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_BUDGET_EXCEEDED')
    known = {item.collection_name: item for item in facts}
    if before.enabled and before.collection_name not in known:
        raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_INVALID')
    rows, total, measured = [], 0, 0
    publications = PublicationStore(settings, store)
    for name in sorted(set(candidates) | set(known)):
        reference = ('CURRENT' if name == before.collection_name else
                     'HISTORICAL_FACT' if name in known else 'NO_OBSERVED_FACT')
        item = dict(collection=name, reference=reference, status='OK', ageSeconds=None,
                    rowCount=None, serializedJsonBytes=None)
        if name not in candidates:
            item['status'] = 'MISSING_TARGET'
            rows.append(item)
            continue
        try:
            description = await store._call('describe_collection', name)
            metadata = json.loads(description['description'])
            # 容量观测不要求旧版本模型等于当前模型，但身份/kind必须属于当前命名空间。
            if (metadata.get('kind') != KIND or metadata.get('aliasHash') != store._alias_hash()
                    or not re.fullmatch(r'task_[1-9][0-9]*|bootstrap_[0-9a-f]{32}', metadata.get('publicationId', ''))
                    or (name in known and metadata['publicationId'] != known[name].publication_id)):
                raise ValueError('metadata identity')
            item['ageSeconds'] = age_seconds(description.get('created_timestamp'), time.time())
            stats = await store._call('get_collection_stats', name)
            count = stats.get('row_count')
            if type(count) is str and count.isdecimal():
                count = int(count)
            if type(count) is not int or count < 0:
                raise ValueError('row count')
            item['rowCount'] = count
            if measure_json:
                if total >= total_json_budget:
                    item['status'] = 'TOTAL_JSON_BUDGET_EXCEEDED'
                else:
                    body = await publications.scan(name, max_bytes=total_json_budget-total)
                    size = sum(len(_encoded(row)) for row in body) + max(len(body)-1, 0) + 2
                    if total + size > total_json_budget:
                        item['status'] = 'TOTAL_JSON_BUDGET_EXCEEDED'
                        # 达上限后不继续下载后续集合，避免预算成为仅展示限制。
                        total = total_json_budget
                    else:
                        item['serializedJsonBytes'] = size
                        total += size
                        measured += 1
                    del body
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # 不将供应商异常正文、内容或认证材料写入报告。
            item['status'] = 'OBSERVATION_UNAVAILABLE'
            if isinstance(error,MilvusKnowledgeError) and str(error)=='KNOWLEDGE_PUBLICATION_BUDGET_EXCEEDED':
                item['status']='JSON_BUDGET_EXCEEDED'
                total=total_json_budget
        rows.append(item)
    after_names = await store._call('list_collections')
    after_candidates = sorted(name for name in after_names if pattern.fullmatch(name))
    stable = before == await client.snapshot() and candidates == after_candidates
    return dict(schemaVersion=1, observedAt=datetime.now(timezone.utc).isoformat(),
                epoch=before.epoch, publicationId=before.publication_id, complete=stable and all(r['status']=='OK' for r in rows),
                controlAndCollectionListStable=stable, measureJson=measure_json,
                measuredCollections=measured, measuredSerializedJsonBytes=sum(r['serializedJsonBytes'] or 0 for r in rows),
                diskBytes=None, deletionSafetyEstablished=False, entries=rows)


async def main_async(args):
    settings = get_settings()
    store = client = None
    try:
        store = MilvusKnowledgeStore(settings)
        client = SpringKnowledgePublicationClient(gateway_base_url=str(settings.spring_gateway_base_url),
                                                 bearer_token=settings.spring_gateway_bearer_token)
        report = await observe(settings, client, store, measure_json=args.measure_json)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({key: report[key] for key in ('complete','epoch','measuredCollections','measuredSerializedJsonBytes')}, ensure_ascii=False))
    finally:
        try:
            if client is not None:
                await client.close()
        finally:
            if store is not None:
                await store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='显式允许只读Spring/Milvus请求')
    parser.add_argument('--measure-json', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error('真实观测需要 --live；不调用模型、不写知识集合')
    try:
        asyncio.run(main_async(args))
    except Exception:
        # CLI也不回显供应商异常正文；失败不写一份伪装完整的新报告。
        raise SystemExit('KNOWLEDGE_CAPACITY_UNAVAILABLE') from None


if __name__ == '__main__':
    main()
