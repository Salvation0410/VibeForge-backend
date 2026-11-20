# 第28轮混合检索配额回放计划

> 使用subagent-driven-development执行实现及独立规格/质量复核，主流程负责输入冻结、实际回放、代理证据核验和交付。用户已批准本轮离线方案。

**Goal:** 判断混合Top8下cap2能否保留新召回证据，并识别挤出损失。

**Architecture:** 新独立rag_hybrid_cap_evaluation.py复用rag_qa_linked_cap_evaluation的重建、选择与预算；只读第27轮结果，不调用其live入口。CLI使用block_network、validate_output和固定输入绑定。

**Tech Stack:** Python标准库及现有正式选择函数，无新依赖。

## 1. 实现与定向验证

- [ ] 新建ai-service/src/ai_service/orchestration/rag_hybrid_cap_evaluation.py和tests/test_rag_hybrid_cap_evaluation.py。
- [ ] 固定第27轮归档SHA，验证计划/报告/账本/原绑定；校验63块来源、180组报告/调度、唯一90题和唯一130公共事实；归档可逆展开与原report对应。
- [ ] reconstruct(row,candidates)恢复检索/排序；select_arm(question,retrieved,ranked,1)逐值重现所有原字段；同输入select_arm(...,2)。选择之后才读取requiredFacts。
- [ ] 完整记录阶段引文、quoteFailure、changedContexts、diversityDelta与budgetDelta，summary分别统计gains/losses；人工模板独立pending。
- [ ] 合成正反例先红后绿，含配额带来恢复与全局Top3挤出，不测试实现镜像。

## 2. 冻结与离线运行

- [ ] 规格和代码审查通过，冻结源码及输入摘要；执行一次CLI到tmp/rag-hybrid-cap-20261120/run-final。不得开启真实模型或服务。
- [ ] 核对90题cap1重现、180组选择以及130事实阶段矩阵；代理逐项复核全部变化题与增减事实，主流程校验摘录来源。

## 3. 验证与交付

- [ ] 全量pytest、compileall、uv lock --check --offline；不复用旧测试成绩。
- [ ] 结构化结果与第28轮新交接、两主交接、README任务附录。解释选择触发、退步、代价、人工/语义边界与下一轮建议。
- [ ] 核对输入hash、归档可逆性、用户原修改保留；git diff --check及中文提交，报告哈希。
