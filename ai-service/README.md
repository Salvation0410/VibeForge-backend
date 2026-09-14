# yu-ai-service

Python 3.12 orchestration service for AI code generation. It owns model calls and a real LangGraph `StateGraph`; Spring remains the system of record and exclusively owns project files, builds, and MySQL data.

## Run locally

```powershell
Copy-Item .env.example .env
uv sync --frozen --python 3.12
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

Run tests without network model calls:

```powershell
uv run pytest
```

All protected endpoints require `Authorization: Bearer <AI_SERVICE_INTERNAL_BEARER_TOKEN>`. Health endpoints are intentionally unauthenticated for probes.

## API

- `POST /internal/v1/route`: route a prompt to `HTML`, `MULTI_FILE`, or `VUE_PROJECT`.
- `POST /internal/v1/generations:stream`: return newline-delimited JSON events (`application/x-ndjson`).
- `POST /internal/v1/generations/{requestId}:cancel`: cooperatively cancel a request. The terminal event has type `failed`, `data.status=cancelled`, and `error.code=cancelled`.
- `GET /internal/v1/health/live`: process liveness.
- `GET /internal/v1/health/ready`: checkpoint dependency readiness.

Every event carries `requestId`, a monotonically increasing `sequence`, `node`, `data`, and optional `error`. Event types are `content_delta`, `tool_started`, `tool_finished`, `node_status`, `completed`, and `failed`.

LangGraph checkpoints, pending writes, and the latest-checkpoint pointer use the `yu-ai:langgraph:*` namespace; key components are URL-safe encoded and every key expires after 24 hours by default. The LangGraph `thread_id` is always `{appId}:{requestId}`. When Redis is optional and unavailable, the service logs a warning, continues without recovery, and reports `not_ready`. When `AI_SERVICE_REDIS_REQUIRED=true`, startup or checkpoint writes fail explicitly. Set `AI_SERVICE_REDIS_ENABLED=false` only for tests or an intentional non-persistent local environment.

## Spring tool boundary

Tools are invoked through `POST {AI_SERVICE_SPRING_GATEWAY_BASE_URL}/invoke` with the configured bearer token. Each request includes a deterministic `toolCallId`; Spring can use it as its idempotency key. Python never reads or writes generated project files and never connects to MySQL.

The Vue agent is capped by `AI_SERVICE_VUE_MAX_TOOL_CALLS` (default 4). Quality repair is capped by `AI_SERVICE_MAX_REPAIR_ATTEMPTS` (default 2).
