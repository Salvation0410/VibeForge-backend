# Vue 构建修复工作集实施计划

> 执行方式：使用 executing-plans 在当前会话按任务实施；用户已明确授权实施和本地真实调用。

**Goal:** 可信必填属性构建失败进入已有有界工作集，避免只读耗尽预算后空修复。

**Architecture:** Java 输出可选有界结构化证据；Python 经当前快照安全清单形成私有问题，复用原 repairWork；公共协议与总预算保持。

**Tech Stack:** Java 21 / Spring / Python / LangGraph / pytest / JUnit。

- [ ] 1. 新增会失败的 Java 结构化证据测试、Python 硬错误到工作集测试、低预算只读拒绝测试，并确认现有实现不能满足。
- [ ] 2. 修改 VueBuildResult、VueComponentContractCheck 和 InternalAiToolsController，只输出已校验的最多四个 file/component/prop；更新共用 internal-ai-tools-v1.json 的可选字段及两侧契约测试。保持旧三字段响应兼容。
- [ ] 3. 新建 models/vue_build_repair.py 验证有界问题与完整快照清单；nodes/verification.py 构建失败时获取清单、生成私有 repair_issues/manifest，并投影构建事件，禁止结构化证据回父图。测试旧结果、不安全路径、截断清单、错误状态、取消和正常构建不触发额外快照。
- [ ] 4. 工作集首次写入前的读取若会耗尽剩余工具则按原停滞错误和两次纠偏拒绝整批；复用原三批读取上限。补提示核对生产者和消费者对象字段。保持 stage/checkpoint/取消与原预算，不新增文件读缓存。
- [ ] 5. 运行 Java clean compile 与相关 JUnit、Python compileall、pytest、uv lock --check、git diff --check；观察已有 Windows 重排器命名锁时保留原失败证据并以临时测试模型身份隔离，不改本地服务私有配置。
- [ ] 6. 更新 ai-service/README.md 和真实交接文档；重启受影响服务，验证健康、鉴权和结构化构建结果。
- [ ] 7. 同一60次账本进行最多两次场景的正常平台真实验收，记录请求ID、调用额度、读写与修复轮次、构建和审查结果；必要时仅在同一额度内进行修正后的重验。不得手动改生成工程或隐瞒失败。
- [ ] 8. 复核 diff、补齐本轮验证/限制/下一轮建议，提交任务相关文件，保留本地私有配置及日志在忽略目录。
