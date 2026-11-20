# 第25轮：五题冻结证据真实回答验证

日期：2026-11-20。用户明确“按照建议继续执行下一轮验证”，承接第24轮5题、两组共最多10次Chat建议。新计划提交 `606f9b9`。本轮只验证冻结证据到真实回答，不推广生产。

## 触发问题与方案

第24轮新语料cap2使完整标注引文111→118/130，但这不能证明真实回答改善：部分已有等价证据，部分新证据可能被模型忽略，模型也可能补写未被上下文支持的知识。因此选5道已知关键题隔离证据选择变量：app-generation-005/017、deployment-download-003/013、community-007。

每题cap1/cap2各一次，逐题交替先后顺序；使用现有客服R1系统提示、正式用户提示和答案字段/引用解析。当前配置为deepseek-chat、temperature=0.1、max_tokens=8192、超时60秒、text输出格式、提示16384字节。两组完全相同，只替换第24轮最终冻结上下文；不重新Embedding、BGE或检索，不加入模型评审调用。模型配置、消息、实现与输入摘要在首次发送前冻结。

## 实现与失败边界

独立入口 `rag_qa_linked_answer_experiment` 从已提交第24轮结果校验comparison及input-integrity摘要，并检查44份完整性输入。5题对应11项公共事实，事实标签仅进入评审材料，不进入模型。沿用正式OpenAICompatibleModel回答方法，通过实验覆写_invoke直接发送HTTP，继承正式提示构造、上下文预算和JSON/引用协议解析，不构造SDK客户端。请求stream=false、max_tokens字段，两组同协议；这不宣称与历史SDK所有线缆字段完全一致。

prepare只生成本轮计划；live显式指定计划摘要和用户授权记录。新账本固定 `tmp/rag-qa-linked-answer-20261120/authorization.jsonl`，排他创建，发送前写入、flush、fsync预占，最多10次；失败、取消和发送结果不确定都不返还。HTTP无自动重试、不跟随重定向、不使用环境代理；响应有界2MiB，只接受stop结束及正式答案协议。首次传输、协议或落盘失败停止后续发送，保留not_run槽位，不换账本恢复。预占/尝试发送不等于供应商精确计费证明。

每槽位保存输入摘要、预占/发送状态、HTTP状态、结束原因、供应商报告token、耗时、回答及固定错误码；原始成功HTTP响应只留本轮tmp。输入在发送前和结束后复核，变更失败关闭；不是跨文件事务或抵抗恶意本地修改的安全系统。进程强杀/磁盘故障可能留下残缺结果或不确定状态，固定账本仍不授权自动续跑。

## 评审口径

分别记录答案覆盖、入选上下文支持、实际引用支持，保留原rubric/等价表达与争议，不按新答案反向修改。11项事实两组共22判断不是90题总分；正确格式和合法引用不代表语义真实。代理复核与真人pending分开，不调用额外评审模型。

特别检查005的HTML单文件内样式/脚本及Vue文件工具、017的目标用户与目标、003的实际可用产物和明确Vue条件、013的权限/任务/源码及Vue构建、community007的作者/管理员可见性。首次生成失败不能直接推断目录不存在；评论审核权限不能推出查看待审帖权限。同文档未入选小节不能作为本次模型已见证据。

## 复现与人工入口

本轮现场目录 `tmp/rag-qa-linked-answer-20261120/` 保留plan、freeze、authorization账本、report、逐槽位结果与原始响应。frozen-review-basis保存5题证据和事实；human-review-template提供真人逐项填写入口，需记录reviewer并另存文件，不能覆盖代理记录。模板结构独立，不直接使用旧回答盲评导入器。

准备/真实命令仅供执行记录；账本一旦创建，不能直接重跑或删除后重跑：

```powershell
uv run --offline python -m ai_service.orchestration.rag_qa_linked_answer_experiment --prepare
uv run --offline python -m ai_service.orchestration.rag_qa_linked_answer_experiment --live --plan-sha256 484a9ab5f3c368982f3c73f4d737bf7b51792010df10bea85167f310c11642c7 --authorization "按照建议继续执行下一轮验证"
```

