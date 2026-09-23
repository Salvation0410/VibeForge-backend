# LangGraph Completed 事件轻量化设计

## 1. 背景

Python LangGraph 当前在 `completed.data.artifact` 中携带完整候选产物。对于 HTML 和 MULTI_FILE，同一完整候选已经通过最后一个 `content_delta` 传给 Java；Java 网关缓存最后一个静态候选，只把 `completed` 当作成功终态。Vue 分支也不读取完成事件中的 `artifact`。

因此该字段会重复传输完整源码，增加 Python 序列化、网络传输、Java JSON 解析和瞬时内存开销，但没有实际消费者。

## 2. 目标

1. 从内部 `completed` 事件移除完整 `artifact`。
2. 使用发布版本、哈希、构建状态和计数信息表达成功摘要。
3. 保持 Java 对外 SSE、聊天历史和前端行为不变。
4. 保持发布完成后外围 checkpoint 失败时的成功补偿语义。

## 3. 非目标

本轮不包含：

- 修改 `content_delta`；
- 修改公共 SSE 的 `done` 或 `business-error`；
- 修改前端；
- 删除工作流状态中的 `artifact`；
- 瘦身业务 checkpoint 或 LangGraph saver；
- 扩展完整工具 JSON Schema；
- 修改发布、构建、修复或取消流程。

## 4. 新完成事件

所有分支共同包含：

```json
{
  "threadId": "42:req-123",
  "codeGenType": "HTML",
  "qualityPassed": true,
  "repairCount": 0,
  "toolCallCount": 0
}
```

HTML 和 MULTI_FILE 在此基础上增加：

```json
{
  "published": true,
  "versionId": "release-id",
  "artifactHashes": {
    "index.html": "sha256..."
  }
}
```

VUE_PROJECT 增加：

```json
{
  "built": true
}
```

任何分支的 `completed.data` 都不得包含 `artifact`、完整源码或完整工具参数。

## 5. Python 工作流

### 5.1 静态产物发布摘要

`artifact_publish` 成功后，`remember_publication` 从 Spring 权威响应提取：

- `published`
- `versionId`
- `hashes`，对外字段名规范化为 `artifactHashes`

同时记录通用终态元数据。不得从工作流 `state.artifact` 复制源码。

### 5.2 Vue 完成摘要

Vue 使用 `state.build.built` 生成 `built`，并携带 `toolCallCount`。构建失败仍走现有失败或修复分支，不得产生 `completed`。

### 5.3 补偿完成事件

Spring 已发布静态产物后，如果节点状态事件或 checkpoint 保存失败，`_complete_committed_publication()` 继续最多补发一次 `completed`。补发使用发布时保存的小型摘要，与正常完成事件字段一致。

## 6. Java 兼容性

`LangGraphAiGenerationGateway` 当前：

1. 从静态分支的最后一个 `content_delta` 保存候选；
2. 把 `completed` 仅解析为成功终态；
3. 收到成功终态后把最后候选交给现有流处理器；
4. Vue 不从完成事件读取源码。

因此 Java 生产代码不需要读取新字段，也无需兼容开关。Java 测试需要锁定以下行为：

- 新版无 artifact 的 `completed` 正常完成；
- 旧版带 artifact 的 `completed` 仍可忽略；
- 没有静态 `content_delta` 时不得从 `completed` 伪造候选内容。

## 7. 错误与安全边界

- `completed` 只能在静态产物发布成功或 Vue 构建成功后发送；
- 发布响应缺少 `published=true` 继续视为失败；
- 缺少 `versionId` 或 `hashes` 时不得把源码放回完成事件作为补偿；
- 完成摘要不得包含提示词、源码、Cookie、Bearer Token 或工具参数；
- 发布成功后的补偿完成事件不能被外围持久化错误反转成失败。

## 8. 测试

Python 测试覆盖：

- HTML、MULTI_FILE、VUE_PROJECT 的完成数据均不包含 `artifact`；
- HTML/MULTI_FILE 包含 `published=true`、`versionId` 和 `artifactHashes`；
- Vue 包含 `built=true` 和工具调用计数；
- 发布后 checkpoint 失败补发的完成数据不包含 artifact；
- 构建或发布失败不产生 completed；
- 现有三分支、事件顺序、修复上限和工具预算测试不回归。

Java 测试覆盖：

- 无 artifact 的 completed 正常结束流；
- 静态候选仍来自最后一个 content_delta；
- completed 不提供候选时，Java 不生成虚假内容；
- failed 和流结束未 completed 的现有错误语义不回归。

## 9. 验证与完成标准

执行：

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check

Set-Location ..
mvn "-Dtest=LangGraphAiGenerationGatewayTest" test
mvn clean -DskipTests compile
git diff --check
```

完成标准：

- `completed.data` 不再出现 `artifact`；
- 三类成功摘要字段符合本设计；
- Java 和前端外部协议不变；
- 所有离线验证通过；
- 真实模型和端到端验收保持待执行。
