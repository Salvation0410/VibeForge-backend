# 客服RAG评估交接（2026-11-16）

## 本轮范围

阅读并适配[Datawhale系统评估章节](https://datawhalechina.github.io/all-in-rag/#/chapter6/18_system_evaluation)，在11月13日方案基础上落地独立评估工具；不修改生产客服检索、模型提示、知识版本任务、数据库或Milvus alias。

具体方案和取舍见[评估与优化设计](../docs/superpowers/specs/2026-11-16-rag-evaluation-optimization-design.md)。核心建议为先修版本契约，再按阶段证据做有界证据组合与定向召回；两块候选暂不推广。

## 新增内容与使用

- `ai-service/evaluation/customer-service-boundaries-v2.json`：48题、24组、40可答/8拒答、63条精确原文事实；前32题重标注，新增16题，待人工复核。
- `rag_evaluation_workflow.py`：来源校验、块级IR、事实span存活、分组/配对、标准单轮字段导出。
- `rag_snapshot_experiment.py`：默认离线，显式真实Embedding/GPU重排/回答对照；调用预占上限、无自动重试、失败/缺失单列。
- `rag_evaluation_judge.py`：可选匿名语义评审，核对claims/证据原文/事实ID；仍存在模型偏差与语义误判。
- 两套新增测试：评估分母、来源/预算/裁判边界，以及已知版本组合冲突的可重复诊断。

在`ai-service`执行：

```powershell
uv run python -m ai_service.orchestration.rag_snapshot_experiment
uv run python -m ai_service.orchestration.rag_snapshot_experiment --live --judge --judge-case-limit 12 --max-chat-calls 120 --output ../tmp/rag-study-new-run
uv run python -m ai_service.orchestration.rag_snapshot_experiment --analyze ../tmp/rag-study-new-run/report.json
```

第二条有真实费用且要求输出目录尚不存在。完整48题需要96次回答，12题两组评审24次，合计120次聊天预占，另计Embedding。不能覆盖账本、自动重试或换目录绕过本次授权总量。模型和Embedding密钥读取私有配置，不输出到日志。纯校验不初始化供应商。

`--analyze`重新计算分组和逐题差异，并导出`user_input/retrieved_contexts/response/reference/metadata`。其中contexts是实际最终模型输入，错误保留null；不代表已经执行Ragas框架。拒答题需要适用性判断，不能因空答案得到虚假的忠实度满分。

## 真实实验记录

本轮数据指纹：`f3e59e9a68ee15938397c5a822c42fdcd94c79962fd9c8fe0f270b3fc31285e2`；公共语料指纹：`d52ab6a099688fc3a718c52d41e1597dc5df34361b4bd8a10bbc200e42315b8f`；归一化向量快照指纹：`2ce9389865ed887a95c250746d76292b3941b1c62c6733eeda2b2a1d7d36f077`。

完整私有产物在`tmp/rag-evaluation-20261116/live-snapshot/`：`report.json`、`retrieval.jsonl`、`calls.jsonl`、`vectors.npz`、`analysis.json`、`samples.jsonl`。不提交原始产物；本文仅保留脱敏聚合与问题ID。本轮版本未在report记录开始时间与实现哈希字段，后续运行已补充；不能追称旧产物含有这些字段。

| 项目 | 基线每文档1块 | 候选每文档最多2块 |
|---|---:|---:|
| 回答可答题 | 40/40 | 40/40 |
| 拒答契约 | 7/8 | 7/8 |
| 最终原文证据宏覆盖 | 90.208% | 93.125% |
| 完整证据题 | 33/40 | 35/40 |
| 回答错误/缺失 | 0/0 | 0/0 |
| 语义评审有效 | 12 | 11，另1次无效 |
| 回答均值/P95 | 1025/1396ms | 1002/1370ms |

真实调用：Embedding4批、120输入、8916 token；聊天120次，其中回答96、评审24。聊天usage为86991输入+9975输出=96966 token，不含Embedding。语义有效样本的支持claim为24/24与17/17，但等距抽样未覆盖关键失败，且两组分母不同，不能称全量满分或人工正确率。

共享Top8必要证据宏覆盖99.375%（39/40完整），重排Top3降到93.125%；现状文档去重进一步降至90.208%；本轮字符/字节裁剪没有新增事实损失。3题改善002/006/007，1题标注覆盖降低032，回答数量无变化。

## 已确认失败与下一步

- 026：越权部署执行请求被输出为answered=true的权限解释。并未执行部署或泄密，但不满足当前内部false/空/无引用契约，两组均不能通过安全门槛。
- 021：所需证据在dense第6位，仍没进入最终证据；应查重排与覆盖选择。
- 029：三种类型所需证据均已召回，但重排及单文档配额造成缺失；最多2块也不能普遍解决同文档3事实。
- 030：Vue证据dense第11位，Top8确有召回不足；有依据比较Top16/24、标题输入。
- 032：丢失9张上限标注但仍正确回答不自动同步，先核对标注必要性，不自动判回答退步；004也需补等价来源，007需核对额外审核提示是否必答。

标注冻结，不根据本轮结果改gold抬分。人工裁决后另建v2.1，两组同等重算。下一轮裁判必须加入这些Bad Case和安全对照，不能只扩大简单题抽样。

版本诊断在Fake中复现相同版本换ETL冲突、禁用后启用受墓碑阻断、重建ETL与后续索引冲突、空重建切空集合。测试通过表示缺陷可复现，不表示已修好；目标生命周期修复仍按11月13日方案单独实施。

## 验证与边界

本轮执行Python compileall、pytest与`uv lock --check`；全量最终结果见本节末尾的交付校验记录。模型评估为真实Embedding、真实GPU BGE与真实回答/裁判；数据库、Milvus、OSS及生产HTTP未参与该隔离实验。初查Docker daemon和8000服务不可用，因此不得沿用11月12日环境结果称为本轮通过。

原90题保持不变；没有生产推广、真实管理员生命周期、多实例过期租约、在途禁用、ANN过滤或完整复杂PDF验收。正确性与质量方案需用户审阅取舍后再改生产行为。

交付校验：最终`compileall -q src`与`uv lock --check`通过；全量pytest **1452 passed、14 skipped、2 warnings**（241.43秒），14项真实PostgreSQL集成测试未opt-in，警告为Starlette及LangGraph弃用提示。新增专项共26项包含在全量内，不重复累加。另对账本124条预占、96组结果、23份有效裁判、96条标准导出做离线一致性校验，无新增模型调用；`git diff --check`通过。
