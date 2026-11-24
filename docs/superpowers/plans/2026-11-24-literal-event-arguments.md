# 字面事件参数线索实施计划

**目标：** 在原静态事件检查中提示有限的字面变体参数被零形参监听器忽略的可能性，不新增硬阻断。

**架构：** Node复用原有本地监听边和词法描述，独立统计argumentChecked/argumentSkipped及argumentAdvisories；Java严格接收事件协议v3，构建摘要同时供单/多Reviewer使用；Python调整生成、修复与审查证据提示。

**技术：** Vue compiler AST、Node测试、Java/Jackson/JUnit、Python/pytest。当前会话按executing-plans执行，用户已授权具体方案，不另起聊天或要求重复批准。

- [x] 新建Node专项测试，以Hero双目标emit和App固定目标零形参处理为正例，原实现应缺少线索；加入消费参数/相同值/未知来源/闭包arguments/rest/default/类型/作用域/截断等反例，严格限4条且不泄漏值。
- [x] component-contract-check.cjs新增literalSources及零形参普通函数检查，复用有限边，禁止动态和不完整推断；事件版本3新增三个字段，不改变原对象载荷与名称门禁。
- [x] VueEventCheck.java严格校验14字段、版本3、计数/固定kind/位置/不完整组合，摘要仅提示；测试v2拒绝、字段篡改、计数矛盾、零覆盖非空建议、路径不合法及建议本身不阻断。
- [x] generation.py/review.py增加触发值到父接收到目标/状态的逐项核对；忽略参数可能合法，Reviewer须以当前需求及源码确认影响，禁止将静态建议直接判错或扩展完整电商后端。更新提示契约测试。
- [x] Node全部相关测试、Java clean compile及相关组合测试、Python compileall/pytest/uv lock --check；对本次留存项目只读回放，记录触发是否符合预期。
- [x] 更新README、AGENTS与交接第九节，保留本次三轮失败、未知原理由和未测项；若真实模型验收沿用原账本最多剩14次，不重跑追求PASS。
- [x] git diff --check与范围/敏感信息检查，按中文Conventional Commit提交必要代码、测试及文档，报告哈希。
