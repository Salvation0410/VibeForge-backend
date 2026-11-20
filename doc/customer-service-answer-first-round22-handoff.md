# 第22轮：有限问法片段降序离线对照

日期：2026-11-20。用户选择方案 A 后完成。结论：A2 相比 cap2 改变 7 题上下文，但没有新增事实覆盖；目前不建议推广生产或仅为此版本追加真实回答调用。人工复核仍为 pending。

## 问题、范围与实现

第21轮发现最终证据存在 14 项全部或部分选择缺项。第13、16轮已有 cap1/cap2 对照，本轮以它们为基线，检验只列问题的片段是否挤占答案。A0=cap1，A1=cap2，A2=cap2 加有限纯问法片段降序。冻结历史 Top8、BGE 分数与稳定顺序、阈值、最终3块及字符/字节预算；不补相邻块、不重做检索、不生成新回答。

新增 `ai-service/src/ai_service/orchestration/rag_answer_first_evaluation.py` 和对应44项测试。选择过程只读片段，事实标注在选择后用于观测，不按题目或标准答案定制规则。线上路径不导入该模块，生产 text/cap1 保持原样。

规则 `answer-first-question-list@2` 要求来源最后一段为“常见问法”，正文是2—20个连续中文引号问句，每句2—100字并包含冻结疑问标记；可有有限的中文章节指引。完整语法见冻结计划与实现。稳定分区仅改变候选顺序，不删除候选或修改分数。它只识别有限语法，不是通用语义分类；本轮命中4个不同片段，不能根据结果追改为覆盖全部10个问法章节。

读取新对照结果前，合成“必须登录”“必须填写标题”反例暴露 v1 将要求误判问句，删除独立“必须”标记并冻结 v2；随后明确空白语法。三次预运行冻结提交为 `2bfff4f`、`4457988`、`34c2a90`。实施中补齐跨阶段分数一致性拒绝测试。首次回放后只修复顺序变化统计漏计并加测试，最终重放的90题候选、三组选择与事实存活结果逐项相同，未调整策略。

## 结果与解释

| 指标 | A0 cap1 | A1 cap2 | A2 cap2＋降序 |
|---|---:|---:|---:|
| 整段标注引文字面存活 /130 |96|112|112|
| 代理判定 covered |102|113|113|
| 代理判定 partial |13|7|7|
| 代理判定 missing |12|7|7|
| 代理判定 disputed |3|3|3|
| 90题各题不同文档数之和 |264|205|206|

三组均无字符/字节裁剪。A0→A1 改变65题，A1→A2 改变7题，A0→A2 改变64题；顺序变化也计入。整段引文增加16条均来自 cap2，A2 无新增或丢失的整段引文。字面存活与代理语义支持都不是回答正确率。

A1→A2 变化题：`app-generation-002/011/012/014`、`community-014`、`admin-knowledge-001/005`。这些替换加入说明片段，但说明未必相关。例如 generation-014 的生成停止问题加入社区发布失败规则，有干扰风险；admin-knowledge-005 的异步删除证据在 A1 已存在，新加入维护说明未补足缺项。

第21轮14项目标中，A2 对比 A0：7项恢复完整支持，1项从缺失改善为部分支持，6项仍不完整。全部改善已发生在 A1。完整恢复为 platform-account-001 的01/02、app-generation-005 的02、community-007 的01、community-013 的01/02、admin-knowledge-005 的02；后者01仅部分改善。仍缺 app-generation-005 的01/03、community-004 的02、admin-knowledge-002 的01/02/03。文件格式、20MiB 与空文件三项在三组都缺失。

## 评审分歧与退步，必须保留

三个代理各复核30题，共90题130事实、390组判定；384处支持摘录均逐字存在对应上下文。9题无公共事实标注，不能据此判断新拒答质量。A1/A2 的130项判定完全相同。人工模板未填写，不能称人工验收。

原三项争议继续保持：`app-generation-006:required:01`、`app-generation-007:required:01`、`deployment-download-009:required:01`。

本轮较严格的代理把以下4项 A0 判为 partial，而第21轮判为等价支持：preview-history-006:01（不刷新与不清空）、deployment-download-013:01（可用产物与当前任务成功）、community-006:01（审批后公开与初始待审）、community-013:03（脱敏摘要与仅必要信息）。这些是评审尺度差异，旧结果不改，人工待裁决，不作为稳健新增收益。排除3项争议及4项分歧后剩123项：A0 为102完整/9部分/12缺失，A1/A2 为109完整/7部分/7缺失，仍非人工金标。

