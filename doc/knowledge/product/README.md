# 产品知识与固定 90 条评测集

本目录将应用生成、社区等内容按逻辑分类组织，仍使用现有单一客服知识索引。分类不是多知识库检索功能；Markdown 和注册表中的状态字段不会自动触发 Milvus 过滤。

## 内容索引

| 分类 | 可导入正文 |
| --- | --- |
| 平台与账号 | [01 平台账号](documents/01-platform-account.md)、[11 功能边界 FAQ](documents/11-faq.md) |
| 应用生成 | [02 创建修改](documents/02-app-generation.md)、[03 需求规范](documents/03-generation-requirements.md)、[04 生成类型](documents/04-generation-types.md)、[05 素材保留](documents/05-images-preservation.md) |
| 预览与历史 | [06 预览停止与记录](documents/06-preview-history.md) |
| 部署与下载 | [07 部署访问和源码](documents/07-deployment-download.md) |
| 社区 | [08 当前功能](documents/08-community-features.md)、[09 内容规范](documents/09-community-guidelines.md) |
| 管理与知识 | [10 管理员说明](documents/10-admin-knowledge.md) |

[manifest.json](manifest.json) 保存逻辑 ID、分类、受众、版本和事实来源。`backend:` 表示本仓库，`frontend:` 表示独立前端仓库；这些是源码定位标识，不是可点击用户入口。每个业务小节标明当前功能、推荐规范或二者结合。推荐规范不构成审核时限、功能接入或生成无损保证。

[规划附录](planning/product-specification.md) 只作产品讨论，不在注册表内、不导入公共客服。它不承诺团队协作、自动内容审核或多库路由已实现。

## 使用说明页面

独立前端 `src/pages/DocsView.vue` 同步扩充普通用户使用说明；页面沿用既有路由与布局。当前下载/部署仅创建者可用，聊天 Markdown 导出允许创建者或管理员。页面按钮是否显示不能替代后端权限规则。

## 评测数据与人工复核

- [dataset.json](evaluation/dataset.json)：兼容现有 `customer-service-rag-eval/v1`，固定 90 条。逻辑文档 ID 用于维护内容，不是服务端真实 ID。
- [annotations.json](evaluation/annotations.json)：每条的分类、问法、关键事实、来源小节、禁止结论和审核状态。当前已由代理对照源码，仍需人工产品复核，未伪装成人工审核。
- [document-id-map.example.json](evaluation/document-id-map.example.json)：空映射示例，禁止拿假 ID 运行真实评测。

配额为平台 8、生成 24、预览 10、部署下载 14、社区 14、管理知识 5、边界安全 15。最后 15 条中有 6 条可回答的边界问题和 9 条真正的无答案/安全问题，不把“不支持某规划功能”一律当作拒答。每次更新保持总数 90，替换低价值重复样例并递增 datasetVersion；同步更新标注版本。

评测标注不是人工评审结果。人工抽检需记录条目 ID、答案要点满足情况、错误事实、漏步骤、状态混淆和是否通过。自动引用有效并不能证明回答事实正确。新版本适当分出调参样例与固定保留样例，避免反复用全部 90 条调整阈值后宣称泛化质量；当前数据规模下不额外增加样例。

## 本地校验

在 `ai-service` 执行：

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation
uv run python -m ai_service.orchestration.product_knowledge_evaluation --output ../tmp/product-knowledge-validation.json
```

默认校验文档引用、小节锚点、问题唯一性和固定配额，并用当前 ETL 解析/切分 11 篇正文。它不调用 Embedding、聊天模型或 Milvus，不写知识索引。输出 `liveMetrics: null` 是明确边界，不代表 90 条真实问答通过。

原 `ai-service/tests/fixtures/customer_service_eval.json` 的 6 条退款/密码/配送合成数据保留为评测算法测试夹具；它与真实产品库没有对应关系，不能直接拿它当产品召回基线。首次真实 90 条评测形成产品基线。

## 上传和更新流程

1. 产品人员复核正文及标注，确认当前功能来自页面和接口，推荐方式标记清楚。
2. 用管理员知识库页面逐份上传 `documents/` 的 11 个 Markdown 文件，不上传 README、注册表、评测集或 planning。上传必须经过 Spring，不允许 Python 直接写 MySQL、OSS 或生成应用目录。
3. 更新已有手册时使用替换原文档功能。盘点此前手册，避免新旧互相矛盾；旧文件禁用后等待异步任务完成并验证，不随意删除无法恢复的资料。
4. 等每份文件成功索引，记录实际 `documentId`、`documentVersion`、内容 SHA-256 和状态。以后台 ACTIVE/任务成功为准，不以“上传成功”为准。
5. 在本地忽略目录 `tmp/` 保存映射文件，每个逻辑 ID 对应真实 ID 字符串、版本整数和正文 SHA-256。摘要可从本地校验报告获取，并与后台上传内容摘要核对。
6. 核对实际文档映射后运行真实评测，逐条人工抽检，再发布该内容版本。

单项映射结构如下，尖括号位置必须替换为实际值，不可原样使用：

```json
{
  "schemaVersion": "product-knowledge-document-map/v1",
  "documents": {
    "01-platform-account": {
      "documentId": "<实际Spring文档ID>",
      "documentVersion": 1,
      "sha256": "<实际正文SHA-256>"
    }
  }
}
```

所有 11 个注册文档均需映射，版本必须匹配上传后的真实版本。校验器只验证本地摘要，不能代替管理员核对服务器实际内容；问答过程中遇到已映射 ID 的版本不一致会记录失败。只写 YAML 状态不具备隔离保证，所以本期通过不导入规划正文防止混淆。

## 真实 RAG 评测

准备现有 `.env` 中的真实 RAG 依赖、可用索引和实际映射。在 `ai-service` 执行：

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation --live --document-map ../tmp/product-document-map.json --output ../tmp/product-rag-report.json
```

该入口使用现有 FastAPI 生命周期初始化依赖，直接执行同一 RAG 问答服务，不改对外 API、不调用 Spring 用户操作或写 Milvus。运行会调用真实供应商并产生使用费用，需已有可用配置。每题默认 60 秒超时，可用 `--timeout` 调整；报告记录实际问答的原始检索排序、重排排序、答案、引用、降级和端到端延迟，不额外发起诊断检索。

有任一服务失败/超时则退出码为 1，聚合指标保持空，不能将故障算成正确拒答。无故障报告分别给出 `retrievalMetrics` 与 `rerankedMetrics`；两者基于文档去重排名，不是 chunk 级指标。重排报告记录阈值过滤之前的排序。基线没有无错误真实运行时保持待测。人工复核 `expectedFacts` 的正确率需另行统计，代码不使用模型自动评分替代产品判断。

目标参考：Recall@8 ≥ 0.85、MRR@3 ≥ 0.75、NDCG@3 ≥ 0.75、无答案准确率 ≥ 0.90、引用有效性 ≥ 0.98、人工抽检正确率 ≥ 0.85。15 条边界安全并非都为无答案，真正拒答只有 9 条，因此比例较粗，应连同错误条目数一起判断。

## 回滚与维护

内容有误时通过原文档替换恢复经审核版本，再等待索引终态并复测；不要直接操作 Milvus alias 或删除 collection 来修正文案。新功能需同步修改对应正文和评测条目，保持总数 90。规划落地前不得迁入当前说明；落地后补上实现来源并替换规划断言。

真实环境记录写入 `tmp/`，不提交日志、凭据、个人问答或运行报告。无法获得管理员会话、实际 ID 或外部依赖时应交付可导入内容和校验结果，明确说明索引和真实基线未执行。
