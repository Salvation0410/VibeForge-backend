# 可视化结构化编辑实施计划

用户授权按交接继续优化，并选择复用已有可视化编辑入口。继续使用当前 dev 工作区；前端为独立仓库，保留已有客服修改。本轮无真实模型调用授权，不使用上轮额度。

## 固定契约

GET `/api/apps/chat/gen/code` 保持原 appId/message，新增可选 query `editIntent`（JSON，最大 2048 字符）。内部 Python GenerationRequest 新增可选顶层 `editIntent`；禁止通过自由 metadata 宣称已验证。

对象严格仅有四个 string 字段：`version="1"`、`selector`、`property`、`value`。selector 最长512，仅支持 `body > tag:nth-of-type(1)` 形式的1至12段路径；tag为 `[a-z][a-z0-9-]{0,30}`，序号1至9999。前端按实际父子关系计算并确认 querySelectorAll 只匹配选中元素；不允许选中 body/html/script/style/link/meta。

property/value：color或backgroundColor使用 `#[0-9a-fA-F]{6}`；fontSize使用1至96整数加px，无前导0；textAlign只允许left/center/right。中文label分别为文字颜色、背景颜色、字号、对齐方式。

三端 canonical message 精确为：

```text
请只修改选中元素的{label}为“{value}”。
元素定位：{selector}
保留原有功能、文字、图片和操作方式，其余内容不变。请先确认定位唯一，无法唯一定位时不要修改。
```

客户端生成这段原文，Spring/Python严格比较，不能附加自由需求后仍跳过重写。原文照常入历史，不被摘要替换。仅校验定位语法，后端不假装已校验DOM或源码唯一性；模型仍需定位和遵守约束。没有实际产物时Python拒绝结构化修改。

兼容：不带editIntent完全保留旧路径；Legacy选中时拒绝结构化请求并给出明确错误，不静默忽略字段。新前端到旧后端兼容时canonical仍是正常修改需求，但不承诺跳过重写。

Python `structured_edit_enabled=true` 仅控制是否省去辅助重写；原重写总开关优先。命中时skipped/STRUCTURED_EDIT，brief=null；无需依赖experimental conservative。关闭此开关时对有效canonical需求正常重写。输入审核、硬预算、取消和窄子图边界保持。

## 实施与验证

- [x] Java：DTO严格JSON解析/字段/原文校验，Controller/Service/Gateway可选扩展，旧调用兼容，LangGraph顶层传递，Legacy拒绝；针对测试+干净编译。
- [x] 前端：选中元素后显示轻量独立属性表单；独立“应用修改”动作不混入自由输入；校验当前选择、禁用生成中状态、刷新/导航清除旧选择，保留2000字符/80ms/终态回源/单次预览；类型检查和构建、纯函数/消息来源测试。
- [x] Python：严格typed输入/原文匹配、顶层状态和context传递、已有产物要求、独立诊断；三分支/开关/恶意metadata/非法字段/原文不一致/缓存/取消测试。
- [x] 三端共享静态契约样本，验证同输入canonical完全一致；新增文档和README，不宣传真实视觉验收。
- [x] 独立规格与质量审查；compileall、全量pytest、lock检查、Java相关测试/clean compile、前端测试/type-check/build；分别提交两仓库并生成交接。
