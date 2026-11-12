# 潮牌电商网页生成失败排查与修复建议

排查日期：2026-11-12（北京时间）。本轮仅只读排查运行数据与源码，未修改生产行为、应用文件或配置，未新增真实模型调用。

## 请求与实际失败路径

- 请求：`2c0c3467-2f55-4f01-9cd3-7a46a4c15ec2`。
- 应用：`478391719128846336`，生成类型 `VUE_PROJECT`。
- MySQL 台账：15:38:54 开始，15:41:19 结束，`FAILED / GENERATION_FAILED`，没有取消请求。
- PostgreSQL 摘要：最后节点 `quality_review`，`qualityPassed=false`，`repairCount=2`，`toolCallCount=36`。
- Redis 工具成功结果和 LangSmith 节点时间记录相互印证以下流程。

| 阶段 | 证据 | 结果 |
| --- | --- | --- |
| 首次生成 | `project_build:0` | `VUE_NPM_BUILD_FAILED`：ProductGrid 导入 products.js 未导出的 `categories` |
| 第一次修复 | `project_build:1` | `VUE_COMPONENT_CONTRACT_FAILED`：App.vue 调用 HeroSection 缺少必填 `image` |
| 第二次修复 | `artifact_validation:2`、`project_build:2` | `valid=true, errors=[]`；`built=true`，静态必填属性检查未发现阻断问题 |
| 最终审查 | PostgreSQL 状态摘要和 LangSmith `quality_review` | 质量未通过，两次修复额度已耗尽，进入 `fail_quality` |

两次修复用于解决先前构建问题，最后首次进入质量审查时已经没有剩余修复额度。36 次工具调用低于默认 40 次上限，本次直接终止条件是修复轮数，并非工具预算耗尽。

## 为什么用户只看到空列表

`ai-service/src/ai_service/orchestration/nodes/verification.py` 中的 `fail_quality`，在构建成功时统一打印 `validation.errors`，未区分硬校验失败和质量审查失败。最后硬校验已经通过，所以错误文本显示 `Artifact did not pass validation or quality review: []`。

这是失败诊断表达缺陷，不说明质量审查没有问题，也不能据此推断用户系统提示词格式错误。

## 当前源码确认的问题与证据限制

应用目录：`tmp/code_output/vue_project_478391719128846336`。

- SiteHeader 中新品、系列、全部商品分别指向 `#new-arrivals`、`#collections`、`#shop`，当前 Vue 模板没有这些目标。
- HeroSection 的“查看系列”指向不存在的 `#series`；页脚还引用不存在的 `#new`。源码中有 collectionCards 数据，但 App 未渲染对应系列区块。
- 购物袋显示固定 0，链接指向不存在的 `#shop`，未发现购物袋状态和操作实现。是否必须实现完整购物流程仍需依据实际需求确定，不能仅凭“电商网页”要求扩展成交易系统。
- 两个商品缺少 category，分类筛选会将它们从非“全部”的结果排除；是否属于需求缺陷需进一步确认分类要求。

本轮静态枚举得到的缺失锚点为 `collections/new/new-arrivals/series/shop/top`。没有执行浏览器验证；这只是当前可见源码的核对，不证明所有交互表现。

完整图 checkpoint 在终态维护中被清理；状态摘要未保存质量审查理由，LangSmith 旁路仅保留白名单元数据。无法从现有持久化记录还原最后 Reviewer 的具体问题正文，因此以上源码缺陷不能冒充当时 Reviewer 的实际拒绝理由。

## 建议与方案比较（尚未实施）

