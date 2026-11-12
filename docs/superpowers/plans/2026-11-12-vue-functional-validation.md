# Vue 本地功能与有限修复续验

本轮按用户授权继续执行已有 Vue 门禁和读书清单探针方案，不增加生产功能或改动预算。关联设计为 `../specs/2026-11-11-vue-required-props-design.md`、`../specs/2026-11-11-reading-list-probe-design.md`，承接 `2026-11-11-vue-contract-real-validation.md`。

## 选择依据

现有 Java 测试覆盖 npm 成功后的缺失属性拒绝，Python 测试覆盖错误结果传入修复、重新构建成功后读取新快照审查、两次修复耗尽，以及检查器环境不可用直接终止。源码确认每轮强制构建执行检查，修复次数进入不同工具调用 ID。暂未发现需要重构的控制流缺口。

因此优先执行一个全新隔离应用的自然生成及实际交互。沿用固定“慢读书房”需求和现有探针，最多一个从零生成样本，不为门禁拒绝或无进展重复抽样。若功能失败，保留原始证据后再定位；若未自然触发修复，明确记录未触发。

## 执行清单

- [x] 阅读 AGENTS、交接第 18–21 节、真实测试集、AI README 及关联设计/计划；核对独立两仓库状态。
- [x] 复核原修复控制流，并重新运行 Python 定向 5 项、Node AST 21 项、Java 构建/检查器 28 项和浏览器探针测试（1 项、内部四种手写夹具）。均通过；Fake/受控测试不当作自然修复证据。
- [x] 核对端口、容器、配置和登录；只启动必要本地服务。沿用 `tmp/real-e2e-unlimited-20261111/model-ledger.json`，初始 136、max_calls=null。
- [x] 新建一次隔离应用并提交一次生成，使用新独占标记；记录真实模型、工具、构建、门禁、审查和显式任务终态。
- [x] 原请求在工具预算耗尽处失败，未到浏览器；同一隔离应用后续新用户修复成功，预览/本机部署共享探针 44 项通过，原运行器退出 1，零模型补验退出 0。
- [x] 保留失败报告，记录模型实际增量、失败/重试及未覆盖项，核对服务收尾和用户修改；仅提交本轮文档或必要修复，不推送。

私有证据放 `tmp/vue-functional-20261112/`，不输出或提交凭据、日志、源码、下载及截图。已有前端客服三文件和后端 cancellation-drain-regression 未跟踪计划保留。跨标签并发、长期压力、跨实例、生产恢复、公网部署和 Vue 回滚不在本轮范围。

## 自然失败后的最小修复计划

对应设计：`../specs/2026-11-12-vue-repair-phase-design.md`。按用户本轮“发现问题后定位并最小修复”的授权实施，保留失败记录。

- [x] 在 `ai-service/tests/test_vue_repair_phase.py` 用已有 FakeModel/FakeToolGateway 建立四种修复子图场景：currentArtifact.exists 为 false/true，repair_count 为 0/1。首轮发出无效模型响应，随后一次写入和正常结束；断言进入时无三种旧反馈，后续仍有本阶段格式反馈、phase 为 modify_files、工具历史和预算连续。
- [x] 执行 `uv run pytest tests/test_vue_repair_phase.py -q`，保存旧代码失败证据。
- [x] 在 `orchestration/vue_tool_loop.py` 原阶段条件中增加 `node == "repair"`；在 `subgraphs/vue.py` 修复初始化中逐项 `context.pop(key, None)` 清除 planningFeedback/batchRetryFeedback/vueFormatFeedback，增加中文意图注释。
- [x] 执行新增测试、`test_vue_subgraph.py`、组件检查及原两组 API 构建修复回归；再执行 compileall、pytest 全量、uv lock --check。
- [x] 同步 AI README、交接第 22 节和真实测试集，区分本次自然失败、受控回归及后续新请求，不把工具内往返写入称作验证子图的 REPAIR_NO_PROGRESS。
- [x] 检查 diff、原修改哈希与模型账本，中文 Conventional Commits 提交，不推送。

## 实际结果

原请求自然命中 FilterBar 四项必填属性缺失，但第一次修复未交回构建即耗尽工具预算。生产修正仅涉及两处编排文件；四项新回归旧代码失败，修复后定向 20 passed，全量 1298 passed/14 skipped，compileall/lock 通过。

后续新用户修复请求在同一隔离应用自然经历 SCOPE_VIOLATION→修复→PASS，两个构建通过，浏览器共享 44 项/16 计数通过。它不证明原 AST 拒绝请求的自动修复成功。模型累计 136→197，新增 61；失败与收尾异常记录保留，详见交接第 22 节。
