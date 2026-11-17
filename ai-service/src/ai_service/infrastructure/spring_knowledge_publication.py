"""只通过 Spring 内部接口观察持久发布事实；不可用时禁止 alias 回退。"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from ai_service.api.knowledge_publication_schemas import ReadSnapshot, PublicationFact, InventoryPage, PrepareExecutionResult
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError


class SpringKnowledgePublicationClient:
    def __init__(self, *, gateway_base_url, bearer_token, transport=None,
                 timeout_seconds=5.0, max_response_bytes=64 * 1024):
        parsed = urlsplit(gateway_base_url)
        prefix = parsed.path.split('/internal/', 1)[0].rstrip('/')
        self._url = urlunsplit((parsed.scheme, parsed.netloc,
            prefix + '/internal/customer-service/knowledge-publications', '', ''))
        self._max_bytes = max_response_bytes
        self._client = httpx.AsyncClient(headers={'Authorization': f'Bearer {bearer_token}'},
            transport=transport, timeout=httpx.Timeout(timeout_seconds), trust_env=False,
            follow_redirects=False)

    async def _request(self, method, suffix, schema, body=None):
        try:
            async with self._client.stream(method, self._url + '/' + suffix, **({'json': body} if body is not None else {})) as response:
                response.raise_for_status()
                declared = response.headers.get('content-length')
                if declared is not None and (not re.fullmatch(r'[0-9]+', declared)
                        or int(declared) > self._max_bytes):
                    raise ValueError
                # aiter_bytes 计算解压后预算，避免压缩响应绕过上限。
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > self._max_bytes:
                        raise ValueError
                    raw.extend(chunk)
            payload = json.loads(raw)
            if (not isinstance(payload, dict) or set(payload) - {'code', 'data', 'message'}
                    or type(payload.get('code')) is not int or payload['code'] != 0
                    or ('message' in payload and not isinstance(payload['message'], str))):
                raise ValueError
            return schema.model_validate(payload['data'], by_alias=True, by_name=False)
        except Exception:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE') from None

    async def _get(self, suffix, schema):
        return await self._request('GET', suffix, schema)

    async def arbitrate_prepare(self, request):
        result = await self._request('POST', 'prepare-execution', PrepareExecutionResult,
                                     request.model_dump(by_alias=True))
        if (result.granted != (result.status == 'STARTED')
                or (request.status != 'BEGIN' and result.granted)):
            raise MilvusKnowledgeError('KNOWLEDGE_PREPARE_EXECUTION_INVALID')
        return result

    async def snapshot(self):
        return await self._get('snapshot', ReadSnapshot)

    async def fact(self, publication_id):
        if not re.fullmatch(r'(task_[1-9][0-9]*|bootstrap_[0-9a-f]{32})', publication_id):
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE')
        fact = await self._get(publication_id, PublicationFact)
        if fact.publication_id != publication_id:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE')
        return fact

    async def require_legacy(self, *, publication_enabled=False):
        snapshot = await self.snapshot()
        if publication_enabled or snapshot.enabled:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_REQUIRED')
        return snapshot

    async def inventory(self, *, max_entries=1000):
        if type(max_entries) is not int or not 1 <= max_entries <= 1000:
            raise ValueError('invalid inventory bound')
        entries, cursor, control = [], 0, None
        while True:
            page = await self._get(f'inventory?afterEpoch={cursor}&limit=100', InventoryPage)
            observed = (page.enabled, page.epoch, page.publication_id)
            if control is not None and observed != control:
                raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_CHANGED')
            control = observed
            epochs = [item.epoch for item in page.entries]
            if (epochs != sorted(set(epochs)) or any(value <= cursor or value > page.epoch for value in epochs)
                    or page.next_epoch != (epochs[-1] if epochs else cursor)
                    or (page.has_more and not epochs)):
                raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_INVALID')
            entries.extend(page.entries)
            if (len(entries) > max_entries or len({item.publication_id for item in entries}) != len(entries)
                    or len({item.collection_name for item in entries}) != len(entries)):
                raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_BUDGET_EXCEEDED')
            if not page.has_more:
                return control, entries
            if len(entries) >= max_entries:
                raise MilvusKnowledgeError('KNOWLEDGE_INVENTORY_BUDGET_EXCEEDED')
            cursor = page.next_epoch

    async def ping(self):
        try:
            await self.snapshot()
            return True
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()
