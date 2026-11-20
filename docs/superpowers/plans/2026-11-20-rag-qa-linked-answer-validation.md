# 第25轮冻结证据真实回答验证实施计划

> 按 subagent-driven-development 执行实现、规格与质量审查；用户已明确“按照建议继续执行下一轮验证”，承接第24轮5题10次Chat方案，无须重复申请同一额度。

**目标：** 检验新语料cap2补回证据是否改善真实回答，保留必要证据缺项与退步观测。

**架构：** 新独立实验入口从第24轮最终comparison及已提交结果绑定读取证据，5题两组共10次单并发真实Chat。沿用当前正式客服系统/用户提示及适配器严格字段与引用解析，HTTP发送前固定新账本预占，不新增Embedding/BGE或生产行为。只验证冻结上下文到回答，不宣称双服务、Milvus或权威快照验收。

**技术：** Python、httpx无重试/不重定向、正式OpenAICompatibleModel回答解析、pytest注入HTTP。

## 冻结决策与成功条件

- 样本固定app-generation-005、app-generation-017、deployment-download-003、deployment-download-013、community-007；每题cap1/cap2各一次，逐题交替两组先后。模型、temperature、max_tokens、超时和输出格式使用当前配置并在prepare脱敏记录；不更换供应商或改提示来追求成功。
- 新账本固定 `tmp/rag-qa-linked-answer-20261120/authorization.jsonl`，发送前flush/fsync排他预占，最多10次。失败/取消/返回不确定不返还，重启拒绝复用或换目录恢复；遇到首次传输/协议/落盘失败停止后续发送，保留未运行项，不为凑满次数追加调用。
- 绑定第24轮已提交结果中的comparison摘要、comparison各输入完整性摘要、当前提示词/解析实现、实验代码、rubric及本计划；prepare和live重新核对一致。上下文和问题与冻结文件一致，无事实标签进入模型。原始响应仅留本轮tmp，不输出凭据。
- 保留每次调用输入摘要、结束原因、供应商报告token、耗时、响应结构/引用是否合法，以及逐事实回答覆盖、证据支持、拒答/不完整原因。合法引用不证明语义正确，代理复核与真人pending分开；11个公共事实两组共22判断，不扩展到全90题或泛化质量。

## 实施步骤

- [ ] 新增 `ai-service/src/ai_service/orchestration/rag_qa_linked_answer_experiment.py` 及 `ai-service/tests/test_rag_qa_linked_answer_experiment.py`。实现prepare/live显式入口；复用正式提示生成/答案解析，通过覆写_invoke注入直接HTTP响应，避免SDK自动重试。HTTP客户端trust_env=False、follow_redirects=False、retries=0，响应大小有界。发送前核对实际messages和计划输入，预占后才发送。
- [ ] 合成测试覆盖10次上限、错误顺序/重复/旧账本拒绝、flush/fsync失败不发送、报告/上下文/配置摘要变化拒绝、HTTP错误/取消不返还、非法JSON/引用/截断失败、默认prepare不联网。严格不修改旧实验及耗尽账本。
- [ ] prepare生成plan和待执行记录，冻结实际模型配置/输入摘要；必要测试和独立审查通过后先提交实现与计划，再执行最多10次真实调用。此用户授权仅适用本轮计划，不授权追加其他模型评审。
- [ ] 检查10个槽位及账本一致性，代理逐项核对回答必要事实与实际引用片段，用精确摘录保留争议，不修改历史rubric。真人模板另存。记录故障、未测项及重复开发集/单次采样限制。
- [ ] 定向及全量pytest、compileall、uv lock --check；新增独立第25轮交接和结构化结果，追加总交接、失败观测交接及README。保护用户既有README围栏修改和未跟踪计划，只暂存任务追加部分，以中文Conventional Commit提交。

## 不适用范围与后续

未知执行状态人工处理、候选无自动GC和幻觉风险均不因本轮消失。本轮无需Docker、Spring或HTTP Python服务，直接实验进程调用已配置Chat端点；不将这解释为线上端到端通过。结果支持后续选择，不自动推广cap2、增加混合检索或调整问法关联。失败应交接真实原因，不通过换账本/重试掩盖。