1. **优先修复失败诊断，保持原预算。** `fail_quality` 按硬校验、构建、质量审查分别生成稳定错误；质量拒绝且预算耗尽时明确提示“构建已通过，质量检查未通过，自动修复次数已用完”。在既有摘要/旁路中保留经过白名单校验的问题码、问题数和失败阶段，不回显模型自由文本、源码或凭据。同步核对 Python 错误码到 Java 网关、任务台账及前端的适配与测试。收益是失败可定位，代价是不能直接提高本次产物成功率。
2. **修复当前应用的可见接线缺陷。** 以当前源码为基线，统一商品锚点，补齐用户确需的系列区块及其锚点，核对购物袋操作预期。对可见静态链接目标做确定性检查并进入原有限修复，动态目标和路由需明确跳过，避免误报。先离线核对，再构建与浏览器点击验收；不把 Reviewer PASS 代替点击验收。收益是解决当前页面具体问题，代价是对该应用的修复不保证后续生成不再出错。
3. **后续再评估修复预算。** 可比较提高共享修复上限与分开计算构建/质量修复的方案。前者改动较少但增加最坏耗时和调用成本；后者可避免构建修复占尽质量修复机会，但状态、路由、checkpoint 与测试更复杂。两者均不能保证模型收敛，也不应绕过质量门禁。建议先完成前两项并积累失败数据，再决定预算策略。

建议实施顺序：诊断可观测性 → 当前产物最小修复和浏览器验收 → 再决定预算策略。生产行为修改须待用户确认方案。

## 本轮验证范围

已只读核对 MySQL 任务台账、PostgreSQL 脱敏摘要、Redis 三轮校验/构建结果、LangSmith 请求级节点元数据及当前应用源码，并静态枚举锚点。未运行新增真实生成、完整测试集或浏览器验收；未重启服务。仓库原有的 `ai-service/README.md` 修改和取消回归计划未动。

## 用户确认后的平台修复（2026-11-12）

上述排查和历史证据保留。用户确认按方案实施后，修改范围落在后续生成平台：没有手工修改、构建或重新发布这个失败应用，也没有把它标为成功。

1. `fail_quality` 现在优先区分本轮硬校验、Vue 构建和质量拒绝；分别保留 `ARTIFACT_VALIDATION_FAILED`、具体构建错误、`QUALITY_REVIEW_FAILED`。硬校验/质量预算耗尽显示次数，质量拒绝不再打印空硬校验列表。缺少明确 built=true 也不能假称构建通过。默认两次修复与40次工具预算不变。
2. 验证子图私有诊断保存 stage/verdict/issueCount/issueCodes，通过双重白名单投影进入既有 PostgreSQL JSONB 摘要和节点旁路。只保存固定问题码；任意多 Reviewer 代码归为 UNCLASSIFIED，布尔兼容不伪造问题。新候选清空旧诊断，不向父图、修复模型和公共事件输出；完整 Reviewer 理由仍不能从摘要恢复。
3. Spring 强制 Vue 构建在 npm/dist 与必填属性检查通过后增加可信脚本锚点模式；检查完整、有限的静态本地组件闭包及 index.html。确定缺失以 `VUE_ANCHOR_TARGET_MISSING` 进入原修复，环境或协议异常 `VUE_ANCHOR_CHECK_UNAVAILABLE` 直接失败。它只支持有限字面链接与普通直接挂载入口；动态/路由/插件/未知调用/截断等跳过，不代替浏览器验收。沿用扫描与取消边界，无成功缓存、源码补丁或版本回滚。

只读运行新检查器时，该应用返回四条有界缺失目标：SiteHeader 的 shop、HeroSection 的 series、SiteFooter 的 new 和 shop。此结果来自新静态规则，不能写成历史 Reviewer 的真实拒绝理由，也不代表修复后页面交互通过。

验证数字、运行命令、环境差异和交付边界见[真实验收交接第30节](generation-structured-edit-acceptance-handoff.md)。下一步优先在服务重启后通过正常生成入口验证错误展示与修复结果；不要绕过门禁、重置旧请求或手改失败产物来证明成功。构建问题占满修复额度、动态范围被跳过、模型判断或交互逻辑错误仍可能造成失败。
