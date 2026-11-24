# 单审查输出异常诊断设计

## 授权与目标

执行质量优化交接文档第七节下一轮P0：区分严格Reviewer输出失败的固定原因，并让异常审查阶段留下安全验证历史。用户已要求按照交接继续完成，沿用该范围执行，不扩大为审查协议放宽或自动重试。

## 已知证据

真实请求412bfd81-6ed9-4cf5-9389-75f8c15f36f1的模型日志为INVALID_OUTPUT/615字符/STOP；供应商正文没有保留，无法判断具体JSON或schema错误。PostgreSQL只保留校验及构建两条记录。解析器目前将长度、重复键、JSON、schema、判定组合及反馈长度统一为同一异常；节点异常绕过guarded.complete_node保存步骤。

## 实现与取舍

只增加固定诊断枚举和异常时的最佳努力摘要保存，继续立即失败。相较增加图状态/专用失败节点，该方案不改变验证路由或修复机会，也不用在图中暂存异常；代价是摘要保存失败时只能依靠固定日志，不能保证诊断持久化。保持现有正常节点保存与取消机制，不将失败节点发送为completed。

1. SingleReviewOutputError保留现有公共str，新增白名单output_reason。解析按结束原因、响应大小、重复键/JSON、schema、判定与问题组合、反馈大小依次分类；未知原因映射UNCLASSIFIED。严禁保留原异常、原文、字段名或Pydantic输入到诊断。
2. 固定原因：INCOMPLETE、RESPONSE_TOO_LARGE、DUPLICATE_KEY、INVALID_JSON、INVALID_SCHEMA、VERDICT_ISSUES_MISMATCH、FEEDBACK_TOO_LARGE、UNCLASSIFIED。成功日志为NONE。仅自有验证器的固定错误类型区分判定组合，不分析或导出自由文本异常。
3. verification_history支持可选outputReason，只有quality_review/ERROR及两个现有公共错误码允许该字段，问题数必须0。旧PASS/REPAIR记录形状不变；不合规字段和值在两次投影被拒绝。最多36项、同轮同阶段替换、原TTL和清理保留规则不变。
4. quality_review仅捕获SingleReviewOutputError并生成ERROR记录，清除当前审查诊断/反馈、标记quality_passed=false后保存投影摘要，随后重新抛出原异常。供应商异常、输入预算、取消不伪装为解析失败。非法注入模型返回值同样通过该通道记录UNCLASSIFIED。
5. 保存普通异常仅记录固定失败标记、不含异常正文，继续原解析错误；异步取消和GenerationCancelled必须传播。保存完成后复核协作式取消，不新增超时、恢复或诊断重试。

## 验证与范围

离线夹具覆盖所有分类、严格接受/拒绝集合、调用次数、固定日志、两次白名单、三种生成类型的真实嵌套图、末轮及首轮失败、无修复/发布、唯一终态、取消及保存故障、同轮重放替换、父图/模型/公共事件隔离。显式PostgreSQL测试仅操作唯一测试行并回滚；可通过真实服务单审查请求验证新日志，继续同一60次账本、最多使用剩余20次，不为PASS重跑。该轮不以生成成功率作为诊断验收标准。

不保存历史供应商正文，不能制造真实615字符样本；人工夹具仅证明分类与边界。多Reviewer解析、全工程交互门禁、Vue回滚、自动恢复及重排器测试隔离均不在本轮实现范围。生成40960、普通8192、40次工具、2次修复保持原配置。