此外 `deployment-download-003:required:02` 从 A0 到 A1 丢失“创建记录成功不等于生成完成”的明确子项证据，尽管复合事实两组都为 partial。不能用总等级未下降宣称没有语义退步；A2 未修复此处。

## 重放与人工入口

在 `ai-service` 执行，输出目录必须不存在且位于仓库 tmp；再次运行请更换后缀：

```powershell
uv run --offline python -m ai_service.orchestration.rag_answer_first_evaluation `
  --report ../tmp/rag-json-answer-20261119/prepared/live/report.json `
  --report-sha256 7fa940447c2553431974c342519ec233395932ca72d7b43b18a005149b325579 `
  --document-map ../tmp/rag-verification-20261118/live/document-map.json `
  --output ../tmp/rag-answer-first-20261120/reproduction-01
```

最终现场包为 `tmp/rag-answer-first-20261120/run-final/`：comparison 包含90题三组证据、变化和预算；summary 为汇总；input-integrity 绑定27份输入/实现/计划；reviews-template 是独立证据评审 schema，**不能直接交给旧 rag_blind_review 导入器**。复制模板到新文件填写 reviewer、逐事实支持与退步理由，保留原模板及 pending 状态，不覆盖代理或历史评审。

可提交的摘要为 [结构化结果](knowledge/product/evaluation/results/answer-first-selection-20261120-v1.json)，包含全部90题事实判定与绑定。完整原文、代理理由及现场日志在本地 tmp，未提交；换机器复现必须先具备对应历史报告与映射。三个代理评审绑定首次 run-v2，最终 run-final 经逐项一致性检查；结果文件记录两组哈希与复核文件哈希。

## 故障与保证边界

输入哈希、大小、重复JSON键、非有限分数、阶段身份/分数/排序或跨组轨迹不一致均拒绝；A0 必须90题逐字重现才能报告收益。输出拒绝覆盖，写出多文件不是事务，中断后应保留残缺目录并用新目录重跑，不把残缺包视为成功。CLI 拦截常见 Python socket 连接/解析/发送入口用于本地观测，不是操作系统网络沙箱。没有自动重试、恢复、生产发布或候选GC。

本轮没有 Chat、Embedding、BGE、INDEX 调用，CLI 网络尝试0；未启停 Docker/Spring/Python，未改配置、知识库或生产行为。没有测试真实新回答、双服务、Java/前端构建或浏览器。旧耗尽账本、语料、rubric、答案和评审不改；未知执行状态仍需人工处理，引用校验不能消除幻觉，最终快照复核后的撤销竞态仍在。

## 验证与交付

定向44项通过；最终完整套件 **2204 passed、25 skipped、2 warnings**，退出码0，耗时264.15秒。两项警告来自 Starlette BlockingPortal 与 LangGraph allowed_objects 弃用提示。compileall 与 `uv lock --check --offline` 通过。90题 A0 精确复现，并与旧第13轮 cap1/cap2 结果各90题完全一致。语义核验90题/130事实/384摘录通过；文档追加前30份保护文件不变，追加后仅 README 增加本轮说明且保留原始字节前缀，其他29份不变。最终实现/计划哈希与 run-final 绑定一致。

```powershell
uv run --offline pytest tests/test_rag_answer_first_evaluation.py -q --junitxml=../tmp/rag-answer-first-20261120/target-final.xml
uv run --offline pytest --junitxml=../tmp/rag-answer-first-20261120/python-final.xml
uv run --offline python -m compileall -q src
uv lock --check --offline
```

测试日志、XML与选择重放等价性记录保存在本地 `tmp/rag-answer-first-20261120/`。这些是本轮实测结果，不沿用历史测试成绩。

本轮代码、测试和文档一并提交，提交哈希见最终回复；原 README 围栏改动与用户未跟踪取消计划不纳入提交。未部署。

## 下一轮由用户决定

建议先裁决上述3项争议、4项尺度分歧和子项退步。若继续小成本实验，可单独比较 **cap1＋同一冻结降序规则**，隔离降序在原配额下的影响；本轮未运行该第四组，不能预测收益。另一选择是 **B：知识分块与问答关联**，需新语料版本、标注及可能的Embedding/INDEX预算。混合检索单独针对真正 Top8 未召回项，不能解决所有配额与生成遗漏。任何后续真实调用都需新计划和额度，不自动推广 A2 或复用旧账本。
