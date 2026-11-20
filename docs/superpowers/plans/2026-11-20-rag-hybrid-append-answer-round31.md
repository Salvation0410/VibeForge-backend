# 第31轮执行计划

> 使用subagent-driven-development执行实现、规格、质量复核；主流程冻结、真实运行、独立核验和交付。用户已批准第30轮给出的四题8次方案，不重复询问。

**目标：** 判断保留cap1追加一段后是否改善真实回答，保持检索和提示不变。
**架构：** 独立第31轮模块沿用第29轮传输/账本语义，以第30轮可逆归档作为唯一候选来源；旧模块和生产代码不改。
**技术：** Python、httpx、pytest、现有正式OpenAI兼容适配器。

- [ ] 新建ai-service/src/ai_service/orchestration/rag_hybrid_append_answer_experiment.py及tests/test_rag_hybrid_append_answer_experiment.py。将第29轮模块和测试作为明确模板，替换轮号、目录、schema、arm cap2→append1、授权字符串、固定源归档与comparison摘要、原绑定数55→168；bindings必须包含本轮真实spec/plan/test路径。prepare检查两组产物残片。任何旧文件均不改，不重算检索。
- [ ] 合成测试先红后绿：实际绑定路径存在、168输入及归档漂移、四块原样传入提示且三引用上限仍生效、8次顺序及首错停止、未知预占不发送、旧账本拒绝、prepare无网络。保留模板已有超时/取消/截断/非法引用/原响应/usage观测测试。
- [ ] 主流程保存保护摘要和README原始字节，冻结评分依据与本轮16项入选证据预评。复用第29轮等价表述，不以查看本轮答案改变门槛。新测试定向命令uv run --offline pytest tests/test_rag_hybrid_append_answer_experiment.py；规格、质量复核通过后提交实现。
- [ ] uv run --offline python -m ai_service.orchestration.rag_hybrid_append_answer_experiment --prepare；核验8槽消息、四块及配置，保存可提交的freeze计划。用生成的planSha256执行--live --plan-sha256 <实际摘要> --authorization "继续执行下一轮"，唯一运行，首错停止无自动补跑。8次均计额度，Embedding/BGE/生产写入0。
- [ ] 原响应离线注入正式解析并比对8结果；检查账本预占/发送/usage/哈希及无变动，独立代理评审16事实的answerCoverage/allContextSupport/actualCitationSupport和额外断言。摘录必须精确匹配，不把未运行当失败分数，真人pending。
- [ ] 完整pytest及compileall、uv lock --check --offline；可逆归档必要证据/答案/审计/测试/代理评审和空白人工模板，新增doc/customer-service-hybrid-append-answer-round31-handoff.md，追加两主交接与ai-service/README.md。git diff --check，独立提交本轮材料，报告实际结果和下一轮建议。

计划进入调用绑定后不回写状态。现有工作目录含必须冻结的历史tmp，继续使用dev，保护用户修改，不创建缺少历史数据的工作树。原始HTTP/凭据/日志不提交。
