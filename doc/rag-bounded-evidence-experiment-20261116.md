# 路线 B1/B2 有界证据实验结果

日期：2026-11-16。此轮仅修改独立评测设施，未更改生产检索、索引、回答提示或冻结 v2 标注。使用现有公共正文及向量快照，新增聊天和 Embedding 调用均为零。

## 冻结条件与执行方式

使用 `customer-service-boundaries-v2.json` 的 48 道诊断题（40 可答、8 拒答、63 条必要事实），不是独立盲测。11 文档、72 块正文、问题向量及原始 Top8/BGE 顺序来自 `tmp/rag-evaluation-20261116/live-snapshot/`。精确 cosine 不包含 Milvus ANN、版本过滤、RPC 或业务生命周期。

工具 `ai_service.orchestration.rag_bounded_evidence_experiment` 校验数据集哈希、向量哈希/维度/单位范数、预定问题身份、dense Top8 完整记录和重排来源身份。重排结果必须完整、有穷、按分数递减，不能篡改原正文、来源或块身份。选择器只接收问题和排序结果，不接收 gold；仅在最终选择和实际预算裁剪完成后使用 gold 计算指标。

B1 冻结旧 Top8 和真实 BGE 顺序，只改变每文档上限 1/2/3。B2 冻结原文向量、每文档上限 1、最终最多 3 块、每块 1200/总 3000 字符及实际 JSON 提示 16384 字节预算，只改变 dense Top8/16/24。B2 三组均用本轮同一 BGE 实例重新评分，避免将旧缓存顺序与新运行混比。B1/B2 是两组独立单变量实验，不能把 Top16 与 cap3 同时变化的结果作为本轮单变量结论。

从 `ai-service` 执行：

```powershell
uv run python -m ai_service.orchestration.rag_bounded_evidence_experiment --source ../tmp/rag-evaluation-20261116/live-snapshot --output ../tmp/rag-evaluation-20261116/bounded-evidence-local-bge-v2 --local-rerank --prepare-title-inputs
```

输出目录必须是 `tmp/` 下不存在的新目录，避免覆盖历史证据。默认不带 `--local-rerank` 时只复用旧 Top8 顺序并计算扩大候选的 dense 指标；Top16/24 最终上下文明确 `final-not-tested-no-rerank`、最终覆盖为 null，不能借用 Top8 顺序冒充扩大候选后的重排。显式本地评分通过生产适配器的 GPU 独占锁，锁失败不会抢占、改成无重排或生成虚假最终结果。

最终运行使用 `BAAI/bge-reranker-v2-m3`、CUDA，共 144 次局部重排（48×3）。之前一次探索运行是 Top16/24 共 96 次局部重排，另有零评分离线校验，合计 240 次本地 BGE 评分请求；无付费聊天或 Embedding 请求。没有启动生产服务或写入 Milvus。

## 结果与定位证据

以下指标仅为冻结标注的精确原文 span 覆盖，不是语义充分性、答案质量或安全通过率。全部 40 可答题均纳入分母。

| 实验组 | dense 必要证据宏覆盖 | dense 完整题 | 最终必要证据宏覆盖 | 最终完整题 |
|---|---:|---:|---:|---:|
| B1 同文档上限 1 | 99.375% | 39/40 | 90.2083% | 33/40 |
| B1 同文档上限 2 | 99.375% | 39/40 | 93.1250% | 35/40 |
| B1 同文档上限 3 | 99.375% | 39/40 | 93.1250% | 35/40 |
| B2 dense Top8、同文档 1 | 99.375% | 39/40 | 90.2083% | 33/40 |
| B2 dense Top16、同文档 1 | 100% | 40/40 | 89.5833% | 33/40 |
| B2 dense Top24、同文档 1 | 100% | 40/40 | 89.5833% | 33/40 |

可定位记录均在最终 `report.json` 的 `cases` 数组，以 `id` 与 `arm` 联合定位；各题包含真实 retrieved/reranked、selected/final 原块身份与正文及各阶段指标。汇总在 `summary.<arm>.stages`。

