"""只通过 Spring 内部接口观察持久发布事实；不可用时禁止 alias 回退。"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from ai_service.api.knowledge_publication_schemas import ReadSnapshot, PublicationFact
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

    async def _get(self, suffix, schema):
        try:
            async with self._client.stream('GET', self._url + '/' + suffix) as response:
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

    async def ping(self):
        try:
            await self.snapshot()
            return True
        except Exception:
            return False

    async def close(self):
        await self._client.aclose()
