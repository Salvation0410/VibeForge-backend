# 第23轮问答关联候选语料

这是独立实验版本，尚未发布或导入生产。`documents/` 中11个文件保留原有实质说明，60个原问法关联到同文档对应说明，形成65处同块关联；1个无法在原篇找到直接解释的问法保留为未解决。分块72→63，原130项标注引文在整库中仍全部存在。这些是结构观测，不是检索召回或答案正确率。

`associations.json` 是由代理逐源文档整理的显式映射，人工pending。它不读取评测答案生成知识。`manifest.json` 保留源摘要、来源、受众、目标小节、未解决问法及被移出检索正文的纯跨文档导航文字。实验chunk版本1/2仅用于本地比较，不是Spring真实documentVersion；不得拿这些逻辑ID生成真实导入映射。11-faq原本已有实质答案，保持字节不变。

`offline-observation.json` 绑定新manifest及旧rubric，逐题记录全库字面可用性，保留旧标注及争议。`reviews-template.json` 提供65处关联及1项未解决项的人工检查入口；复制到新文件填写，不覆盖原模板，不使用旧回答盲评导入器。

在ai-service执行（输出目录必须全新）：

```powershell
uv run --offline python -m ai_service.orchestration.rag_qa_linked_corpus --output ../tmp/rag-qa-linked-reproduction-01
```

真实检索实验另用 `rag_qa_linked_experiment --prepare` 冻结本机计划，再显式 `--live --plan-sha256 <生成的摘要>`。本轮用户授权8次Embedding请求/225输入和180次本地BGE重排，Chat与生产索引写入0；固定锚点 `tmp/rag-qa-linked-20261120/authorization.jsonl` 排他创建，发送前持久预占，失败不返还、不重试、不恢复。不能删锚点、换输出或改脚本绕过耗尽额度。

真实实验使用本地穷举COSINE Top8及原过滤/选择/预算函数，cap1、final3及阈值固定；不是Milvus近似检索、权威发布或线上双服务验收。没有运行Chat，不能评估新答案或拒答质量。后续发布须人工复核并经过原Spring管理发布流程；本版本不自动变更权限、语料权威或发布开关。


## 真实实验已完成

8次Embedding（225输入）与180次本地BGE全部完成，Chat和生产写入0。本地Top8整段标注引文120→124/130，最终证据96→111/130，同时出现4项引文丢失；部分新增引文已有等价支持，不能当回答正确率。候选未推广，额度已耗尽，不能删除账本重跑。完整退步分析、验证及后续方案见 [第23轮交接](../../../../customer-service-qa-linked-round23-handoff.md)。