常规测试使用合成响应，不访问真实模型：

```powershell
uv run --offline pytest tests/test_rag_qa_linked_answer_experiment.py -q
uv run --offline pytest
uv run --offline python -m compileall -q src
uv lock --check --offline
```

本轮不需要启动Docker/Spring/HTTP Python服务，模型由本地实验进程直连现有配置端点；未验收Milvus、权威发布、当前快照、HTTP双服务或实际用户应用。两组单次采样、已反复使用的5道开发题，不能证明稳定收益或泛化。未知执行状态仍人工处理、候选无自动GC、快照撤销竞态与幻觉边界不变。用户既有README围栏修改和取消计划保留。

## 本轮真实执行结果：首个协议失败后停止

实现与测试先提交为 `0f2f097`，再执行唯一一次live。实际9次Chat预占并发送，8次正式解析成功、1次协议失败、第10槽位未运行。9次均HTTP 200、finish_reason=stop；第9次community-007:cap1的message.content中出现 `,citedChunkIds"`，字段名缺少起始双引号。正式解析器拒绝为ANSWER_CONTRACT_INVALID，运行器退出码2；这是本轮真实失败，不能因HTTP成功算作完成回答。

账本保留1授权+9预占，不删除、不续跑；剩余1槽位因首错停止规则封存，不转移到新账本。原始响应未修补。离线注入原始message到正式answer_customer_service复验8接受/1拒绝，json.loads同样在位置76拒绝。错误正文不进入正式回答或语义分数。community-007:cap2没有答案，两个community事实的两组共4判断记不可评分，不填0分或虚构拒答。

供应商报告prompt/completion/total tokens分别6793/1022/7815，含失败调用。请求配置deepseek-chat，9次响应均自报deepseek-flash；与第19轮相同，不能据配置名认证底层模型身份。未额外探测、重试或调用模型评审；新Embedding/BGE/INDEX/生产写入均0。token统计不是精确费用账单。

report字节SHA256：`24ae7bd26826b06492785c7f6beca96719eb23d3ec313532676a8daafa07f51b`；账本字节SHA256：`672b2088571b5737074c113ce1152fd4b47de6d7a809fef327a3ed952be5f61b`。9份raw摘要、全部10槽位状态、请求摘要、代理判断和冻结证据收录于[本轮结构化结果](knowledge/product/evaluation/results/qa-linked-answer-validation-20261120-v1.json)；原HTTP包不提交。

## 回答与证据：有收益，也有遗漏和局部退步

主代理逐题核对并由独立代理复核。只有4题9事实形成有效配对，共18判断；按完整事实口径cap1为1/9、cap2为5/9。这不是题目正确率，更不是90题总体成绩。两组8个有效响应均answered=true，没有有效拒答；原数据集可答不表示本轮不足的片段已足够回答所有子项。

| 题目 | 本轮回答观测 | 仍不足或退步 |
| --- | --- | --- |
| app-generation-005 | cap2补MULTI_FILE拆分静态文件 | 两组均缺HTML单文件内样式/脚本及Vue文件工具/组件工程说明；cap2的HTML静态概括缺同等直接引文支持 |
| app-generation-017 | cap2补目标用户/目标、主要页面或区域 | cap1视觉区域示例能否算第二项等价表达待人工裁决 |
| deployment-download-003 | 两组仍未完整说明可用源码与Vue构建前提 | cap1说“不能保证可以正常部署”，cap2省略此直接提醒并转向重新部署；不能报告无退步 |
| deployment-download-013 | cap2补明确Vue构建产物；创建者/任务/目录两组已覆盖 | cap1附带下载权限建议，不能据此认定部署源码不存在的实际原因 |
| community-007 | cap1协议失败，cap2未运行 | 两组均无有效可比较答案；原始文字未说管理员例外只作失败诊断，不计分 |

新增完整覆盖4项：005:02、017:01、017:02、013:02；均有本次入选和实际引用支持。其中017:02仍有等价裁决空间，其余3项为当前代理复核无争议的增益，不等于真人已确认。003:02当前两组均partial；若人工把cap1“不能保证”视为完整等价表达，则cap1变为2/9，并产生1项full→partial退步。原rubric、历史标签和等价表达未改。

