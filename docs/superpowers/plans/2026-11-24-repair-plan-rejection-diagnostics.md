# Vue计划拒绝安全诊断实施计划

> **For agentic workers:** 使用 executing-plans 在当前会话逐项执行；用户已确认设计，复用指定codex/python-ai-only分支与当前干净后端工作区。

**Goal:** 定位新真实请求的修复计划拒绝分支，同时维持原门禁、重试与隐私边界。

**Architecture:** 工作集纯转换抛出受控异常，工具循环只投影固定原因和计数；终态异常出口沿现有双白名单保存可选planRejection对象。观察失败不影响原错误及取消。

**Tech Stack:** Python、LangGraph、pytest、PostgreSQL JSONB。

- [x] 在tests/test_vue_plan_rejection_diagnostics.py建立16种PLAN_INVALID与WRITE_REQUIRED_AFTER_READS分类测试、同时违规首因优先测试；断言原work不变、敏感哨兵不进入异常。运行 `.venv/Scripts/python.exe -m pytest tests/test_vue_plan_rejection_diagnostics.py -q`，先确认缺少分类而失败。
- [x] 新增models/vue_plan_diagnostics.py：固定原因、布尔/计数白名单与错误码配对；新增专用异常在vue_repair_workset.py使用，保留公开错误字符串。例如 `raise RepairPlanError("PLAN_REQUIRED")`，不附加原响应。保持原校验顺序及工具执行资格。
- [x] 在vue_tool_loop.py的工作集捕获点生成safe结构、固定日志；私有loop.plan_rejection随新阶段重置并在接受响应时清除。终止前直接给专用异常绑定受控诊断，observed将它传给save_repair_failure，避免失败节点未返回而丢失最后现场。日志失败捕获普通Exception，取消不吞。
- [x] repair_diagnostics.py仅对匹配错误码采用safe_plan_rejection；verification_diagnostics.py允许repair/ERROR的可选字段，保持旧记录及36项限制。新增双白名单、跨轮重试、恢复、成功清除、日志与保存异常、取消和模型/公共事件隔离测试。
- [x] 运行诊断、repair_workset、read_stall、budget_yield、existing_edit_yield、vue_subgraph、verification_history及postgres专项；停止已核对的Python进程后全量pytest，compileall、uv lock --check。记录真实输出，不覆盖.env。
- [x] 以原启动器和同一账本启动验收代理及Python。经应用内浏览器所有者正常入口发起新请求，记录唯一终态；若拒绝，按安全原因与形状构造匿名回放，反馈调整另据证据决定。桌面/390px复验表单入口及提交。完成后恢复普通Python并检查健康，停止验收代理。
- [x] 更新README、AGENTS、交接第十三节与设计状态；git diff --check及git log -1检查后，中文Conventional Commit提交本次代码/测试/文档。独立前端和tmp不入提交。

## 用户已确认的新实测定向纠偏

新请求4e0ba925固定原因三次均为WORKSET_SWITCH_BEFORE_WRITE，账本116→129；用户随后确认实施定向纠偏并继续真实验收。

- [x] 在现有匿名JSON形状回放基础上，新增反馈感知模型：原通用反馈不能促成省略计划，改后沿活动文件写入、逐项声明再强制构建/审查。保留两次耗尽反例和其他原因原反馈断言。
- [x] vue_tool_loop.py只对WORKSET_SWITCH_BEFORE_WRITE采用固定反馈，明确省略repairPlan/completedIssueIndex、沿activePlan.files最小修改，不能无效写入凑资格；tool_contract.py补充活动计划持续有效、普通轮次省略repairPlan。不调整prepare_work_turn门禁。
- [x] 停止已核对且无活动请求的验收Python，运行受影响专项与全量回归、compileall及锁检查，再沿同一账本启动新代码真实对照。报告实际终态及浏览器结果；若仍失败保留具体原因，不扩大预算。
- [x] 同步设计、README、AGENTS和交接，保留首次失败及费用记录，恢复普通Python并停止代理，中文提交代码及文档。

执行结果：定向反馈先红后绿，相关241通过/2跳过，最新全量2894通过/30跳过，真实PG两项通过。新真实请求26f48115为SUCCEEDED，但repairCount=0，未覆盖真实repair成功链；此缺口作为后续验收保留，不能由实施项勾选推断已完成。服务已恢复普通配置，代理停止，前端既有改动保持原状。
