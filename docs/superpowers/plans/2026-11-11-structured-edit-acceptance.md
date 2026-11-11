# 结构化编辑真实验收实施计划

> 使用 subagent-driven-development 实施独立代理任务并审查；主代理负责环境、端到端验收及交接。用户已授权在当前 dev 工作区执行，无需重复确认。

**目标：** 在不超过 60 次供应商请求的条件下，验收三种生成类型的四属性修改并修复有证据的问题。

**架构：** scripts 下独立 FastAPI/httpx 代理，Python AI 服务仅通过运行时模型地址接入；业务 Spring、Redis 和 PostgreSQL 保持原协议，浏览器检查真实预览。

**技术：** PowerShell、Python 3.12/FastAPI/httpx/pytest、Java 21/Spring、Chrome、Vue。

- [x] 新增 `scripts/acceptance_model_proxy.py` 与 `ai-service/tests/test_acceptance_model_proxy.py`：显式执行、回环监听、鉴权、固定上游、持久化预占账本、60 次硬限额、并发保护、失败/取消不退额度、流式关闭。MockTransport 验证不访问真实模型。
- [x] 启动现有 Docker PostgreSQL、隔离 Redis、Spring/Python/前端；仅运行时覆盖模型地址和令牌，保留 `.env`。交付前停止本轮服务与相关容器。
- [x] 创建专用账号和三类应用，HTML/MULTI_FILE 通过真实模型生成初始页面；Vue 为手写 fixture 经 Spring 工具写入和构建，未验收模型从零生成。
- [x] 在硬额度内执行属性验收，最终 60/60；完整矩阵未覆盖，失败及未测项记录到交接。修复 PostgreSQL 准备诊断状态白名单，增加审查固定字段日志和 structured-edit-scope v1。新规则真实复测遇审查拒绝后额度拦截，未证明范围问题解决。
- [x] 验证失败旧产物保留、HTML HTTP 取消及部分真实 AppChat 刷新边界；补足 SSE/浏览器脚本和 Vue 固定夹具。真实 UI 停止按钮未覆盖，原始截图和报告不提交。
- [x] 必要验证：Python compileall/lock 通过，全量 1194 passed、10 skipped；PostgreSQL 专项 19 passed（含 2 项真实资源测试）；Node 工具测试 5 passed。无 Java/前端生产修改，未重跑 Maven 或前端构建。两阶段审查通过，交接与 AGENTS 同步，提交仅含本轮改动。