- `rag-v2-002/006/007`：cap1→2 最终覆盖分别 0.5→1、1/3→1、0.5→1；`rag-v2-032` 是 1→0.5。后者保留既有标注疑义，不因观察结果修改 v2。
- cap2→3：全部题必要证据覆盖均无变化，8 题最终上下文身份发生变化，不能据相同指标称答案语义完全相同。
- `rag-v2-021`：Top8/16/24 召回均覆盖 1，最终均 0.5；未解决 UPLOADED/ACTIVE 的证据组合缺口。
- `rag-v2-029`：Top8/16/24 召回均 1，最终均 1/3；cap3 也仍为 1/3。简单放宽上限并不会主动寻找第三种类型的互补证据。
- `rag-v2-030`：Top8 dense 覆盖 0.75，Top16/24 dense 为 1；重排前三仍都为 0.25，最终从 Top8 的 0.25 降为 Top16/24 的 0。扩大后 `04-generation-types:1:4` 排到同文档第一个保留位置，排除了原来有已标注必要事实的 `04-generation-types:1:1`。最终三个块为 `04-generation-types:1:4`、`07-deployment-download:1:2`、`02-app-generation:1:6`。这不是“没召回”的单一问题。

快照绑定与本地原始证据：

| 项目 | SHA-256 |
|---|---|
| 冻结 v2 数据集 canonical 指纹 | `f3e59e9a68ee15938397c5a822c42fdcd94c79962fd9c8fe0f270b3fc31285e2` |
| 原单位向量数值快照 | `2ce9389865ed887a95c250746d76292b3941b1c62c6733eeda2b2a1d7d36f077` |
| 原 retrieval.jsonl 文件 | `4c44987629712aa0e793dde7b9609309bcbbcc0ecd072439795fb98968224316` |
| 最终本轮 report.json 文件 | `cbeedc890951249928375eaa9ca0a1a9c9bd03cef2f5ec372f78f08faea9b84d` |
| 执行时实验模块文件 | `8d64633a69f9b94d0c3da51e985bfdc63515fe90e18a9655022e51a130bb26c0` |

原始文件在忽略目录保留，不提交源码仓库；上表与逐题身份用于核对本机证据，不承诺另一台机器可取得未提交向量。模型别名与同实例评分也不能保证供应商或本地权重以后不变。

## 标题实验与未覆盖项

`--prepare-title-inputs` 显式导出 `title-inputs.json`，格式 `rag-retrieval-inputs/v1`，输入版本 `title-path-body/v1`，记录块/问题顺序和实际输入哈希。标题路径使用当前解析器的 `locator`，模型输入是“标题路径 + 正文”，引用正文和 chunkId 均保持原身份。正文基线是 `body/v1`；两个输入版本哈希必须不同。

这仅是标题实验的输入准备能力，本轮未生成新标题 Embedding、未导入或评分标题向量、未检验标题召回收益。报告始终标记 `not-run-requires-new-embeddings`；不把原 body 向量当成标题实验结果。需要新 Embedding 调用授权和有界调用账本后，才能执行完整标题变量对照。

本轮没有生成新回答、做语义裁判、人工裁决 v2、运行父章节/邻块扩展、生产 ANN、版本迁移、生命周期或独立盲测。拒答 8 题只重算上下文，不评价拒答是否修复。B1 cap2 的历史改善仍不能越过这些缺口直接上线；B2 扩大召回对当前选择器没有最终覆盖收益，不能据召回 100% 推荐直接提高生产 TopK。

下一步优先以题 021/029/030 为诊断，预先规定只使用问题与候选元数据/正文的有界互补选择或父章节规则，再按同样 final3 和字节预算比较；同时用独立人工问题验证过拟合风险。标题变量单独执行，不能和选择器变化叠加后归因给标题。

## 本轮验证

- `uv run pytest tests/test_rag_bounded_evidence_experiment.py tests/test_rag_evaluation_workflow.py -q`：48 项通过。覆盖配额、块身份、标题输入版本、损坏/错配向量、伪重排身份/正文、重复/缺失来源、GPU 不可用、扩大候选未评分不得标已测、同实例三组评分与固定 final3。
- `uv run python -m compileall -q src`：通过。
- `uv lock --check`：通过。
- 本地 BGE 真实评分运行成功，不代表生产 API、Milvus 或答案质量验收通过。
