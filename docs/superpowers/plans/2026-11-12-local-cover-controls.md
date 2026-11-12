# 本地封面与控件一致性实施计划

> **For agentic workers:** 使用 subagent-driven-development 按独立模块实施，完成后统一审核和提交。用户已选择方案A，实施中不重复请求确认。

**Goal:** 降低显式本地驱动截图的初始化不确定性，正确释放浏览器，并改善新增控件的视觉一致性。

**Architecture:** 截图服务传递明确驱动配置并在单次截图内管理WebDriver；模型沿既有准备上下文接收新增白名单控件规则。两个模块不改变SSE或修复预算。

**Tech Stack:** Java21/Spring/Selenium/Mockito，Python/pytest，Chrome本地浏览器。

## 1. 截图模块

- [x] 在WebScreenshotUtils生命周期测试复现成功/异常后未quit，补显式路径不存在、普通文件检查及配置优先级断言。
- [x] 修改utils/WebScreenshotUtils.java、service/impl/ScreenshotServiceImpl.java、AppServiceImpl.java和application.yml，按设计实现单次浏览器、显式driver service、固定分段计时及任务异常边界；必要计时工具单独成类。
- [x] 增加离线上传失败清理及日志不含敏感标记的测试。显式浏览器测试仅在opt-in时运行，用本地页面，不访问外部测试站。
- [x] 执行`mvn test -Dtest=WebScreenshotUtilsTest,ScreenshotServiceImplTest`及实际新增类，保留首次失败；最终`mvn clean -DskipTests compile`后重新跑定向测试。

## 2. 控件规则

- [x] 在test_preparation_evaluation.py增加“电商/不要电商/否定后新增/已有页面”的选择用例，先验证“电商首页”回归失败。
- [x] 增加control-consistency@2并纳入resolve_skills公共规则，保持原规则版本；补“电商”、递增SELECTION_VERSION。必要时调整诊断完整ID数量及预算预期，保留旧ID合法性。
- [x] 复用test_generation_preparation.py所有适配器入口断言新增规则恰好一次、开关关闭不注入、原文/结构化编辑边界保留。
- [x] 执行准备相关pytest、compileall、uv lock --check，再执行默认全量；本机RAG/重排器锁冲突时按既有ignored隔离脚本单独记录，不修改用户配置掩盖失败。

## 3. 新隔离验收及交付

- [x] 重新检查端口、模型账本、会话，复用私有运行器并用新目录/新提交标记；启动隔离Spring18123/Python18000/代理18001（仅空闲时）。
- [x] 新应用生成书单基线，再提交删除增量，不发旧标记；模型调用/失败/重试以原无上限账本记录。
- [x] 使用实际预览dist/index.html，在1440/390px检查按钮组间距、高度与样式，功能验收使用共享读书清单/删除探针；失败保留，不要求自然门禁必然触发。
- [x] 在新应用上验证截图显式驱动及关闭记录，若部署调用包含上传则核对封面HEAD和更新时间；无法访问OSS时明确记录失败阶段。
- [x] 更新ai-service/README.md、根README本地截图说明、AGENTS及交接本轮结果；保留README既有末尾围栏修改，分离暂存。
- [x] 检查实际diff、git diff --check和Git状态，精确提交本轮文件，不推送，关闭本轮临时进程。

## 实际执行补充

试验规则@1首次自然样本功能通过但视觉失败，保留证据后升级@2，并插入一个明确视觉修正请求，再做原删除增量。默认Python环境失败及隔离最终结果、真实调用41次、封面分段耗时4477ms见交接第34节；不把试验@1结果称为最终@2首次生成通过。
