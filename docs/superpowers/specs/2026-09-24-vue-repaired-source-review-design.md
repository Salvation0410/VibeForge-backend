# Vue 修复后源码质量检查设计

## 背景与目标

Vue 项目的生成和修复通过 Spring 内部文件工具直接修改项目目录。当前 `quality_review` 仍把 Python 状态中的 `artifact` 交给 Reviewer；Vue 修复分支不会用最终项目源码更新该字段，因此 Reviewer 可能看到修复前文本、工具结果和构建摘要，却看不到修复后的真实文件内容。

构建成功只能证明项目可以编译，不能证明功能、文字、图片和交互仍满足用户需求。本次优化的目标是：Vue 至少完成一次修复并重新构建成功后，由 Spring 提供有界、确定性的最终源码视图，让 Reviewer 基于修复后的实际文件判断 `PASS` 或 `REPAIR`。

## 范围

本次修改 Spring 内部工具契约、源码快照读取组件、Python 工作流和对应测试、README 与交接文档。

不修改公共 SSE 事件类型，不改变 HTML 和 MULTI_FILE 的质量检查，不增加修复次数，不改变模型可调用工具白名单，也不让 Python 直接访问项目目录。

真实模型、真实 Redis、Spring/Python 真实 HTTP 和前端生成验收不在本次自动验证范围内。

## 方案

新增工作流专用、模型不可调用的内部工具 `vue_source_snapshot`。Spring 是项目文件的唯一所有者，负责文件选择、读取、排序和限额；Python 只消费通过共享 Schema 校验的结构化响应。

该工具只允许 `codeGenType=VUE_PROJECT`，请求仍必须携带 Bearer、`appId`、`requestId` 和 `toolCallId`。模型不能直接选择或传入文件路径。

## Spring 源码快照

### 文件选择

Spring 从应用的 Vue 项目根目录读取源码，并遵守以下规则：

- 不跟随符号链接。
- 跳过隐藏目录、`node_modules`、`dist` 和 `build`。
- 排除隐藏文件、符号链接、常见二进制文件和 `package-lock.json`、`pnpm-lock.yaml`、`yarn.lock` 等锁文件。
- 只读取明确允许的文本源码和配置类型，包括 `.vue`、`.ts`、`.tsx`、`.js`、`.jsx`、`.css`、`.scss`、`.less`、`.html` 和 `.json`。
- `package.json`、`src/main.*`、`src/App.vue` 优先，其余文件按可移植相对路径稳定排序。

目录遍历必须在进入目录前执行排除判断，不能先递归遍历 `node_modules` 或 `dist` 后再过滤。

### 有界内容

快照使用固定上限：

- 最多包含 24 个文件。
- 单文件最多包含 12,000 个字符。
- 所有文件内容合计最多包含 60,000 个字符。

单文件超限时保留头尾内容并插入明确截断标记。达到文件数或总字符上限后停止加入更多内容，并通过元数据说明遗漏。排序和截断算法必须确定，相同项目状态应产生相同快照。

响应结构包含：

```json
{
  "files": [
    {
      "path": "src/App.vue",
      "content": "...",
      "truncated": false
    }
  ],
  "eligibleFileCount": 8,
  "includedFileCount": 8,
  "omittedFileCount": 0,
  "truncated": false
}
```

找不到项目目录、没有符合条件的源码文件、读取失败或请求了非 Vue 类型时返回稳定业务错误，不能返回看似有效的空快照。

### 源码留存边界

现有工具幂等服务会把成功结果保存到 Redis。`vue_source_snapshot` 返回大段源码，若沿用成功结果缓存，会扩大源码留存范围并抵消上一轮 checkpoint 留存优化。

因此该工具采用只读瞬时执行：通过认证、请求字段和共享 Schema 校验后直接读取，不把成功快照写入工具幂等 Redis。重复请求允许重新读取当前文件状态，因为该操作没有副作用。其他文件写入、删除、构建、发布和既有读取工具的幂等行为保持不变。

## 共享契约

Spring `InternalAiTool` 和 `internal-ai-tools-v1.json` 同步增加 `vue_source_snapshot`：