003:02尤其说明“没有完整引文”不等于“没有可用语义证据”：两组最终都有核查源码/构建可用性的失败排查块，足以支持冻结等价表达“先确认可用结果”，但回答均未采用；cap2甚至未引用该块。它同时包含证据条件不足（缺明确Vue）和模型遗漏，不能全部归因召回。005中HTML完整文档补充措辞丢失的第24轮风险仍保留，当前两个答案都没表达该子项。

18个有效判断附17处答案和26处上下文精确摘录，分开记录answerCoverage、allContextSupport与actualCitationSupport。实际引用是回答级ID集合，没有逐句锚点；对应关系仍是代理语义判断。合法ID、JSON和摘录匹配不能消除幻觉。未声称全部句子都被直接证据证明，也未把不完整回答等同于凭空编造。

## 新鲜验证与交付状态

- 定向测试：30通过；完整pytest JUnit：2323项，2297通过、26跳过、0失败、0错误。2条依赖弃用警告（Starlette/AnyIO、LangGraph allowed_objects）。本轮日志为python-verified.txt/xml，不沿用第24轮成绩。
- compileall及uv lock --check --offline退出0。未运行Java/前端/浏览器或可选基础设施端到端验收。
- 离线观测脚本核对9笔账本顺序、原始响应、usage、正式解析8/1、未发送第10项和全部55项冻结绑定；68份历史保护文件摘要不变。README等于原用户文件加本轮已提交附录，原围栏修改保留。
- 22个判断槽位完整（18可评分、4不可评分），43处摘录精确匹配。重复fact、虚构答案、虚构证据、错误actualCited四种负例均拒绝。这证明材料一致性，不能自动证明语义评分。
- 本地观测脚本初次在Windows事件循环创建socketpair时被禁网钩子拦截；将钩子移至事件循环初始化后，业务回放期间禁止连接，复验通过。该离线设施问题未触发真实请求、未改冻结实现或原结果。

离线复核与材料生成命令（不发起真实调用，会重写本轮派生JSON和human-review-ready.json；真人填写结果须另存）：

```powershell
cd ai-service
uv run --offline python -X utf8 ../tmp/rag-qa-linked-answer-20261120/verify_observations.py
uv run --offline python -X utf8 ../tmp/rag-qa-linked-answer-20261120/build_delivery.py
```

以上两个收尾脚本及XML/raw只留本机tmp；可提交的实验入口及30项测试在源码中。独立结果包含完整公共问题、事实、上下文、有效回答和审查理由，后续交接不依赖隐含代理结论。新真人入口为 `tmp/rag-qa-linked-answer-20261120/human-review-ready.json`，已填实际答案/上下文/可评分状态，保留原空模板。请另存、填写reviewer及判断；humanReview继续pending，不将本轮代理复核冒充人工。

冻结计划保留运行前原文及复选框以保持摘要，实际执行状态以本交接和结果为准。计划提交606f9b9、实现提交0f2f097，收尾提交由最终回复报告。没有部署、服务启停、.env修改或生产策略推广；用户原README修改与未跟踪取消计划不混入提交。

## 下一轮建议，留待用户决定

优先使用现成真人包裁决017:02和003:02，再考虑证据选择优化。若继续工程实验，建议先做零模型调用的“必要条件与例外同块”答案单元候选：只围绕005类型定义、003部署前提、community管理员例外设计，不更改产品事实、不把私有rubric作为运行时选择输入；离线核对全90题的事实与文档多样性退步。新分块无法直接复用旧向量/BGE宣称真实检索收益，后续真实检索仍需独立冻结方案及额度。这是提案，本轮未实施。

第19轮已经有text/json_object的90题真实对照及同样模型别名现象。本轮一次坏JSON不足以重复整个格式实验或直接打开生产JSON模式；可以复用既有证据另做上线决策，但不能承诺消除协议失败或幻觉。混合检索留待真实Top8缺项单独处理，它不能修复坏JSON、已入选证据未采用或管理员例外未被选择。cap2仍是实验候选，需承担文档多样性下降和本轮003局部退步，不自动推广。
