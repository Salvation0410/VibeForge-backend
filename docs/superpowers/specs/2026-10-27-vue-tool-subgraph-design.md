# Vue 工具循环多节点子图设计

本阶段承接子图重构交接文档第六节优先项一，用户已要求继续执行该阶段。

## 结构与取舍

采用共享的编译工具循环图，节点为 initialize、model_turn、validate_turn、execute_tool、continue_loop、finish。生成阶段直接注册该图，验证修复阶段通过修复分派图复用它。相比仅包装现有 while，节点边界能保存逐轮与逐工具进度；相比拆成两套生成/修复实现，共享实现可避免预算与校验漂移。

私有 VueLoopState 扩展公共状态，集中保存 file_plan、written_files、ordinal、retry_count、batch_limit、planning_turns、image_retries、image_used、当前完整响应、已校验待执行工具和游标。仅使用可序列化数据，不保存客户端、回调或 emitter。output_schema 过滤私有状态，父图不复制工具循环历史。

模型响应先保存，再做截断、批次、整批工具参数与预算校验；每次 execute_tool 只执行一个工具并写回进度。整个批次验证通过前不执行任何工具。单次成功后检查点记录工具 ID、结果与 ordinal；继续节点决定执行下一工具或请求模型。首次生成与每次修复初始化独立 ordinal，恢复原子图不重新初始化。跨阶段 tool_call_count 累加。

## 兼容性与恢复边界

保持原 node 事件名、工具 ID、文本拼接、有限重试、计划文件完整性、图片实际写入校验及修复反馈清理语义。生命周期事件与脱敏追踪仍按原阶段发送；内部节点不成为公共事件。取消检查覆盖模型、校验、每个工具与继续判断。

父图 saver 默认传递至嵌套图，不启用独立 thread 或跨调用记忆。增加与工具上限匹配的内部 recursion_limit，避免合法 40 工具请求被 LangGraph 默认 25 步中止。生产不开放恢复接口。工具成功与 checkpoint 提交间崩溃仍可能重试同一 ID，依赖 Spring 的既有幂等和不确定状态处理，不承诺 exactly-once。

## 验证

使用 InMemorySaver 在模型后、工具后暂停，重建父图恢复，检查不重复模型/工具、批次内 ordinal 连续、生成与修复均可恢复及私有字段隔离；覆盖截断/无效批次零工具执行、取消、完整预算与嵌套命名空间清理。执行 API、子图、追踪测试及 Python 全量检查，单独报告 Reranker owner 锁及真实 PostgreSQL 跳过情况。更新 README 与交接文档。
