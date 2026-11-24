# Vue 图片使用策略修复交接

## 2026-11-24：自动素材不再阻断后台生成

### 问题与证据

请求 `1ef4e814-e402-45d4-9cc0-f0c768f3424d` 对应应用 `482744429714173952`，原始需求为“做一个电商运营后台，看订单和销售数据”。本轮通过 Spring 应用接口、Redis 工具缓存、Python 运行日志及 PostgreSQL 固定摘要只读核对，未修改业务记录或生成项目。

首次产物上下文 `exists=false`，平台自动返回六张素材，主要涉及办公和数据分析场景。工具缓存有 39 次调用，其中 15 次写入/修改、24 次读取。通过 Spring 获取的完整有限快照包含 11 个文件，没有图片 URL 或图片标签。模型完成后用图状态仍未满足，原循环两次提醒后以 `VUE_IMAGE_USAGE_MISSING` 失败。该请求没有进入构建或质量审查，不把已有源码视为已验收。

### 方案取舍与实现

用户已确认区分“平台自动素材”和“用户明确配图”。保留首次默认搜图，为自动素材提供 `optional` 策略；不采用按“后台”等主题穷举排除的方案，也不强行插入装饰照片。

1. `orchestration/nodes/input.py` 使用本轮原始 prompt 的既有 `requests_image_assets` 规则生成 Vue 上下文 `imageUsagePolicy`，明确配图为 `required`，其他为 `optional`。不接收 metadata 同名值作为策略，不改原始需求或工具契约。
2. `orchestration/vue_tool_loop.py` 阶段初始化只将显式 `optional` 纳入用图放行条件。缺字段或未知值按旧逻辑；明确要求且有素材时继续检查成功写入参数中是否包含搜索 URL，两次提醒和原失败码不变。可选放行不等于实际用图，不会因此设置 `imageAssetsApplied`。
3. 生成及修复共用提示明确可选素材不应引入无关照片。单 Reviewer 投影保留策略，多 Reviewer 传递同一策略，共享审查提示禁止仅因可选素材未用而阻断。
4. 阶段切换和 checkpoint 沿现有上下文保留策略，不修改公共事件，不新增恢复入口、模型调用、工具次数或正式修复预算。静态类型不增加 Vue 策略字段。

### 边界与仍可能失败的场景

- 明确配图识别沿用有限规则，不是通用语义理解；规则未识别的表达仍可能落入可选策略。搜索无结果沿原降级逻辑，并不保证用户的配图目标已实现。
- 默认搜图仍存在一次可能无用的外部调用；本轮不优化搜索成本或相关性。
- 强制用图检查仍只核对成功写入参数中的 URL，不证明可见、网络可达、DOM 实际使用或最终图片未被删除。
- 旧失败任务不会自动恢复。Vue 工程直接写入，失败无版本回滚保证；本轮没有手工修补应用来冒充平台修复。
- 构建、审查、上下文、工具或供应商预算仍可能在后续阶段失败。

### 验证与提交/部署状态

已新增离线回归并先确认旧实现失败：21 项中 13 项失败、8 项通过，包含后台未采用自动素材无法完成、策略缺失和提示未透传。修复后相关流程测试通过，覆盖默认素材、明确配图、修复继承、metadata 不可信字段、缺失/未知策略、单/多审查供应商消息、静态类型不变及内存 checkpoint 重建续跑。

- 相关测试：`uv run pytest tests/test_vue_image_policy.py tests/test_vue_review_evidence.py tests/test_api.py -o addopts='' -q --tb=short`，176 passed。新增策略测试共 23 项，另补充明确配图搜索无结果的 API 回归。
- 全量：`uv run pytest -o addopts='' -q --tb=short`，2913 passed、30 skipped、4 failed（306.04 秒）。四项均在 `tests/test_reranker.py`：`test_spawn_worker_normal_result_and_unavailable_protocol`、`test_spawn_worker_timeout_shutdown_terminates_permanent_child`、`test_large_blocked_ipc_send_does_not_block_loop_and_aborts_child`、`test_gpu_owner_lock_rejects_same_key_then_releases`；失败为 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，其中子进程堆栈明确位于 `_GpuOwnerLock.acquire`。
- 只读 Windows 命名锁核对确认当前 `.env` 对应重排器 mutex 已存在。对上述四项单独设置本轮临时 `AI_SERVICE_RAG_RERANKER_MODEL=vue-image-policy-test-<随机UUID>` 后重跑，4 passed（45.94 秒）。测试使用 Fake worker，该变量仅隔离测试锁身份，不加载或调用真实模型，不修改 `.env`、重排器代码或运行服务。保留第一次全量失败记录，不表述成默认环境全量一次通过。
- `uv run python -m compileall -q src`、`uv lock --check`、`git diff --check` 均退出 0。依赖无变更。
- 本轮按仓库约定提交到当前 `codex/python-ai-only` 分支，提交说明为 `fix(ai): 将默认搜图素材改为可选以避免后台生成失败`；具体哈希见交付回复和 Git 历史。

本轮未发起真实模型生成，未重启 Python 服务，未进行页面浏览器验收；离线工具构建返回为 Fake，不可声称该电商后台已构建成功。部署需重启 Python 后对新请求生效，既有失败任务不自动恢复。

### 下一轮建议

重启 Python 后，从正常前端入口重新发起同类后台生成，核对构建和实际订单/销售筛选交互。原因是本轮解决完成条件误加约束，不能证明原模型产物功能正确。若后续需要减少无用搜图，先记录可选素材采用率和搜索失败率，再决定调整默认搜索；复杂需求、有限意图规则和网络故障仍可能失败。
