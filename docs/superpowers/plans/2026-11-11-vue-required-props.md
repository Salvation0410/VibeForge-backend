# Vue 必填属性检查实施计划

- [x] 在 VueProjectBuilderTest 新增“npm 成功但缺失 props 应失败”的旧实现回归，保存失败结果。
- [x] 新增可信 CJS AST 分析器及 Node 测试，限定静态可判断范围和扫描预算；最终 Node 21/21，不写生成源码。
- [x] 在 VueProjectBuilder dist 校验之后运行检查，复用现有取消/超时/输出限制，解析有界 JSON 并返回稳定错误码；环境不可用直接终止，确定缺失进入有限修复。
- [x] Java 53/53（含真实 Node opt-in）及干净编译通过；Python 定向5项、compileall和锁文件检查通过。全量首次既有重排器测试失败，单独复核通过，全量重跑1292 passed/14 skipped，原失败保留。
- [x] 历史快照零模型重检命中原 FilterBar 四项缺失；另一个历史样本无本范围问题。真实 Node 使用手写夹具，不重复生成标记，模型仍124次。
- [x] 同步 AI README、AGENTS、测试集和交接第18节，核对两仓库并纳入中文提交；保留用户修改和原未跟踪计划。
