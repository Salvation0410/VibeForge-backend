"""真实 HTTP 发送边界核对及有界响应观测；不记录请求头、地址或异常正文。"""
from __future__ import annotations

from copy import deepcopy
import asyncio
import hashlib
import json
import sys

import httpx

from ai_service.orchestration.rag_json_answer_contracts import MAX_RESPONSE_BYTES, fail


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, cfg, budget, expected_messages):
        self.inner, self.cfg, self.budget = inner, cfg, budget
        self.expected_messages = deepcopy(expected_messages)
        self.used = set()
        self.key = None
        self.observation = {}
        self.cancelled_error = None

    def begin(self, key):
        if key not in self.expected_messages or key in self.used:
            fail('HTTP_KEY_INVALID')
        self.key = key
        self.cancelled_error = None
        self.observation = {'reserved': False, 'attemptedSend': False, 'providerCalled': False,
                            'actualMessages': [], 'rawResponse': None, 'usage': None,
                            'responseMetadata': None, 'answerLatencyMs': None}

    async def handle_async_request(self, request):
        try:
            return await self._handle_async_request(request)
        except asyncio.CancelledError as error:
            # 部分 SDK/回调聚合路径会把内层取消包装成普通错误，保留原取消供适配器传播。
            self.cancelled_error = error
            raise

    async def _handle_async_request(self, request):
        observation, key = self.observation, self.key
        try:
            body = json.loads(request.content)
            messages = [{**m, 'role': 'user' if m['role'] == 'human' else m['role']}
                        for m in self.expected_messages[key]]
            arm = key.rsplit(':', 1)[1]
            expected = {'messages': messages, 'model': self.cfg.model_name,
                        'temperature': self.cfg.model_temperature, 'stream': False,
                        'max_completion_tokens': self.cfg.model_max_tokens}
            if arm == 'json_object':
                expected['response_format'] = {'type': 'json_object'}
            if key in self.used or body != expected or request.method != 'POST':
                fail('HTTP_BODY_CHANGED')
        except (KeyError, TypeError, json.JSONDecodeError):
            fail('HTTP_BODY_CHANGED')
        # 即使后续发送或 SDK 解析抛错，持久化成功的预占永不返还。
        self.budget.reserve_chat(key)
        observation['reserved'] = True
        self.used.add(key)
        observation.update(attemptedSend=True, providerCalled=True,
                           actualMessages=deepcopy(self.expected_messages[key]),
                           httpRequestSha256=hashlib.sha256(request.content).hexdigest())
        response = await self.inner.handle_async_request(request)
        observation['httpStatus'] = response.status_code
        # aiter_bytes 返回解压后的实体；上限和摘要针对该实体，不冒充压缩线缆字节。
        observation['httpResponseRepresentation'] = 'decoded_entity'
        digest, length, chunks = hashlib.sha256(), 0, []
        try:
            async for chunk in response.aiter_bytes():
                digest.update(chunk)
                length += len(chunk)
                if length > MAX_RESPONSE_BYTES:
                    observation.update(observedResponseBytes=length, observedResponsePrefixSha256=digest.hexdigest(),
                                       responseCaptureComplete=False,
                                       observationError='RESPONSE_TOO_LARGE')
                    fail('RESPONSE_TOO_LARGE')
                chunks.append(chunk)
        finally:
            active_error = sys.exc_info()[1]
            try:
                await response.aclose()
            except BaseException:
                if active_error is None:
                    raise
        raw = b''.join(chunks)
        observation.update(httpResponseBytes=length, httpResponseSha256=digest.hexdigest(), responseCaptureComplete=True)
        # 供应商错误正文不落盘；成功响应只保留答案及固定标量白名单。
        if response.status_code < 400:
            payload, choices = {}, None
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
                metadata = {}
                if isinstance(payload.get('model'), str) and len(payload['model']) <= 256:
                    metadata['model'] = payload['model']
                choices = payload.get('choices')
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    choice = choices[0]
                    if isinstance(choice.get('finish_reason'), str) and len(choice['finish_reason']) <= 64:
                        metadata['finish_reason'] = choice['finish_reason']
                    content = choice.get('message', {}).get('content')
                    if isinstance(content, str):
                        observation.update(rawResponse=content,
                                           rawResponseSha256=hashlib.sha256(content.encode()).hexdigest())
                usage = payload.get('usage', {})
                observation.update(responseMetadata=metadata,
                    usage={target: usage[k] for k, target in (('prompt_tokens', 'input_tokens'),
                           ('completion_tokens', 'output_tokens'), ('total_tokens', 'total_tokens'))
                           if isinstance(usage, dict) and type(usage.get(k)) is int})
            except (ValueError, TypeError, AttributeError):
                observation['observationError'] = 'RESPONSE_OBSERVATION_INVALID'
            if (observation['rawResponse'] is None or not isinstance(choices, list) or len(choices) != 1
                    or (payload.get('model') is not None and
                        (type(payload['model']) is not str or len(payload['model']) > 256))):
                observation['observationError'] = 'RESPONSE_OBSERVATION_INVALID'
            if observation.get('observationError'):
                fail('RESPONSE_OBSERVATION_INVALID')
            if metadata.get('finish_reason') in ('length', 'content_filter'):
                observation['responseTerminationError'] = metadata['finish_reason']
                if arm == 'text':
                    # text 的普通 create 路径不提供 parse 门禁；本实验仅拒绝长度截断与内容过滤。
                    fail('RESPONSE_TERMINATED')
                # JSON 组保留原 SDK parse 的提前错误路径，观测已在它之前完整落入本题记录。
        # 原压缩及传输长度头不再适用于解压实体，否则 SDK 会再次解压或误读长度。
        headers = response.headers.copy()
        for name in ('content-encoding', 'content-length', 'transfer-encoding'):
            headers.pop(name, None)
        return httpx.Response(response.status_code, headers=headers, content=raw, request=request)

    async def aclose(self):
        await self.inner.aclose()
