from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
)
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class CheckpointStore(Protocol):
    """工作流依赖的 checkpoint 生命周期与持久化协议。"""
    available: bool

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def save(self, thread_id: str, state: dict[str, Any]) -> None: ...

    async def ping(self) -> bool: ...

    def get_graph_saver(self) -> BaseCheckpointSaver | None: ...


class DisabledCheckpoint:
    """用于测试或本地禁用场景，不声明具备状态恢复能力。"""

    available = True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        return None

    async def ping(self) -> bool:
        return True

    def get_graph_saver(self) -> BaseCheckpointSaver | None:
        return None


class RedisGraphSaver(BaseCheckpointSaver):
    """使用 Redis 字符串和独立 TTL 实现的异步 LangGraph saver。"""

    def __init__(self, client: Redis, *, ttl_seconds: int):
        super().__init__()
        self._client = client
        self._ttl_seconds = ttl_seconds

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """读取指定 thread 的 checkpoint 及其待提交写入。"""
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id is None:
            checkpoint_id = await self._client.get(self._latest_key(thread_id, namespace))
        if not checkpoint_id:
            return None
        raw = await self._client.get(self._checkpoint_key(thread_id, namespace, str(checkpoint_id)))
        if raw is None:
            return None
        payload = json.loads(raw)
        pending_writes: list[tuple[str, str, Any]] = []
        pattern = self._writes_prefix(thread_id, namespace, str(checkpoint_id)) + "*"
        async for key in self._client.scan_iter(match=pattern):
            write_raw = await self._client.get(key)
            if write_raw:
                pending_writes.append(tuple(self._decode(write_raw)))  # type: ignore[arg-type]
        return CheckpointTuple(
            config=self._decode(payload["config"]),
            checkpoint=self._decode(payload["checkpoint"]),
            metadata=self._decode(payload["metadata"]),
            parent_config=self._decode(payload["parentConfig"]) if payload.get("parentConfig") else None,
            pending_writes=pending_writes,
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None or (limit is not None and limit <= 0):
            return
        item = await self.aget_tuple(config)
        if item is not None and (
            filter is None or all(item.metadata.get(key) == value for key, value in filter.items())
        ):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """保存 checkpoint，并更新当前 namespace 的最新 checkpoint 指针。"""
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(checkpoint["id"])
        next_config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }
        parent_id = get_checkpoint_id(config)
        parent_config: RunnableConfig | None = None
        if parent_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": namespace,
                    "checkpoint_id": parent_id,
                }
            }
        payload = json.dumps(
            {
                "config": self._encode(next_config),
                "checkpoint": self._encode(checkpoint),
                "metadata": self._encode(metadata),
                "parentConfig": self._encode(parent_config) if parent_config else None,
            },
            separators=(",", ":"),
        )
        pipeline = self._client.pipeline()
        pipeline.setex(self._checkpoint_key(thread_id, namespace, checkpoint_id), self._ttl_seconds, payload)
        pipeline.setex(self._latest_key(thread_id, namespace), self._ttl_seconds, checkpoint_id)
        await pipeline.execute()
        return next_config

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """保存 LangGraph 节点产生的待提交 channel 写入。"""
        configurable = config["configurable"]
        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id is None:
            return
        prefix = self._writes_prefix(
            str(configurable["thread_id"]),
            str(configurable.get("checkpoint_ns", "")),
            checkpoint_id,
        )
        pipeline = self._client.pipeline()
        for index, (channel, value) in enumerate(writes):
            key = prefix + _component(f"{task_id}:{index}")
            pipeline.setex(key, self._ttl_seconds, self._encode((task_id, channel, value)))
        await pipeline.execute()

    async def adelete_thread(self, thread_id: str) -> None:
        """删除指定 thread 下由本服务创建的全部 LangGraph 数据。"""
        keys = [key async for key in self._client.scan_iter(match=f"yu-ai:langgraph:*:{_component(thread_id)}:*")]
        if keys:
            await self._client.delete(*keys)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError("RedisGraphSaver is async-only")

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("RedisGraphSaver is async-only")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("RedisGraphSaver is async-only")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("RedisGraphSaver is async-only")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("RedisGraphSaver is async-only")

    def _encode(self, value: Any) -> str:
        type_name, data = self.serde.dumps_typed(value)
        return json.dumps(
            {"type": type_name, "data": base64.b64encode(data).decode("ascii")},
            separators=(",", ":"),
        )

    def _decode(self, value: str) -> Any:
        payload = json.loads(value)
        return self.serde.loads_typed(
            (payload["type"], base64.b64decode(payload["data"].encode("ascii")))
        )

    @staticmethod
    def _checkpoint_key(thread_id: str, namespace: str, checkpoint_id: str) -> str:
        return f"yu-ai:langgraph:checkpoint:{_component(thread_id)}:{_component(namespace)}:{_component(checkpoint_id)}"

    @staticmethod
    def _latest_key(thread_id: str, namespace: str) -> str:
        return f"yu-ai:langgraph:latest:{_component(thread_id)}:{_component(namespace)}"

    @staticmethod
    def _writes_prefix(thread_id: str, namespace: str, checkpoint_id: str) -> str:
        return f"yu-ai:langgraph:writes:{_component(thread_id)}:{_component(namespace)}:{_component(checkpoint_id)}:"


class RedisCheckpoint:
    """管理 Redis 可用性、业务状态快照和 LangGraph saver 生命周期。"""
    key_prefix = "yu-ai:langgraph:checkpoint:"

    def __init__(self, url: str, *, required: bool, ttl_seconds: int):
        self._client = Redis.from_url(url, decode_responses=True)
        self._required = required
        self._ttl_seconds = ttl_seconds
        self.available = False
        self._graph_saver = RedisGraphSaver(self._client, ttl_seconds=ttl_seconds)

    async def start(self) -> None:
        """探测 Redis；required 模式不可用时阻止服务启动。"""
        try:
            await self._client.ping()
            self.available = True
        except Exception as exc:
            self.available = False
            if self._required:
                raise RuntimeError("Redis checkpoint is required but unavailable") from exc
            logger.warning("Redis checkpoint unavailable; running without recovery: %s", exc)

    async def close(self) -> None:
        """关闭 Redis 客户端连接。"""
        await self._client.aclose()

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        """按 TTL 保存精简业务状态；可选模式写入失败时自动降级。"""
        if not self.available:
            return
        try:
            await self._client.setex(
                self.key_prefix + _component(thread_id),
                self._ttl_seconds,
                json.dumps(state, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            self.available = False
            if self._required:
                raise RuntimeError("Redis checkpoint write failed") from exc
            logger.warning("Redis checkpoint write failed; degrading: %s", exc)

    async def ping(self) -> bool:
        """返回当前 Redis 连接的实时可用状态。"""
        if not self.available:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            self.available = False
            return False

    def get_graph_saver(self) -> BaseCheckpointSaver | None:
        """仅在 Redis 可用时向 LangGraph 提供 saver。"""
        return self._graph_saver if self.available else None


def _component(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=") or "_"
