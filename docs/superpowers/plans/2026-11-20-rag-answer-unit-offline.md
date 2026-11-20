# 第26轮答案单元离线验证实施计划

> 按subagent-driven-development执行实现、规格和质量审查。用户已选择上一轮建议，不重复请求同一范围批准；本轮不调用真实模型。

**Goal:** 合并三组同源完整小节，完成保真和90题来源映射敏感性，保留两项人工争议。

**Architecture:** 新独立入口读取冻结问答关联候选及显式合并规则，正式解析/分块后再使用rubric观测。旧排序不复用；来源映射仅为条件性诊断。

**Tech Stack:** Python、正式Markdown解析/分块、客服字符与字节预算、pytest、JSON。

## 任务1：候选和观测入口

文件：新建`ai-service/src/ai_service/orchestration/rag_answer_unit_evaluation.py`、`ai-service/tests/test_rag_answer_unit_evaluation.py`；规则`doc/knowledge/product/experiments/answer-unit-20261120-v1/merge-rules.json`。

- [ ] 先写失败测试：原小节正文/问法逐字保留、按首来源位置合并、未变文件字节一致、重复/不存在标题及重叠组拒绝。
- [ ] 实现纯转换`merge_sections(text, groups)`；使用原H1/H2有限结构，正文原样嵌入普通标签，不生成H3。完整子节不能被1000字符分块拆开，失败关闭。
- [ ] 源manifest/document hash严格核对；全63块唯一映射到新块，版本3仅离线身份；不实例化Settings。构造函数参数不接收rubric或题目。
- [ ] 第24轮comparison绑定已提交结果SHA，核对90题两组身份及旧块完整内容。映射后同新块按首次出现去重、不补位；用正式字符/字节预算生成诊断投影，保留before/after内容和全部差异。禁止使用历史分数预测新排序。
- [ ] 仅在候选/投影完成后观测130公共事实整段引文可用性和预算/文档多样性，聚合必须由逐题生成。liveRetrievalMetrics=null、humanReview=pending、所有供应商/生产写入0。
- [ ] CLI `uv run --offline python -m ai_service.orchestration.rag_answer_unit_evaluation --output ../tmp/rag-answer-unit-20261120/run-final`，仅允许tmp下不存在非链接目录；输入hash前后核对、无覆盖/恢复。
- [ ] 测试加入hash/重复身份/伪上下文/去重/截断/超限/输出链接/旧目录/坏字段，真实数据验收前跑定向。独立规格审查通过后再质量审查。

## 任务2：本轮数据、争议与文档

- [ ] 冻结源材料保护hash及用户README原字节，运行新入口；候选manifest及11文件保存为独立实验版本，报告只保留本轮公共数据。完整90题条件性投影可提交；raw日志不提交。
- [ ] 单列017:02和003:02的严格/宽松四种组合敏感性，引用原答案与原事实，不把代理判断当人工；真人填写使用新文件，旧模板不改。
- [ ] 复核所有实际变化题的增益、丢失、预算和文档多样性，尤其005/003/community。没有新排序/模型答案，禁止写成真实检索或质量提升。
- [ ] 定向测试、全量`uv run --offline pytest -q --junitxml=../tmp/rag-answer-unit-20261120/python-final.xml`、compileall和lock；核对保护hash。
- [ ] 写`doc/customer-service-answer-unit-round26-handoff.md`及结构化结果，追加主/失败观测交接和README。只暂存本轮，diff检查后中文Conventional Commit提交并报告哈希。
