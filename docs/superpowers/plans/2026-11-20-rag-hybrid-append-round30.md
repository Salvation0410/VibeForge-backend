# 第30轮执行计划

使用subagent-driven-development完成实现、规格和质量复核，主流程负责冻结、正式离线运行、独立证据核验与提交。用户已批准第29轮建议，无需追加真实调用授权，本轮真实调用为0。

1. 新建ai-service/src/ai_service/orchestration/rag_hybrid_append_evaluation.py及tests/test_rag_hybrid_append_evaluation.py。固定28归档/现场SHA及55绑定，重建63块和90题cap1，全部选择完成后才读取130事实。不得修改历史源码/语料/rubric或生产函数。
2. 基线正式cap1最多三块；新组依原BGE顺序仅补第一个未选且符合每文档2块的块，追加末尾。原组不足三块也只补一块，无候选则保持。正式字符和JSON字节预算不变，不改引用上限。分别记录选中前缀及预算后原证据是否完整保持，报告增加/丢失、噪声、多样性和成本。

   正式_bounded_contexts先截到3块，新组使用实验内最多4块适配器，复用正式_clean_text/1200/3000常量；<=3块与正式函数逐值一致测试，4块存活/预算耗尽测试，禁止修改生产全局上限。
3. 合成测试覆盖收益与保留、只补一个、不足三块、无候选、配额、去重、字符/字节截断导致保留失败、引文空白清洗、标签隔离、固定输入/归档/绑定路径漂移、CLI新输出目录及禁网。先红后绿；完成规格与独立质量复核后冻结源码。
4. 唯一正式输出tmp/rag-hybrid-append-20261120/run-final（不得覆盖）。命令：在ai-service执行 uv run --offline python -m ai_service.orchestration.rag_hybrid_append_evaluation --output ../tmp/rag-hybrid-append-20261120/run-final。全90题、130事实阶段矩阵，纯手工参考选择独立校对；代理复核全部变化题与005/008/admin004/download009。核验摘录实际入选及最终存活，不按新增引文直接声称语义收益。
5. 定向与完整pytest、compileall、uv lock --check --offline。归档完整可逆阶段结果、输入和测试摘要、代理复核与真人模板；新增doc/customer-service-hybrid-append-round30-handoff.md，追加两主交接和README。git diff --check、中文提交、原用户修改保留。真实答案质量、拒答质量均未测试，候选不推广。

计划属于运行绑定，正式执行后不回写状态。完成和未测项在新交接记录。新候选最多四块但答案仍最多三个引用，更多证据可能仍未被采用；下一步需结合本轮证据再由用户选择。
