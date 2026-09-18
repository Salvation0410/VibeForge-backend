from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 `AI_SERVICE_` 环境变量和可选 `.env` 文件加载服务配置。"""
    model_config = SettingsConfigDict(
        env_prefix="AI_SERVICE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    internal_bearer_token: str = Field(min_length=1)
    spring_gateway_base_url: HttpUrl
    spring_gateway_bearer_token: str = Field(min_length=1)

    model_api_key: str = Field(default="not-configured", min_length=1)
    model_base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-chat"
    model_temperature: float = 0.1

    redis_enabled: bool = True
    redis_required: bool = False
    redis_url: str = "redis://localhost:6379/2"
    checkpoint_ttl_seconds: int = Field(default=86400, ge=60)
    vue_max_tool_calls: int = Field(default=4, ge=1, le=20)
    max_repair_attempts: int = Field(default=2, ge=0, le=5)


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置对象，避免重复解析环境变量。"""
    return Settings()  # type: ignore[call-arg]