- `modelCallable=false`
- 请求仅允许 `codeGenType=VUE_PROJECT`
- 响应要求 `files`、`eligibleFileCount`、`includedFileCount`、`omittedFileCount` 和 `truncated`
- 每个文件要求 `path`、`content` 和 `truncated`

Java 与 Python 继续加载同一份版本化 JSON Schema。契约测试必须证明工具枚举、请求示例、响应示例和 Python 结果校验保持一致。

## Python 工作流

`quality_review` 仅在以下条件全部成立时调用快照工具：

1. `code_gen_type` 为 `VUE_PROJECT`。
2. `repair_count > 0`。
3. 产物硬校验结果为有效。
4. 最近一次项目构建结果为成功。

调用使用确定性的 `toolCallId`：

```text
<requestId>:vue-source-snapshot:<repairCount>
```

快照序列化后作为 `model.review()` 的 `artifact` 参数。Reviewer context 继续携带原有业务上下文、硬校验和构建摘要，但不再重复嵌入源码快照。

快照只作为当前 `quality_review` 节点的局部变量，不写入 `WorkflowState`、业务 checkpoint 或完成事件。首次生成未发生修复时维持现有质量检查，避免每次 Vue 请求都增加文件读取和模型 token 成本。

## 事件与失败语义

快照调用继续产生 `tool_started` 和 `tool_finished`，但 `tool_finished.data.result` 只能包含：

- `eligibleFileCount`
- `includedFileCount`
- `omittedFileCount`
- `truncated`

事件不得包含 `files` 或任何源码内容。为此，Python 工具调用封装需要支持针对特定调用提供结果摘要，同时仍把完整工具响应返回给工作流节点。

失败规则：

- 快照请求、Schema 校验或读取失败时，本次工作流进入失败终态，不允许回退到旧 `artifact` 后继续 Reviewer。
- 快照合法但被截断时允许 Reviewer 继续判断，并在 artifact 中保留截断元数据。
- Reviewer 返回 `REPAIR` 时继续现有最多两次修复循环。
- 取消检查、Vue 模型工具总预算、构建和完成事件语义保持不变；工作流专用快照调用不计入模型可调用工具预算。

## 测试设计

### Java

- 工具枚举与共享 Schema 包含 `vue_source_snapshot`，且模型不可调用。
- 只允许 Vue 请求，非 Vue 类型返回稳定参数错误。
- 目录过滤在遍历阶段跳过隐藏目录、`node_modules`、`dist`、`build` 和符号链接。
- 入口文件优先，剩余文件稳定排序。
- 文件数、单文件字符数和总字符数上限及截断元数据准确。
- 空项目、读取失败和不存在的项目不能返回有效空快照。
- 控制器调用该只读工具时不进入 `ToolInvocationIdempotencyService`；其他工具仍保持原有幂等入口。
- Spring 返回值通过共享响应 Schema。

### Python

- 未修复的 Vue、HTML 和 MULTI_FILE 不调用快照工具。
- Vue 修复并重建成功后恰好调用一次快照工具。
- Reviewer 的 artifact 包含修复后的文件内容而不是旧 `state.artifact`。
- Reviewer context 不重复携带完整快照。
- `tool_finished` 只包含计数和截断摘要，不泄露源码。
- 快照失败或响应不符合 Schema 时生成失败，不回退旧 artifact。
- Reviewer 返回 `REPAIR` 时仍遵守两次修复和总工具预算。

### 验证命令

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check

Set-Location ..
mvn "-Dtest=InternalAiToolContractTest,InternalAiToolsControllerTest" test
mvn clean -DskipTests compile
git diff --check
```

真实 Redis、真实模型、Spring/Python HTTP 和前端首次生成/二次修改由后续真实环境验收完成。

## 文档交付

实现完成后更新：

- `ai-service/README.md`：记录修复后源码快照和事件脱敏边界。
- `doc/ai-service-phase-one-handoff.md`：将该 P1 项标记完成，更新验证基线，并保留真实环境和前端人工验收事项。
