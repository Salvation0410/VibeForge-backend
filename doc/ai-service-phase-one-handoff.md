# AI 服务 LangGraph 交接说明

> 更新日期：2026-09-30
> 当前分支：`codex/customer-service-rag`
> 文档目标：让后续开发者用最短时间确认当前事实、验证证据、人工验收门和下一步优先级。

## 1. 五分钟接手摘要

### 当前状态

- Spring Boot 仍是业务数据、项目文件、构建与发布状态的唯一所有者。
- Python AI 服务负责模型调用、LangGraph 编排、工具选择、参数校验、质量检查和有限修复，不连接业务 MySQL，也不直接读写项目目录。
- Spring 通过 `AiGenerationGateway` 在 Legacy、LangGraph 和稳定灰度模式之间路由，对前端继续保持原有 SSE 协议。
- LangGraph 已支持 HTML、MULTI_FILE、VUE_PROJECT 三类生成、最多两次修复、Vue 有界工具循环、构建失败修复、质量检查、取消和终态事件。
- Vue 多 Agent 质量审查已经实现，但 `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false` 默认关闭且只对 `VUE_PROJECT` 生效；开启后由 requirement、function、technical 三个只读 Reviewer 并发审查真实源码快照。
- HTML 与 MULTI_FILE 只有在 Spring 严格解析、确定性校验和不可变版本发布成功后才能完成；HTML 还执行 Selenium 浏览器烟测。
- 用户此前确认的 P0 真实环境验收不包含本轮新增的 Vue 多 Agent 能力；本轮真实 PostgreSQL、真实模型和完整前端人工验收仍明确待执行。
- Legacy LangChain4j 仍是生产回滚路径。稳定灰度数据和回滚窗口满足要求前不得删除 Legacy。
- Python checkpoint 已迁移到独立 PostgreSQL 数据库 `yu_ai_checkpoint`，使用官方 `AsyncPostgresSaver` 和同池脱敏状态表 `ai_workflow_status`；Python 不再依赖 Redis database 2。
- 本轮不启用长期记忆，不创建或注入 `PostgresStore`。

### 当前最高优先级

1. 先在本机现有 PostgreSQL Docker 容器中创建并初始化独立角色和数据库 `yu_ai_checkpoint`，配置 Python checkpoint 连接，执行 opt-in 集成测试并确认 `/health/ready`；当前代码与测试入口已就绪，但数据库尚未创建。
2. 在上述 checkpoint 环境可用后，再启动 Spring、Python 和 Vue 前端并开启 Vue 多 Agent 开关，完成首次生成、针对性 major 修复、停止传播、超时/错误凭据和延迟/token 成本人工验证。
3. 继续 P1 真实 Uvicorn/代理压力和真实 npm 长构建压力，补充资源收敛证据。
4. 长期记忆保持关闭；只有出现清晰的跨 thread 用户或应用记忆需求时才重新评估 `PostgresStore`。

### 客服机器人 RAG 实施进度

- 设计提交 `b61fc07`、实施计划提交 `be6b5db` 已完成。当前 Task 1–13 已实现：Task 13 增加关闭态延迟导入、独立客服健康摘要、版本化 synthetic 评估集、离线指标 helper 和运维/回滚文档；下述真实环境验收仍未声称完成。
- Task 1 的 MySQL 知识文档与 ETL outbox 表、对应 MyBatis-Flex 实体和 Mapper 已在提交 `8e18149` 完成。
- Task 1 定向 schema 测试 2 项通过，`mvn clean -DskipTests compile` 通过，暂存差异的 `git diff --cached --check` 通过。仓库没有 `mvnw.cmd`，测试和编译使用系统 Maven；测试实际命令为 `mvn test -Dtest=CustomerServiceKnowledgeSchemaTest`。
- 尚未在真实 MySQL 执行 DDL、CRUD 或并发任务认领验证，这些仍是人工待验收项。
- Task 2 私有 OSS 知识文档能力已在 `89d1487` 实现，并由 `312b9f5`、`a0be99a` 修正 DOCX 宏绕过和 PDF 文本误判。上传策略支持 PDF、DOCX、Markdown、TXT，校验扩展名、MIME、内容和 20 MiB 上限，规范展示名并计算 SHA-256；对象键由随机 ID 生成，上传显式设置 private ACL，只返回对象键和文件元数据。另提供可配置有效期的单对象签名 GET URL 与对象删除。
- DOCX 校验包含 ZIP 条目数、单条和总展开大小边界，并安全解析 `[Content_Types].xml` 与各 `.rels` 的宏内容类型和关系类型；XML 有大小限制且禁用 DTD、外部实体。Task 2 的 15 项定向测试、`mvn clean -DskipTests compile` 和 `git diff --check` 均通过，使用系统 Maven，未调用真实 OSS。
- 真实私有 OSS 上传、签名 URL 下载和删除仍待人工验收。Spring 对 PDF 只做上传层的扩展名、MIME、大小、空文件和 `%PDF-` 魔数等校验，不识别加密状态；Python Task 4 的 `pypdf` 解析已识别并拒绝加密 PDF。
- Task 3 的 Python RAG 依赖与配置在 `dde4bce` 完成，Windows CUDA 运行时在 `8e9663a` 修正。功能开关 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED` 默认关闭；分块默认 1000 字符、重叠 150 字符，检索 Top 8、最终 Top 3。CloseAI Embedding 独立使用 `AI_SERVICE_CLOSEAI_*`，兼容既有裸键 `CLOSEAI_API_KEY`/`CLOSEAI_BASE_URL`；同一配置来源中前缀键优先，进程环境优先于 `.env`。配置和示例均不含真实密钥。Milvus URI 默认 `http://localhost:19530`，本轮未建立连接。
- Task 3 锁定 `langchain 0.3.30`、`langchain-core 0.3.86`、`langchain-openai 0.3.35`、`langchain-text-splitters 0.3.11`、`pymilvus 2.6.17`、`FlagEmbedding 1.4.2`、`pypdf 6.19.0` 和 `python-docx 1.2.0`。Windows 从 PyTorch 官方 cu124 索引安装 `torch 2.6.0+cu124`；本机驱动 555.97 下实测 `torch.version.cuda=12.4`、`torch.cuda.is_available()=True`。CUDA wheel 约 2.4 GiB，部署时需预留下载、缓存和磁盘空间。
- Task 3 的 83 项定向测试、完整 Python 测试 271 项通过且 1 项跳过；`uv sync --frozen --python 3.12`、`compileall`、`uv lock --check` 和 `git diff --check` 均通过。仅验证依赖导入和 CUDA 可用性；没有下载或运行 BGE Reranker 模型，没有调用 CloseAI，也没有连接 Milvus。
- Task 4 在 `51dac9d` 实现安全下载和文档解析，并由 `4e7d4a6`、`e5e7b8d` 修正标题与表格顺序、下载和取消边界。`KnowledgeDownloader` 仅允许配置白名单内的 HTTPS 主机，逐跳校验全部 DNS 结果为公网地址，并对 DNS、连接和读取分别设置超时；每次重定向独立创建客户端，以当前主机设置 Host 和 TLS SNI，避免同 IP 跨主机复用旧连接。下载按声明和实际字节数限额流式写入临时文件，核对 SHA-256，失败与解析完成后清理临时文件；错误、日志和返回元数据不包含签名 URL。
- Task 4 支持 PDF、DOCX、Markdown、TXT 解析。PDF 保留页码并拒绝加密或无文本文件；Markdown/DOCX 保留真实标题层级，TXT 保留行范围，DOCX 普通、合并和嵌套表格按文档顺序提取并保留表格/行定位。`RecursiveCharacterTextSplitter` 默认分块 1000 字符、重叠 150 字符，生成稳定的 `documentId:documentVersion:index` chunk ID，按实际产出执行最多 10000 个 chunk 的上限。同步解析与切分在线程中执行，取消时等待工作线程结束再清理临时文件。
- Task 4 定向测试 55 项通过，完整 Python 测试 326 项通过、1 项跳过；`compileall`、`uv lock --check` 和 `git diff --check` 通过。测试使用本地生成文档和模拟传输；真实 OSS 签名下载、真实 TLS/SNI 与同 IP 跨主机重定向，以及超时、大文件和取消压力仍待人工验证。首期不做 OCR，扫描 PDF 因无可提取文本而失败。
- Task 5 已完成 CloseAI Embedding Provider 和 Milvus versioned store。Provider 单例复用底层客户端，确定性批处理 document embedding，分别支持 document/query，严格校验数量、维度和有限数，并把供应商异常映射为不含密钥或响应正文的稳定错误。Milvus store 支持版本化 chunk/manifest、完整写后校验、旧版本保护、增量激活、全量 staging + alias 切换、确定性 tombstone、COSINE 分数语义、float32 canonicalization、分页读取和单文档 10000 条硬上限；同步 RPC 使用可配置 timeout，取消时 drain 已启动 RPC 后才释放 permit/本地锁，alias 状态不确定时保留 staging。完整 alias 参与业务集合 fingerprint，control collection 也包含完整 alias hash，长 alias 之间保持隔离。
- Task 5 采用显式 fail-closed mutation lease。Spring/MySQL Outbox 是唯一允许承担跨实例 mutation coordinator 的组件；Python store 的 `upsert/delete/rebuild` 都要求不可伪造的 `scope / operation / fence / expiry / proof`，默认 coordinator 为 `DenyAll`。同一 document scope 串行，collection rebuild scope 与全部 document scope 互斥。Task 6 已通过认证内部接口原样传递 lease，并将同步 `MilvusClient` 构造移入线程 offload；Task 7 已实现 MySQL 签发、验证、global fencing 和冲突域，真实多实例行为仍待验收。
- Task 5 提交链可概括为 `e052d20`、`6a6771f`、`6970873`、`2a865b6`、`a5841dd`、`a6ee768`。最终定向测试 49 项通过，完整 Python 测试 375 项通过、1 项跳过；`compileall`、`uv lock --check` 和 `git diff --check` 通过。测试使用 Fake CloseAI/Fake Milvus；真实 CloseAI 尚未调用，真实 Docker Milvus 的 schema、dynamic fields、Strong consistency、分页、批量写入、alias 切换和重启恢复仍待人工验证，真实 Spring/Python lease 集成仍待验收。
- Python 内部 API 当前已提供 Bearer 认证的 `POST /internal/v1/customer-service/knowledge:etl`、`POST /internal/v1/customer-service/knowledge:delete`、`POST /internal/v1/customer-service/knowledge:rebuild` 和只读 `GET /internal/v1/customer-service/health`。INDEX 完整串联安全下载、解析切分、Embedding、Milvus 版本写入和稳定响应，DELETE 与 REBUILD 调用同一 store 边界；请求中的 `scope / operationId / operation / fence / expiresAt / proof` 六个 lease 字段原样绑定到存储操作，不由 Python 补造或改写。Java REBUILD 已有对应 Python 路由，不再因路由缺失而确定失败。
- Spring lease adapter 对每次 mutation 使用 `POST /api/internal/customer-service/knowledge-mutation-leases:validate`，健康检查使用只读 `GET /api/internal/customer-service/knowledge-mutation-leases/health`；网络错误、非 2xx、超时、非法 JSON、字段不匹配和超过 64 KiB 的声明或流式响应均 fail-closed，且不记录响应正文。功能只在 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=true` 时装配外部 RAG 依赖；同步 Milvus 构造在线程中执行，取消时等待构造结束并关闭已创建资源。
- CloseAI Embedding 使用自有同步和异步 HTTP clients，并在 provider 关闭时独立释放。Embedding 维度由 `AI_SERVICE_RAG_EMBEDDING_DIMENSION` 显式配置，默认 3072；单次 ETL 的默认预算为 8,000,000 个元素。REBUILD 每解析一份文档就立即检查累计 chunk、投影元素和默认 64 MiB UTF-8 chunk 文本预算，超限后不再下载下一份文档；Embedding 仅在全部预算预检通过后开始，因此预算失败时 provider 调用数为 0。provider 和 service 均复核实际返回维度。进程内 INDEX/REBUILD 默认并发为 1，等待或执行任务取消会释放 semaphore permit。
- Task 6 提交链为 `172763a`、`4d9e56a`、`34633c9`、`b7e9ce5`、`e3d9bc3`、`70b3f68`。资源上限与配置定向测试分别 6 项、14 项通过；完整 Python 测试 435 项通过、1 项跳过，`compileall`、`uv lock --check` 和 `git diff --check` 均通过。测试使用 MockTransport、Fake CloseAI 和 Fake Milvus；Task 7 现已提供 Spring 内部 lease endpoints 和索引调度端，但真实 Spring/MySQL fencing、OSS、CloseAI、Milvus 和完整 E2E 均待人工验收。
- Task 7 的 MySQL coordinator 使用 guard 表行锁分配全局单调 fence，并将 lease 审计写入独立表；HMAC proof 不入库。签名 lease 包含 `scope / operationId / operation / fence / expiresAt / proof` 六个字段，Spring 提供 Bearer 认证的内部 `validate` 与只读 `health` 接口，验证字段绑定、过期、撤销和 HMAC，异常时 fail-closed。初始化 SQL 与面向既有 Task 1 数据库的升级 SQL `sql/alter_customer_service_knowledge_task7.sql` 已同步。
- Task 7 已实现知识文档 Service、outbox、管理员上传/替换/重索引/启停/删除/任务历史接口，以及 worker 的 claim、过期 reclaim、claim refresh、lease 内快照重验和版本 CAS。INDEX 成功与最终失败通过独立 Spring 事务 finalizer 原子更新文档和 outbox；重试基数、指数退避和 MySQL `DATETIME` 上限均有边界。对 Python 的 HTTP 调用使用覆盖响应头与完整响应体的整体超时，流式按字节限制响应并在超限或超时时取消订阅。
- Task 7 的 REBUILD 全链路源码已闭合：Java 默认最多 1000 个文档，序列化后 UTF-8 JSON 最多 4 MiB，查询只读取 `max + 1` 条并在六字段 collection lease 内重验；Python `/internal/v1/customer-service/knowledge:rebuild` 严格匹配 Java 请求/响应。空文档列表会发布同 schema 的空 collection 并切换 alias，从而清除旧知识；无旧 alias 时使用配置模型和默认 3072 维。staging 由 alias、operationId/fence、ETL 版本和不含 signed URL 的文档 fingerprint 确定性派生；成功重放必须验证 completion marker，并在下载和 Embedding 前返回 `idempotent=true`，不会重复产生供应商费用。
- REBUILD 每解析一份文档就立即检查累计 chunks、按默认 3072 维投影的 8,000,000 embedding elements 预算和默认 64 MiB UTF-8 chunk 文本预算；任一预算失败都停止后续下载且 provider 调用数为 0。Milvus 在幂等探测后先创建 staging，逐文档消费，chunk 每批最多 100 行并逐批 readback，逐文档验证 manifest，最终验证顺序 fingerprint 与计数并写 completion marker 后才切 alias。第二批写入失败、create/insert 期间取消或校验失败均保留旧 alias；清理前重新确认 alias、collection 存在性和本 operation/plan metadata，状态不确定时保留 staging。重建只纳入当前 `ACTIVE` 且 `indexedVersion=documentVersion` 的文档；替换失败后仍指向旧 `indexedVersion` 的文档继续被排除。
- Task 7 最新 Python 全量测试为 467 项通过、1 项跳过，`compileall`、`uv lock --check` 和 `git diff --check` 通过；Java `CustomerServiceAiClientTest` 契约 focused 8 项和 `mvn clean -DskipTests compile` 在此前实现验证中通过，本次文档同步未重新执行 Java。`CustomerServiceKnowledgeMySqlIT` 仍需 URL 与执行开关双 opt-in，本轮未运行，不能声称真实事务回滚、行锁或迁移已验证。
- Task 7 的人工验收仍包括：在真实 MySQL 执行初始化/升级迁移，验证 Spring/MySQL fencing、guard 行锁、事务回滚、过期 reclaim 和多 Spring 实例竞争；验证真实私有 OSS、CloseAI 和 Milvus；执行 Spring/Python lease、INDEX/DELETE/REBUILD、失败恢复与完整 E2E 联调。
- Task 8 已实现 `RerankerProvider`、disabled provider 和本地 Cross-Encoder provider，并将 `FlagEmbedding` 精确锁定为 `1.4.2`。生产 `local_cross_encoder` 使用 Windows `spawn` 模型子进程，`BAAI/bge-reranker-v2-m3` 只在子进程加载一次并常驻 GPU，模型对象不跨进程 pickle；功能关闭或 provider 为 disabled 时不导入、加载模型，也不占用 GPU。
- 主进程与 Reranker worker 使用有界 Pipe、request ID 和单请求锁通信，排队、发送和响应共享请求 timeout。timeout 或取消会立即向调用方返回，同时禁止下一个请求与未完成 GPU 推理重叠；关闭阶段在 grace period 后执行 `terminate -> join -> kill -> join`，并覆盖 startup 取消、部分初始化失败、阻塞 IPC、重复 close 和 Windows process handle 释放。GPU ownership 由模型子进程持有：Windows 使用不含模型或设备明文的 Win32 named mutex，POSIX 使用安全 runtime/private 目录、`O_NOFOLLOW`、owner/mode/type 校验和非阻塞 flock，同一 `model + device` 只允许一个 owner。
- FlagEmbedding 1.4.2 的原始 encoder OOM 探测可能把 batch 降为 0 并无限循环，Task 8 使用项目内安全适配器做有下限的 batch 退避；batch 最小为 1，仍 OOM 时返回脱敏 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`。模型输出显式使用 `normalize=false`，服务统一以 sigmoid 归一化，校验数量和有限数，并保持 score 降序、同分原顺序和输入 chunk 不变。
- Task 8 定向测试 36 项通过，完整 Python 测试 503 项通过、1 项跳过；`compileall`、`uv lock --check` 和 `git diff --check` 均通过。测试使用 Fake Cross-Encoder 和不加载 GPU 的真实 Windows spawn worker，覆盖 timeout、OOM、协议错误、阻塞 Pipe、取消、强制退出、模型资源清理、named mutex ownership 和多父进程竞争。
- Task 8 仍需人工验证：真实下载并加载 BGE Reranker、RTX 4050 上的显存占用、延迟、吞吐和真实 CUDA OOM 行为、POSIX ownership lock，以及实际部署中的多实例/多 Web worker 拓扑。
- Task 9 已实现 Grounded RAG 客服问答 API 与 Bearer 鉴权，并通过 Task 7 Spring bridge 获取文档快照。检索固定为 Top 8 的同一物理 collection snapshot，先读取 manifest 的标量字段，再按 manifest 当前 chunk ID point-get，避免跨版本混读；Reranker 最终取 Top 3，disabled 模式禁止配置 rerank score threshold。Embedding、向量存储、检索、Reranker 和回答失败均映射为脱敏稳定错误码。
- Task 9 的回答协议要求严格 JSON，拒绝 Markdown fence、额外字段和不在检索白名单内的引用；用户 prompt 被视为不可信输入，不得改变系统约束、引用白名单或工具边界。回答受总 deadline 约束，客户端断开会 drain 已启动调用后再释放资源；序列化后的 UTF-8 回答预算在输出前强制校验。
- Task 9 的 Milvus 读路径使用 manifest scalar + point-get，重建支持空文档发布和幂等重放；retention state 保存代数，retired marker 带稳定 `aliasHash`，清理以 marker backlog 和 marker-ID 游标跨 namespace 分页，受 cleanup timeout、scan limit、ownership 验证和 mutation permit fence 约束。protected window 按顺序去重，排除 current physical，缺失项移除，ownership/RPC 不确定时 fail-closed 不删除。
- Task 9 定向 Milvus 测试 99 项通过；最新完整 Python 测试为 576 项通过、1 项跳过，`uv run python -m compileall -q src`、`uv lock --check` 和 `git diff --check` 均通过。自动化测试未替代真实评估集阈值、真实模型/Milvus、客户端断连、多实例 cleanup 或完整 E2E 验收。
- Task 10 已提供登录用户入口 `POST /api/customer-service/ask`。Controller 先通过现有会话取得登录用户，再调用问答 Service；接口使用 USER 维度限流，当前配置为每个用户每 60 秒最多 10 次。请求只接受问题文本，拒绝未知字段，并同时限制字符数和 UTF-8 字节数；不会把用户资料或其他业务数据发送给 Python。
- Spring 调用 Python `POST /internal/v1/customer-service/answers` 时生成并传递 `requestId`，响应必须回传完全相同的 `requestId`。Java 严格要求 `degraded` 字段存在、类型为布尔且值为 `false`，拒绝响应和 source 中的未知字段、重复 source、缺失 locator、超限字段、失效或未完成索引的文档版本，以及不符合当前文档版本格式的 chunk ID；任一契约不满足均以脱敏的服务不可用语义失败，不向客户端透传供应商响应或内部原因。
- 对外 `CustomerServiceAnswerVO` 只包含 `answered`、`answer` 和脱敏后的来源字段，不暴露 `degraded`、相似度或 rerank 分数、prompt、reasoning、内部错误正文。未登录请求保持统一 `code=40100` 语义。
- Task 10 提交链为 `bf736f5`、`f9a377b`、`c9e923b`、`ba178af`、`5ef8611`、`2f9c053`、`fa42253`、`e61e5a8`、`73885b8`。2026-10-01 执行 `mvn -q "-Dtest=CustomerServiceAnswerServiceTest,CustomerServiceControllerTest,CustomerServiceAiClientTest" test`，20 项通过；在 `ai-service` 执行 `uv run pytest -q tests/test_api.py -k customer_service_answer`，5 项通过。两组均为 Fake/本地契约测试，不代表真实 Spring、Python、CloseAI、Milvus、OSS、GPU 或浏览器 E2E 已通过。
- Task 11 位于独立前端仓库分支 `codex/customer-service-rag-ui`，提交链为 `4322156`、`04baf7d`、`1f38e39`、`4d91fb6`、`759f9a9`；这些前端提交没有合入或复制到后端 Git 历史。登录路由为 `/customer-service`，仅登录后显示“智能客服”导航，路由本身也声明 `requiresAuth=true`。
- 当前页面只提供单轮问答：提交问题后调用 Spring `/customer-service/ask`，显示回答或拒答/不可用/已取消状态，并最多展示 3 条去重来源。公开类型只使用 `answered`、`answer`、`sources` 及 source 的 `documentId / documentVersion / documentName / chunkId / locator / excerpt`，不接收或展示分数、prompt、reasoning、`degraded` 等内部字段。
- 每次请求使用独立 `AbortController`；用户取消、组件卸载或新序列替代旧序列后，迟到响应不得更新页面状态。问题提交前先 trim，并同时执行最多 4000 个 JavaScript UTF-16 code units 和最多 16000 UTF-8 bytes 的边界检查。首期不保存问答历史、不使用流式响应，也不启用长期记忆。
- 2026-10-01 在前端隔离 worktree fresh 执行 `node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts tests/customerService.test.ts`，30/30 项通过；`npm run type-check`、`npm run build-only` 和 `git diff --check` 均通过。构建只报告既有 chunk size 警告，没有新增构建错误。该验证未启动真实浏览器、登录会话、Spring、Python、CloseAI、Milvus、OSS 或 GPU。
- Task 12 继续位于独立前端仓库分支 `codex/customer-service-rag-ui`，提交链为 `4767863`、`74fda17`、`2433560`；这些前端提交没有合入或复制到后端 Git 历史。管理员路由为 `/admin/customer-service/knowledge`，声明 `requiresAuth=true` 和 `roles=['admin']`，并加入管理后台导航。
- 知识文件由浏览器 multipart 上传到 Spring 中转接口，不由前端直传 OSS；前端只允许 `.pdf / .docx / .md / .txt`，单文件上限 20 MiB。列表和健康检查采用 latest-wins 请求控制，新的分页请求会取消旧请求；页码、文档 ID、文件大小、文档版本、索引版本、ETL 版本和分页统计使用 `LongValue` 字符串兼容契约，避免 Java Long 在 JavaScript Number 中丢失精度。
- 管理页支持分页文档列表、上传/替换、ETL 任务历史、重试索引、启用、停用、删除、全量 rebuild 和健康状态。上传和任务历史请求可取消；删除、替换、rebuild 等危险操作使用确认与操作 guard，分页删除最后一条后回退到有效末页，组件卸载时取消请求、销毁确认框并清理操作状态。
- 页面只展示管理所需公开字段和脱敏错误码，不展示 OSS object key、签名 URL、lease proof、模型 prompt/reasoning、向量或 rerank 分数、供应商响应正文等敏感内部字段。
- 2026-10-01 当前主 Agent fresh 执行 `node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts tests/customerService.test.ts`，44/44 项通过；`npm run type-check`、`npm run build-only` 和 `git diff --check` 均通过。构建仅报告既有 dynamic-import/chunk warning，没有新增构建错误。该验证未启动真实浏览器、管理员会话、真实文件上传、OSS、MySQL、Python、CloseAI、Milvus 或 GPU。
- Task 13 使客服 RAG 的可选依赖只在功能开关开启后的 lifespan 中导入和装配；关闭态完整 lifespan 不加载 Milvus、CloseAI Embedding、文档 ETL、Grounded RAG 或本地 Reranker。`/health/ready` 继续只反映 checkpoint/代码生成 readiness。认证客服 health 只输出 `answerService/answerModel/embedding/etl/leaseValidator/milvus/reranker` 固定七键，缺键或非严格布尔 true 按 false，额外键忽略；只有七项全部可用时 healthy。同步探针使用 daemon single-flight；Milvus 专用 `health_ready_sync()` 直接调用同步 client，不复用业务 `ping()` 的 cancellation-draining `_thread_call`，超时和 lifespan 退出都不等待 RPC、不占默认 executor、不留 ASGI pending Task。其他 async provider/dependency 按实例与 dependency key 共享 Task，dispose 先分离缓存再有界 cancel/drain，旧 loop 后的新 loop 可恢复；模型、Embedding 和 Reranker 只做无供应商调用的本地生命周期检查，异常/缺失 answer service 同样 fail-safe degraded。
- Task 13 新增 `customer-service-rag-eval/v1` synthetic fixture 与注入 runner 的离线 evaluator。指标包括 Recall@8、MRR@3、NDCG@3、no-answer accuracy、citation validity 和延迟摘要；重复检索 ID 去重，重复/未知 citation 计为无效，空集不除零。latency 的不可转换值、字符串、bool、NaN、Inf 或负数会 fail-closed 抛出只含 entry id 的稳定 `ValueError`，不按 0 计入。fixture 包含中文同义改写、错别字、稳定错误码、无答案和“忽略规则/泄露系统提示”攻击样例，不含真实业务内容或秘密。
- Task 13 自动化验证只使用 fake/injected provider；真实 OSS、CloseAI、Docker Milvus、GPU Reranker、浏览器和完整 E2E 截至 2026-10-01 均未执行，继续保持 pending。客服仍不使用 checkpoint，长期记忆和 `PostgresStore` 仍关闭。
- Task 13 规格复审补强了 answer readiness：回答模型复用 `AI_SERVICE_MODEL_*` 配置的 `OpenAICompatibleModel`，CloseAI 仅用于 Embedding。生产模型、Embedding、Reranker 使用不发外部请求的本地生命周期检查；disabled 测试已在隔离子进程完整进入和退出 lifespan，并注入 fake model/gateway/checkpoint 验证代码生成 ready 不受影响。

### 已关闭的源码阻塞

- Java LangGraph 客户端已固定使用 HTTP/1.1，h2c Upgrade 导致的 HTTP 422 已有回归测试，不再是当前源码阻塞。
- 下游取消会取消未完成 HTTP future、关闭 NDJSON 响应体并终止读取循环。
- LangGraph 响应流支持可配置空闲超时，默认 600 秒；该值按连续未收到完整 NDJSON 行计时，不是整轮总时长。
- 灰度身份键已明确为 `userId -> appId -> requestId`，覆盖应用创建前后、生成和取消的一致性。
- 连接复用、并发大流隔离、超时风暴恢复和重复父子进程回收已有自动化覆盖。
- 超时风暴测试的关闭 latch 已移动到活动流计数清理之后，避免测试线程在最后一个处理器 `finally` 尚未完成时偶发误报连接未释放。

### 接手前先读

1. `AGENTS.md`
2. `ai-service/README.md`
3. `doc/ai-service-startup.md`
4. `doc/ai-service-langchain-langgraph-refactor-design.md`
5. 本文

后端和前端是两个独立 Git 仓库：

- 后端：`D:/VibeForge/yu-ai-code-mother`
- 前端：`D:/VibeForge/yu-ai-code-mother-frontend`

必须分别检查工作区、运行验证和提交，不得把前端既有 `package-lock.json` 修改混入无关提交。

## 2. 系统边界与当前架构

```text
Vue EventSource
  -> GET /api/apps/chat/gen/code
  -> AppController
  -> AppServiceImpl
  -> GenerationLeaseService
  -> DelegatingAiGenerationGateway
     -> LegacyAiGenerationGateway -> LangChain4j
     或
     -> LangGraphAiGenerationGateway
        -> POST Python /internal/v1/generations:stream
        -> LangGraph StateGraph
        -> PostgreSQL AsyncPostgresSaver / ai_workflow_status
        -> OpenAICompatibleModel / DeepSeek
        -> SpringToolGateway
        -> POST Spring /api/internal/ai-tools/invoke
        -> ArtifactPublicationService
        -> VersionedArtifactStore
  -> StreamHandlerExecutor
  -> 前端 SSE
```

边界原则：

- Python 决定模型提示词、工具选择、参数校验、调用顺序、修复和工作流流转。
- Spring 负责鉴权、应用权限、生成租约、幂等、目录沙箱、文件操作、解析、校验、构建、发布、聊天历史和数据库写入。
- Python 不获得项目目录挂载、业务数据库连接或绕过 Spring 的文件权限。
- 内部 Spring/Python 使用 NDJSON；对外 SSE 兼容性由 Java 网关维持。
- `VersionedArtifactStore` 的发布幂等与内部工具 Redis 幂等是两套独立机制，不可混为一谈。
- PostgreSQL checkpoint 只服务 Python 工作流恢复；Spring 业务 MySQL 和 Redis database 1 保持不变。

## 3. 当前已实现能力

### 3.1 LangGraph 工作流

```text
START
  -> input_guard
  -> context_prepare -> Spring artifact_context
  -> HTML: generate_html
     MULTI_FILE: generate_multi_file
     VUE_PROJECT: vue_agent <-> Spring file tools
  -> artifact_validation
  -> VUE_PROJECT: project_build
  -> quality_review
  -> repair（校验、构建或质量失败时最多 2 次）
  -> HTML/MULTI_FILE: artifact_publish
     VUE_PROJECT: finalize
  -> END
```

- 内部事件为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。
- HTML 只接受完整闭合文档或唯一 HTML Markdown 代码块，拒绝解释、多代码块和截断内容。
- MULTI_FILE 要求完整匹配的 `index.html`、`style.css`、`script.js`。
- Vue 首次生成与修复复用五个标准文件工具，并共享 `AI_SERVICE_VUE_MAX_TOOL_CALLS` 总预算。
- `project_build` 返回 `built/errorCode/message`；失败进入有限修复，质量检查只在构建成功后执行。
- 多 Agent 开启时，Vue 首次构建成功以及每次修复后重新构建成功都会通过 `vue_source_snapshot` 让三个 Reviewer 瞬时审查当前真实源码；开关关闭时保持原有仅修复后快照的兼容行为。

### 3.2 Vue 多 Agent 质量审查

- 功能开关 `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED` 默认 `false`，第一阶段只支持 `VUE_PROJECT`，不改变 HTML、MULTI_FILE 或关闭开关时的现有路径。
- 三个角色分别是 requirement、function、technical；三者并发、只读、职责独立，共享 `AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS` 指定的整体超时。
- 三个角色的 `artifact` 均为 Spring 返回的有界真实 snapshot；Reviewer `context` 严格白名单为 `prompt`、`codeGenType`、`validation`、`build`，且为每个角色深拷贝。不会传入 `conversation`、`metadata`、`currentArtifact`、`toolResults`、`appId`、`requestId` 或其他生成期状态；Vue repair 工具循环仍保留 `toolResults`。
- 提示词要求无 Markdown 围栏的纯 JSON。适配器仅为供应商兼容防御性剥离单一完整 JSON 围栏，随后仍执行严格 Pydantic schema、字段边界和 Reviewer 身份校验；围栏外解释或其他附加文本仍属于非法输出。
- 聚合由确定性代码完成。`critical`、`major` 生成最多 4000 字符的阻断性 `repair_feedback` 并进入现有 repair 回环；`minor` 不触发 repair，也不消耗修复次数。
- `repair_feedback` 每个问题固定单独一行，字段依次为 `severity`、`code`、`issue`、`evidence`、`repair`；字段值折叠空白并把内部 `;`、`=` 规范化为全角字符，不添加总标题、Reviewer 或 category。
- repair 总上限仍由 `AI_SERVICE_MAX_REPAIR_ATTEMPTS` 控制，默认最多两次。每次修复后重新执行硬校验、项目构建、真实源码快照和三个 Reviewer。
- 任一 Reviewer 超时、普通模型调用异常、非法结构或快照失败均按 F1 直接进入 `failed`，不回退单 Reviewer、不盲修、不发布候选结果。稳定错误码为 `MULTI_AGENT_REVIEW_TIMEOUT`、`MULTI_AGENT_REVIEW_MODEL_ERROR`、`MULTI_AGENT_REVIEW_INVALID_OUTPUT`、`MULTI_AGENT_REVIEW_SNAPSHOT_ERROR`。直接 `SystemExit`/`KeyboardInterrupt` 会在 Reviewer 边界脱敏，等待兄弟任务清理后重新抛出；非整数 `SystemExit.code` 归一为 `1`。
- 取消检查覆盖快照前、Reviewer 启动前和聚合完成后；`asyncio.CancelledError` 保持取消语义，仍在运行的 Reviewer 任务由任务组收敛。
- 不新增公开 SSE 类型，不改变 Spring 对应用数据、项目文件、构建、发布、聊天历史和对外 SSE 的所有权。
- 业务状态表 `ai_workflow_status` 不保存审查正文。LangGraph 图 checkpoint 只保存最多 4000 字符的阻断性 `repair_feedback`，用于从已完成的 `quality_review` 恢复到 repair 且不重复 Reviewer；minor 详情、Reviewer summary、`reviewer_results`、`quality_issues` 均不保存。
- 成功、失败或取消终态仍 best-effort 清理图 checkpoint，TTL 继续作为异常退出兜底。本轮不启用长期记忆，也不创建或注入 `PostgresStore`。

### 3.3 工具契约与安全

- `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json` 是 Java/Python 共享的版本化工具契约。
- Python 在出站前按请求 Schema 严格校验工具参数；未知工具、未知参数和额外字段不会到达 Spring。
- Spring 成功响应会按响应 Schema 校验；请求拒绝额外字段，响应允许新增字段以支持滚动升级。
- Schema、协议和业务错误使用稳定脱敏信息，不包含源码、绝对路径、参数值或响应正文。
- 内部工具幂等作用域为 `appId + requestId + toolCallId`；工具名或参数指纹冲突会拒绝执行。
- 已存在的 `RUNNING` 和 action 成功但 Redis 完成状态写回失败，都按不确定状态处理，不承诺文件系统与 Redis 严格 exactly-once。
- Python `SpringToolGateway` 会识别 HTTP 200 中非零的 Spring `BaseResponse.code`，不会把业务失败当作成功。

### 3.4 产物发布、并发与取消

- HTML 在发布前执行文档、CSS、JavaScript 完整性校验和 Selenium 烟测。
- HTML/MULTI_FILE 使用不可变 release、manifest、请求墓碑、单调序号和原子活动指针。
- 发布失败、模型截断或页面运行检查失败时，上一活动版本保持不变。
- `GenerationLeaseService` 防止同一应用并发生成。
- 取消和提交使用 `ACTIVE`、`CANCELLED`、`COMMITTING`、`COMMITTED` 状态门仲裁。
- 取消先获胜时禁止发布；提交已经开始后，迟到取消不能反向覆盖成功版本。
- Python 在产物已发布但外围 checkpoint 失败时最多补发一次完成事件，避免文件已发布而前端收到失败。

### 3.5 前端生成体验

- 前端只在普通 JavaScript 状态中累计完整流式进度，Vue 响应式状态仅保留最近 2000 个字符。
- 界面每 80ms 最多刷新一次，自动滚动每 200ms 最多一次。
- 完成后重新读取服务端聊天历史，不能把 2000 字符临时窗口当成最终完整消息。
- 生成期间保留旧预览，仅在当前请求成功后刷新一次；失败、停止、卸载和迟到回调不得刷新。
- 三类“优化提示”均使用简短普通语言，并要求保留原有功能、文字、图片和操作方式。

### 3.6 资源边界

- 活动 HTML/MULTI_FILE 上下文最多 100000 字符；Vue 上下文最多返回 200 个排序后的文件项。
- 修复后 Vue 快照最多 24 个文件、单文件 12000 字符、总计 60000 字符。
- Vue 项目总访问条目最多 20000 个，合格源码候选最多 10000 个，单文件最大 1 MiB。
- 快照排除依赖、构建产物、隐藏目录、符号链接、锁文件、非文本、非法 UTF-8 和 NUL。
- 完整快照只瞬时传给当前 Reviewer，不进入工具幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或事件；只有阻断性审查生成的最多 4000 字符 `repair_feedback` 可进入图 checkpoint。
- npm 输出、路径和环境值有界且脱敏；父子进程树和输出读取使用有界终止策略。
- `scripts/verify-langgraph-real-gate.ps1` 提供统一验证入口：默认 dry-run，显式 `-Execute` 后固定执行 Java clean compile、35 项定向测试、Python compileall/pytest/lock 检查和 PowerShell 脚本检查；`-IncludeRedis` 可追加 Spring 工具幂等真实 Redis 6 项，`-IncludePostgres` 可追加真实 checkpoint 集成测试。
- 统一入口只向 `target/ai-validation/langgraph-real-gate.json` 写入步骤名、命令标签、状态、退出码、耗时和人工待验收项，不收集 Maven 原始日志、源码、令牌、Cookie 或响应正文。
- `scripts/new-ai-validation-record.ps1` 可离线创建版本化人工验收记录，固定覆盖三类型首次生成/二次修改、停止、断线、模型超时、工具失败、长构建、双 Spring 竞争和 Legacy 回滚 13 个 P0 场景。
- 人工记录为 requestId、终态、稳定错误码、旧预览、刷新次数、历史回源和证据引用提供统一字段，初始状态全部为 `pending`，不保存凭据、源码、工具参数或响应正文。

## 4. 关键文件

### Spring Boot

| 仓库相对路径 | 作用 |
| --- | --- |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/AiGenerationGateway.java` | 统一生成契约 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java` | Legacy、LangGraph 和灰度路由 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGateway.java` | Python NDJSON 事件适配、HTTP 生命周期与空闲超时 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/GenerationLeaseService.java` | 应用级租约与取消/提交仲裁 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyService.java` | Redis 工具幂等和不确定态保护 |
| `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java` | 内部文件、校验、发布和构建工具边界 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java` | 解析、校验、烟测和发布编排 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/VersionedArtifactStore.java` | 不可变版本、墓碑和活动指针 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java` | 修复后 Vue 最终源码有界快照 |
| `src/main/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilder.java` | 强制构建、进程治理和错误脱敏 |
| `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java` | 生成入口、租约、历史和 SSE 生命周期 |
| `scripts/verify-langgraph-real-gate.ps1` | 统一执行自动化验收门并输出脱敏 JSON 摘要 |
| `scripts/new-ai-validation-record.ps1` | 创建包含 13 个 P0 场景的脱敏人工验收记录 |
| `scripts/ai-validation-scripts.tests.ps1` | 验收脚本的静态安全与参数契约检查 |

### Python AI 服务

| 仓库相对路径 | 作用 |
| --- | --- |
| `ai-service/src/ai_service/app.py` | FastAPI 应用工厂和依赖组装 |
| `ai-service/src/ai_service/api/routes.py` | 健康、流式生成、取消及认证客服知识 INDEX/DELETE/health 接口 |
| `ai-service/src/ai_service/api/schemas.py` | 内部请求、响应和事件模型 |
| `ai-service/src/ai_service/orchestration/workflow.py` | LangGraph 工作流、修复、构建和终态 |
| `ai-service/src/ai_service/orchestration/cancellation.py` | 单进程协作式取消 |
| `ai-service/src/ai_service/models/openai_compatible.py` | OpenAI 兼容模型与 Vue 工具轮次解析 |
| `ai-service/src/ai_service/models/quality_review.py` | Reviewer、问题严重级别和严格结构化审查契约 |
| `ai-service/src/ai_service/models/tool_contract.py` | 模型工具提示和出站校验 |
| `ai-service/src/ai_service/orchestration/multi_agent_review.py` | 三角色并发、整体超时、F1 异常和确定性聚合 |
| `ai-service/src/ai_service/prompts/` | 路由、生成、审查和修复提示词 |
| `ai-service/src/ai_service/infrastructure/checkpoint.py` | checkpoint 协议与禁用实现 |
| `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py` | 官方 PostgreSQL saver、脱敏状态摘要和 TTL 清理 |
| `ai-service/src/ai_service/infrastructure/checkpoint_setup.py` | 独立 schema 初始化命令 |
| `ai-service/src/ai_service/infrastructure/spring_tools.py` | Spring 工具客户端与脱敏错误处理 |
| `ai-service/src/ai_service/infrastructure/knowledge_download.py` | 客服知识文档安全下载、逐跳校验、摘要与临时文件生命周期 |
| `ai-service/src/ai_service/infrastructure/spring_knowledge_lease.py` | Spring mutation lease 验证与只读健康检查的 fail-closed adapter |
| `ai-service/src/ai_service/infrastructure/milvus_knowledge.py` | versioned Milvus store、lease fencing 边界与取消清理 |
| `ai-service/src/ai_service/models/embeddings.py` | CloseAI Embedding provider、独立 HTTP clients 与元素预算 |
| `ai-service/src/ai_service/orchestration/document_etl.py` | 文档解析切分及 download/embed/store 完整 ETL 编排 |

### Vue 前端独立仓库

| 前端仓库相对路径 | 作用 |
| --- | --- |
| `src/pages/AppChatView.vue` | 生成对话、停止、预览和终态回源 |
| `src/api/app.ts` | SSE 与 `business-error` 事件适配 |
| `src/utils/generationStreamProgress.ts` | 80ms 刷新和 2000 字符尾部窗口 |
| `src/utils/previewRefreshCoordinator.ts` | 当前成功请求只刷新一次预览 |
| `src/utils/optimizePrompt.ts` | 三类简短优化提示 |
| `src/views/customer-service/CustomerServiceView.vue` | 登录客服单轮问答、取消和最多 3 条来源展示 |
| `src/api/customerService.ts` | Spring 客服问答公开请求与响应类型 |
| `src/utils/customerService.ts` | 问题与文件边界、LongValue、来源规范化、latest-wins 和请求生命周期治理 |
| `src/views/admin/CustomerServiceKnowledgeView.vue` | 管理员知识文档、ETL 任务、健康状态和危险操作页面 |
| `tests/customerService.test.ts` | 客服与知识管理纯函数、分页、取消、迟到响应和卸载契约测试 |

## 5. 配置与本地服务

默认地址：

```text
Spring: http://localhost:8123/api
Python: http://localhost:8000
Vue: http://localhost:5173
Spring Redis: redis://localhost:6379/1
Python checkpoint PostgreSQL: postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint
```

Python 已删除 `AI_SERVICE_REDIS_*` checkpoint 配置，改用 `AI_SERVICE_CHECKPOINT_ENABLED`、
`AI_SERVICE_CHECKPOINT_REQUIRED`、`AI_SERVICE_CHECKPOINT_POSTGRES_URL`、`AI_SERVICE_CHECKPOINT_AUTO_SETUP`、
TTL 和连接池边界配置。Spring 工具幂等继续使用 Redis database 1，不属于本次迁移范围。

仓库不管理本机已有 PostgreSQL Docker 容器，也不保存角色密码。准备数据库后运行：

```powershell
cd ai-service
uv run python -m ai_service.infrastructure.checkpoint_setup
```

本地可使用 `AI_SERVICE_CHECKPOINT_AUTO_SETUP=true`；生产推荐独立初始化后关闭自动 DDL。序列化保持 pickle fallback 关闭，并通过 `allowed_json_modules=()` 不额外允许自定义 JSON constructor 模块；锁定版本不存在 `LANGGRAPH_STRICT_MSGPACK` 开关，数据库写权限必须只授予可信 AI 服务。

当前两个调用方向仍共用静态令牌，本地联调时以下值必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN
```

不得把真实令牌、模型密钥、账号、密码、Cookie、验证码、OSS 密钥或邮件授权码写入仓库、测试报告或交接文档。生产环境应通过密钥管理或运行时环境变量注入并定期轮换。

关键配置：

- `AI_ENGINE=legacy|langgraph|gray|auto`
- `AI_SERVICE_URL=http://localhost:8000`
- `AI_GENERATION_STREAM_IDLE_TIMEOUT_SECONDS=600`
- `AI_SERVICE_VUE_MAX_TOOL_CALLS=<有限正整数>`
- `AI_SERVICE_MAX_REPAIR_ATTEMPTS=2`
- `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false`：默认关闭，只影响 Vue。
- `AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS=60`：三个 Reviewer 共享的整体超时。
- `AI_REDIS_INTEGRATION=true`：只在明确运行真实 Redis 集成测试时设置。
- `AI_SERVICE_POSTGRES_INTEGRATION=true`：只在独立 `yu_ai_checkpoint` 已初始化且明确运行真实 checkpoint 集成测试时设置。

本地真实验收前先确认服务和配置，不要把“端口当前可达”“当前终端有令牌”等临时现场写成长期事实。

统一自动化门禁：

```powershell
# 仅显示计划，不执行构建或测试
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1

# 执行 Java、Python 和 PowerShell 自动化门禁
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute

# Redis database 1 可用于隔离验收时，额外执行真实 Redis 6 项
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute -IncludeRedis

# 独立 PostgreSQL checkpoint 数据库已初始化时，额外执行真实集成测试
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute -IncludePostgres
```

创建人工验收记录：

```powershell
# 仅查看说明，不写文件
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/new-ai-validation-record.ps1

# 创建 target/ai-validation/manual-validation-record.json
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/new-ai-validation-record.ps1 -Execute
```

人工执行每个场景后只更新对应记录的 `status`、`requestId`、`terminalStatus`、`errorCode`、`durationMs`、`oldPreviewPreserved`、`previewRefreshCount`、`historyReloaded`、`evidenceRefs` 和简短脱敏备注。不要把日志正文、源码、提示词或认证信息复制进记录。

## 6. 当前自动化验证证据

下表优先记录当前分支的最新 fresh 结果；未在本轮重新执行的历史门禁显式标明，不能替代真实外部依赖验收。

| 范围 | 命令 | 最新证据 | 能证明什么 |
| --- | --- | --- | --- |
| Java 定向门禁 | `mvn "-Dtest=LangGraphAiGenerationGatewayTest,DelegatingAiGenerationGatewayTest,AppServiceGenerationCancellationTest,VueProjectBuilderTest" test` | 35 项通过 | HTTP 生命周期、灰度、取消、长构建治理 |
| Java 网关 | `mvn "-Dtest=LangGraphAiGenerationGatewayTest" test` | 10 项通过 | HTTP/1.1、连接复用、大流隔离、空闲超时与恢复 |
| Vue 构建器 | `mvn "-Dtest=VueProjectBuilderTest" test` | 16 项通过 | 构建错误边界、输出限制、父子进程回收 |
| Java 生产编译 | `mvn clean -DskipTests compile` | 2026-10-01 Task 13 fresh：263 个生产源文件编译成功 | 当前生产源码可干净编译 |
| Java 客服登录 API 定向门禁 | `mvn "-Dtest=CustomerServiceAnswerServiceTest,CustomerServiceControllerTest,CustomerServiceAiClientTest" test` | 2026-10-01 Task 13 fresh：20 项通过 | 登录路由、请求边界、requestId 关联、degraded/unknown fields/source 严格校验、公开 VO 脱敏与 HTTP 客户端边界 |
| Redis opt-in 集成 | `powershell -NoProfile -File scripts/verify-langgraph-real-gate.ps1 -Execute -IncludeRedis` | 最近真实环境 6 项通过 | 跨客户端回放、唯一执行、冲突与不确定态 |
| PowerShell 脚本 | `powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1` | Windows PowerShell 5.1 与 PowerShell 7 检查通过 | 验收脚本参数、认证和脱敏约束 |
| 统一非外部数据库门禁 | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute` | 本轮未重新执行；此前历史基线为 6 个步骤通过 | 不能作为本轮新增多 Agent 测试数量的 fresh 证据 |
| Python 编译 | `uv run python -m compileall -q src` | 2026-10-01 Task 13 fresh：退出码 0，无输出 | 当前 Python 源码可完成字节码编译 |
| Python 全量 | `uv run pytest` | 2026-10-01 Task 13 规格修复 fresh：605 项通过、1 项跳过、2 个依赖弃用警告 | 既有生成链路与客服 ETL/RAG，加上完整 lifespan 关闭态隔离、同步/异步 single-flight 健康探针、Milvus 专用同步探针与硬超时、跨 loop 恢复、固定七键摘要、版本化评估集和严格 latency 契约 |
| Python Task 13 focused | `uv run pytest tests/test_customer_service_health.py tests/test_api.py tests/test_milvus_knowledge.py -q` | 2026-10-01 规格修复 fresh：221 项通过、2 个依赖弃用警告 | 关闭态完整 lifespan 零 RAG 导入、2 秒阻塞 Milvus fake client、退出时限、同步/异步 timeout/busy/recovery、默认 executor 隔离、跨 loop dispose、固定七项依赖、严格 latency 和鉴权 |
| Python 客服回答契约 | `uv run pytest -q tests/test_api.py -k customer_service_answer` | 2026-10-01 Task 10 fresh：5 项通过 | Python 内部问答请求、requestId 回传、字段边界、脱敏错误和 disabled/degraded 契约 |
| Vue 客服、知识管理与既有纯函数测试 | `node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts tests/customerService.test.ts` | 2026-10-01 Task 12 fresh：44/44 项通过 | 单轮问答，以及知识文件边界、LongValue、latest-wins、分页回退、请求取消、危险操作和卸载清理契约 |
| Vue 类型检查 | `npm run type-check` | 2026-10-01 Task 12 fresh：退出码 0 | 客服页面、知识管理路由、API 与工具类型一致 |
| Vue 生产构建 | `npm run build-only` | 2026-10-01 Task 12 fresh：退出码 0；仅有既有 dynamic-import/chunk warning | 前端生产 bundle 可构建，不代表真实浏览器或后端联调通过 |
| Vue 差异检查 | `git diff --check` | 2026-10-01 Task 12 fresh：退出码 0 | 前端分支差异没有尾随空格等补丁错误 |
| Python 锁文件 | `uv lock --check` | 2026-10-01 Task 13 fresh：退出码 0，解析 150 个包 | `uv.lock` 与项目依赖声明一致 |
| 文档空白检查 | `git diff --check` | 2026-10-01 Task 13 fresh：退出码 0，仅有 Git 的 LF/CRLF 工作树提示 | 本轮差异没有尾随空格等补丁错误 |
| 当前工作树 | `git status --short` | 当前隔离分支工作树干净，最终提交后命令无输出 | 不把其他工作树或前端仓库状态混入本分支结论 |
| 实现差异范围 | `git diff --stat b5c665d..HEAD` | 20 个文件，2510 行新增、57 行删除，覆盖 Python 生产代码、测试、配置和文档 | Vue 多 Agent 实施并非仅文档修改；范围以设计基线至当前 HEAD 的真实 Git 差异为准 |
| PostgreSQL 默认门 | `uv run pytest tests/test_postgres_checkpoint_integration.py` | 默认 1 项跳过，不连接数据库 | opt-in 门禁不会误连本机数据库 |

重要限制：

- 全量 `mvn test` 存在历史实验代码和外部依赖相关失败，当前不能声明全量 Java 测试通过。
- Task 13 初始实现的一次中间运行在 Windows spawn 压力下触发既有 reranker 固定 `sleep(0.03)` 时序断言；该单测独立复跑通过，Milvus 专用同步健康探针复审修复后的最终完整 `uv run pytest` 605 项通过、1 项跳过。未为掩盖抖动修改业务实现或该测试同步策略。
- 本轮统一入口未使用 `-IncludeRedis`；表中的真实 Redis 6 项来自最近一次独立真实环境验证，不冒充本轮 fresh 结果。
- 本机 PostgreSQL Docker 容器存在，但独立数据库 `yu_ai_checkpoint` 尚未创建；本轮没有运行 `-IncludePostgres`，不得声称真实 PostgreSQL 集成通过。
- Python 大部分测试使用 Fake Model、内存网关或 MockTransport，不能替代真实模型、真实 Spring 和完整前端验收。
- pytest 的 2 个 warning 分别来自 Starlette `anyio.abc.BlockingPortal` 别名弃用和 LangGraph `allowed_objects` 默认值将变更；本轮没有把依赖 warning 写成测试失败，也没有扩大范围修改依赖。
- 受控本地 HTTP/进程测试不能证明真实 Uvicorn、代理、供应商限流、网络背压或 npm 包装层在所有平台上的行为。
- 未实际运行的 Docker、真实模型、浏览器端到端和生产灰度，必须明确标记为未验证。

## 7. 当前人工与真实环境验收门

用户此前确认的历史 P0 场景不覆盖 Vue 多 Agent。本轮没有执行真实模型、浏览器或多 Agent 人工验收，因此相关项目全部保持待验证，不补造 requestId、截图或通过证据。

### 7.1 状态总表

| 优先级 | 验收项 | 当前状态 | 通过标准 |
| --- | --- | --- | --- |
| P0 | 三类型首次生成和二次修改 | 用户确认已完成，本轮未复测 | 三个隔离应用均完成，历史、完整内容、图片、交互、构建、发布和预览正确 |
| P0 | 停止、断线和失败终态 | 用户确认已完成，本轮未复测 | 取消不发布新版本，失败保留旧预览，迟到回调不刷新，终态唯一 |
| P0 | 双 Spring 工具竞争 | 用户确认已完成，本轮未复测 | 同一作用域只执行一次，另一实例回放同一成功结果，新作用域探针保持独立 |
| P0 | 双引擎摘要与 Legacy 回滚 | 用户确认已完成，本轮未复测 | 同一主体路由稳定，可比较摘要，可切回 Legacy 且取消语义一致 |
| P0 | 真实 PostgreSQL checkpoint | 待人工执行 | 本机 Docker 中独立 `yu_ai_checkpoint` 初始化成功，opt-in 集成测试通过，`/health/ready` 正常，并覆盖 optional、required、disabled 三种模式 |
| P0 | Vue 三 Reviewer 真实模型链路 | 待人工执行 | 首次生成、major 修复、取消、超时、非法输出和错误凭据均符合 F1/S1 约定，候选发布与旧预览语义正确 |
| P0 | Spring/Python/Vue 完整 SSE 端到端 | 待人工执行 | 真实模型和真实工具网关下事件顺序、唯一终态、历史回源、单次预览刷新及失败保留旧版本均正确 |
| P1 | 多 Agent 成本与质量对比 | 待人工执行 | 对比开关关闭/开启后的延迟、token、repair 次数、成功率和多轮需求回归识别质量，形成是否灰度开启的证据 |
| P1 | 真实 Uvicorn/代理压力 | 待执行 | 无连接泄漏，慢流/背压可控，超时后线程与连接收敛 |
| P1 | 真实 npm 长构建压力 | 待执行 | 取消/超时后无可复现残留 PID 或管道阻塞 |

### 7.2 三类型生成

为 HTML、MULTI_FILE、VUE_PROJECT 分别创建隔离测试应用，每类至少执行：

1. 首次生成完整应用。
2. 保留原功能、文字、图片和操作方式的二次修改。
3. 检查聊天历史回源内容完整，不以 2000 字符临时窗口代替最终消息。
4. 检查成功后预览只刷新一次，失败或取消不刷新。
5. 检查 HTML 严格解析与烟测、MULTI_FILE 三文件、Vue 工具循环与构建结果。
6. 检查新版本发布后活动指针更新，失败时旧活动版本仍可预览。

### 7.3 停止、断线和失败

覆盖以下场景并同时观察浏览器、Spring、Python、PostgreSQL checkpoint 和 Spring Redis 状态：

- 生成过程中点击停止。
- 客户端主动断开 SSE。
- 模型连接超时或响应流长期无完整 NDJSON 行。
- Spring 工具返回业务失败。
- Vue 构建长时间运行后取消或超时。
- 成功、失败或停止后的迟到回调。

报告只记录 requestId、appId、生成类型、引擎、终态、稳定错误码、耗时和是否保留旧预览。不得保存完整源码、提示词、工具参数、令牌、Cookie、账号密码或绝对临时路径。

### 7.4 双 Spring 工具竞争

使用 `scripts/test-ai-tool-controller-competition.ps1` 对两个 Spring 实例发起相同 `appId + requestId + toolCallId` 的并发调用。验收要求：

- 使用随机隔离临时文件，不触碰 `projects/` 中用户产物。
- 两个实例共享同一 Redis database 1。
- 首次竞争只有一个真实副作用，另一个实例得到相同成功结果或受控不确定态。
- 再次调用相同作用域回放结果；新 requestId/toolCallId 不得误命中旧结果。
- 报告脱敏，不记录 Bearer 令牌、Cookie、临时绝对路径或响应正文。

### 7.5 双引擎对比与回滚

使用 `scripts/compare-ai-generation-engines.ps1` 比较 Legacy/LangGraph 的摘要和稳定路由，不保存完整生成源码。提高灰度比例前必须证明：

- 相同 userId 在应用创建、持久化后生成和取消阶段选择同一引擎。
- Legacy 和 LangGraph 均可产生可发布结果或稳定失败终态。
- 切回 `legacy` 后生成、取消、聊天历史和旧预览语义正常。
- 灰度配置变更作为独立提交，不能和功能修复混在一起。

### 7.6 统一人工验收记录

运行 `scripts/new-ai-validation-record.ps1 -Execute` 创建本轮记录。13 个场景必须逐项从 `pending` 更新为以下状态之一：

- `passed`：通过标准有直接证据。
- `failed`：已执行且行为不符合通过标准，应记录稳定错误码和最小复现信息。
- `blocked`：缺少环境、权限或外部依赖，应记录直接阻塞原因。
- `not-run`：本轮明确不执行，不能计入通过率。

证据优先引用 `target/ai-validation/` 下的脱敏 JSON 文件、requestId 和稳定错误码。浏览器截图或后台日志如果包含账号、源码、Cookie、令牌、绝对路径或完整请求正文，必须先脱敏且不得提交到 Git。

### 7.7 Vue 多 Agent 人工待验证

以下项目本轮均未执行，不能写为通过：

1. 本机 Docker PostgreSQL 创建并初始化独立 `yu_ai_checkpoint`，确认 checkpoint 与 `/health/ready` 正常可用。
2. 同时启动 Spring、Python AI 服务和 Vue 前端，配置一致的内部令牌并显式开启多 Agent 开关。
3. 使用真实模型完成 Vue 首次生成，确认首次构建后读取真实源码 snapshot 并执行三个 Reviewer。
4. 制造明确的 major 问题，确认 repair 仅针对结构化阻断反馈修复，保留未提及的原功能；修复后重新构建、重新取 snapshot、重新审查，且总 repair 不超过两次。
5. 在审查期间停止生成，确认取消传播到三个 Reviewer，终态为 cancelled，候选版本不发布且旧预览不刷新。
6. 分别使用错误模型凭据、整体超时和非法模型输出，确认稳定错误码为 `MULTI_AGENT_REVIEW_MODEL_ERROR`、`MULTI_AGENT_REVIEW_TIMEOUT` 或 `MULTI_AGENT_REVIEW_INVALID_OUTPUT`，且不泄露供应商响应、凭据或源码。
7. 对比开关关闭/开启后的端到端延迟、模型 token 消耗、repair 次数和成功率，评估测试环境灰度成本。
8. 使用包含历史修改要求的多轮 Vue 会话，确认 Reviewer 只接收当前 prompt 的既定边界不会造成不可接受的需求回归漏检；若发现问题，先评估由 Spring 提供脱敏、结构化需求摘要，而不是直接放开 conversation。
9. 在真实 Spring 工具网关下验证 `vue_source_snapshot` 的文件数量、单文件大小、总字符数和遍历上限，确认超限时稳定失败且不泄露源码或绝对路径。
10. 完成 Spring -> Python NDJSON -> Spring SSE -> Vue 的完整链路验收，确认多 Agent 期间的 `node_status`、唯一终态、聊天历史回源和成功后单次预览刷新行为。

### 7.8 Grounded RAG 人工待验证

Task 9 的协议、鉴权、快照、引用白名单、资源边界和 cleanup 安全语义已有自动化覆盖，但以下项目仍需真实环境验收，不能写为通过：

1. 使用真实客服评估集校准 Top 3 rerank threshold；disabled 模式不得配置阈值，并记录拒答率、引用准确率和答案质量。
2. 使用真实 Embedding、Reranker、Milvus 和 Spring Task 7 bridge，验证物理 snapshot、manifest scalar + point-get、跨模型/维度迁移、空 rebuild 和幂等 rebuild。
3. 在真实客户端断连、模型超时和供应商错误下，确认 answer deadline、disconnect drain、稳定错误码和资源收敛，不泄露 prompt、引用正文、令牌或供应商响应。
4. 使用两个或更多 Spring/Python 实例验证 cleanup marker backlog、scan cursor、ownership 校验和 mutation permit fence 的跨实例行为；确认旧 namespace 可清理、current/protected collection 不被删除。
5. 完成真实 Spring -> Python -> 客户端的问答 E2E，确认鉴权、检索、Top 3 回答、严格 JSON、引用白名单和失败终态一致。

人工验收完成后，应在脱敏记录中填写执行日期、环境版本、requestId、终态、稳定错误码、耗时和证据引用；不得只把本节复选项改成“已完成”而缺少可追溯证据。

### 7.9 Spring 登录客服 API 人工待验证

Task 10 只完成了自动化契约验证，本轮没有启动真实 Spring、Python、CloseAI、Milvus、OSS、GPU Reranker 或浏览器环境。以下项目仍需人工执行，不能写为通过：

1. 启动真实 Spring 和 Python 服务，使用已登录会话调用 `POST /api/customer-service/ask`，确认成功响应遵循统一 `BaseResponse<CustomerServiceAnswerVO>`，匿名请求返回 `code=40100`。
2. 在本机 Docker Milvus、真实 CloseAI Embedding、`AI_SERVICE_MODEL_*` OpenAI 兼容回答模型、真实 OSS 文档和 RTX 4050 GPU Reranker 同时可用时，完成上传、索引、提问、Top 3 来源展示的完整链路，并核对引用确实来自当前活动文档版本。
3. 人工构造 Python `requestId` 不匹配、`degraded=true`、未知字段、重复 source、缺失 locator、过期文档版本和超限 chunk ID，确认 Spring 全部 fail-closed，公开响应不泄露分数、prompt、reasoning、内部错误正文或供应商响应。
4. 使用两个登录用户分别验证 USER 限流隔离，同一用户在 60 秒窗口内超过 10 次时被限流，另一用户不受其计数影响；确认日志和响应不包含问题正文、凭据或签名 URL。
5. 使用中文、emoji、空白、4000 字符边界和 UTF-8 超限问题验证请求限制；同时覆盖 Python 超时、非 2xx、非法 JSON、响应体超限和客户端断开，确认稳定错误语义与资源收敛。
6. 在真实浏览器完成已实现客服页面的登录、提问、拒答、错误、重试和来源展示验收；当前只有 Node 纯函数、类型检查和构建证据，尚无浏览器 E2E 证据。

### 7.10 Vue 客服页面人工待验证

Task 11 已完成源码和自动化门禁，但本轮没有启动真实浏览器、登录会话、Spring、Python、CloseAI、Milvus、OSS 或 GPU。以下项目仍需人工执行，不能写为通过：

1. 使用未登录浏览器访问 `/customer-service`，确认路由守卫按现有登录策略处理，导航不显示“智能客服”；登录后确认导航出现并能进入页面，刷新后会话状态正确恢复。
2. 连接真实 Spring/Python 服务，提交普通问题、无依据问题和后端错误，分别确认回答、拒答和服务不可用状态；响应只展示公开字段，最多 3 条来源且 locator、excerpt 和文档名符合预期。
3. 在请求进行中点击取消并立即离开页面，确认 `AbortController` 中止请求，迟到成功或失败响应都不更新已卸载页面，也不出现控制台未处理异常。
4. 验证空白、前后空格、中文、emoji、恰好/超过 4000 UTF-16 code units 和恰好/超过 16000 UTF-8 bytes 的输入边界，确认前后端拒绝语义一致且不会重复提交。
5. 使用真实 OSS 文档、CloseAI、Docker Milvus 和 RTX 4050 GPU Reranker 完成上传、索引、问答、来源展示全链路，确认页面引用属于当前活动文档版本。
6. 确认刷新或重新进入页面后不保留历史问答，本轮没有流式 token 展示，也没有跨请求长期记忆；这些是首期明确边界，不应误报为缺陷。

### 7.11 Vue 知识库管理页面人工待验证

Task 12 已完成源码和自动化门禁，但本轮没有启动真实浏览器、管理员会话、真实文件上传、OSS、MySQL、Python、CloseAI、Milvus 或 GPU。以下项目仍需人工执行，不能写为通过：

1. 使用普通登录用户和管理员分别访问 `/admin/customer-service/knowledge`，确认普通用户无法进入或看到管理导航，管理员能够进入且刷新后权限状态正确恢复。
2. 分别上传合法 PDF、DOCX、MD、TXT，验证浏览器只向 Spring 发送 multipart 请求，Spring 再写入私有 OSS；验证 `.markdown`、其他扩展名、空文件和超过 20 MiB 文件在前后端边界被拒绝。
3. 使用超过 JavaScript 安全整数范围的文档 ID、版本、文件大小和分页统计，确认 JSON string `LongValue` 在列表、操作请求、任务历史和页码展示中不被取整或截断。
4. 快速切换页码、连续刷新健康状态、取消上传和关闭任务历史，确认 latest-wins、`AbortController`、迟到响应隔离和 loading 状态正确；删除末页最后一条后应回退到新的有效末页。
5. 对上传替换、重试索引、启用、停用、删除和全量 rebuild 逐项执行成功、业务失败和网络失败场景，确认危险确认框、并发操作 guard、错误提示和组件卸载清理正确，不出现重复提交。
6. 启动真实 MySQL、OSS、Python、CloseAI、Docker Milvus 和 RTX 4050 GPU Reranker，验证 ETL 任务状态与历史、健康状态、失败重试、启停后的检索范围、删除清理和 rebuild 全链路。
7. 检查浏览器网络面板、页面和控制台，确认不展示或记录 OSS object key、签名 URL、lease proof、prompt/reasoning、向量/rerank 分数、供应商响应正文或认证凭据。

### 7.12 Vue 多 Agent 功能回滚

Vue 多 Agent 是 Python LangGraph 内部的功能开关，回滚时保持 LangGraph 引擎不变，按以下顺序执行：

1. 在所有 Python AI 服务部署环境设置 `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false`。
2. 逐实例停止接收新请求、等待或终止在途请求后，滚动重启 Python AI 服务；不要同时重启 Spring 或前端，以缩小回滚影响面。
3. 每个 Python 实例重启后检查 `/health/ready`，确认服务和 checkpoint 依赖就绪，再继续下一实例。
4. 使用隔离的 Vue 生成请求确认恢复现有单 Reviewer 路径，不再执行 requirement、function、technical 三角色审查。

该功能回滚无需修改 Spring、Vue 前端、业务数据库 schema、PostgreSQL checkpoint schema，也无需回退或清理既有 checkpoint 数据。`AI_ENGINE=legacy` 或调整 Spring 灰度配置属于更上层的引擎回滚，用于整个 LangGraph 链路异常；它与仅关闭 Vue 多 Agent 的功能回滚是两套独立手段，不应混用或同时变更。

## 8. 未完成优化清单

### P0：激活并验证 PostgreSQL checkpoint 运行环境

代码重构、配置迁移、初始化命令、optional/required/disabled 生命周期和 opt-in 集成测试已经实现。剩余人工环境步骤：

1. 在本机现有 PostgreSQL Docker 容器中创建独立角色和数据库 `yu_ai_checkpoint`，密码不进入仓库或命令输出。
2. 运行 `uv run python -m ai_service.infrastructure.checkpoint_setup`，确认官方表和 `ai_workflow_status` 初始化成功。
3. 设置 `AI_SERVICE_POSTGRES_INTEGRATION=true` 和当前进程连接 URL，运行真实集成测试；只记录通过/失败和脱敏原因。
4. 启动 Python 服务检查 `/health/ready`，再覆盖 optional、required 和 disabled 三种部署模式。
5. 验证终态删除图 checkpoint，强制过期时先删图、成功后再删状态；图删除失败必须保留状态行供下次重试。

详细设计见 `docs/superpowers/specs/2026-09-29-postgres-checkpoint-design.md`。

### 长期记忆决策

- 本轮不启用 `PostgresStore`，不保存跨 thread 用户偏好或应用事实。
- Spring 已提供聊天历史，当前没有必须由长期记忆解决的明确业务缺口。
- 只有出现跨应用稳定偏好、跨 requestId 结构化架构决策，且已定义查看、修改、删除和过期机制时才重新评估。
- 未来即使启用长期记忆，也必须使用独立 Store namespace，不得读取 checkpoint 表模拟记忆，也不得保存完整源码。

### P1：提高验收可复现性与资源证据

1. 在真实 Uvicorn/代理环境观测连接复用、慢流、网络背压、空闲超时和虚拟线程收敛。
2. 对真实 npm/Shell 包装层执行重复长构建、取消与超时压力；只有复现残留进程时才引入 Windows Job Object 或 Unix process group。

### P2：真实运行稳定后再做

1. 拆分 Python 入口令牌和 Spring 工具网关令牌，并设计新旧令牌并存的轮换窗口。
2. 增加 requestId、appId、userId、engine、node、tool、status、节点耗时、模型耗时和构建耗时的结构化观测。
3. 如接入 LangSmith，保持旁路、采样和源码脱敏，观测故障不得改变业务终态。
4. 只有单工作流真实验收、指标和回滚稳定后，才评估 CloseAI 单模型多 Agent。
5. 灰度稳定期结束且完成回滚演练后，才评估删除 Legacy 和未接入主链路的 Java LangGraph4j 实验目录。
6. 检查 Git 历史和部署配置中的 AI、OSS、邮件等历史凭据并执行轮换。

### 明确不做

- 不把文件、数据库、构建或发布迁移到 Python。
- 当前单 worker、单实例 Python 部署下，不实现跨进程共享取消；扩容到多 worker/实例时重新评估。
- 业务约束未变化前，不额外实现同账号同应用的并发修改仲裁。
- PostgreSQL checkpoint 和 Vue 多 Agent 真实运行验证、稳定观察完成前，不删除 Legacy，也不默认开启多 Agent。

## 9. 最近关键提交

| 提交 | 内容 |
| --- | --- |
| `763240d` | 为一般 async provider/dependency 增加共享健康 Task 与有界 lifespan 清理 |
| `e36c106` | 限制同步健康探针 single-flight、固定七键摘要并严格校验 evaluator latency |
| `912346f` | 补齐 answer readiness、七项必需依赖、稳定 reason 和完整 lifespan disabled 验证 |
| `4291790` | 增加客服健康隔离、版本化离线评估集和运行文档 |
| `73885b8` | 拒绝客服回答中的失效来源和超限 chunk ID，补齐 Task 10 严格来源契约 |
| `e61e5a8` | 加强客服登录 API 的 requestId、重复来源与匿名响应契约测试 |
| `fa42253` | 覆盖客服登录 HTTP 路由 |
| `5ef8611` | 固化 Python 客服回答请求与响应契约 |
| `ba178af` | 验证 Spring 客服 HTTP 客户端契约 |
| `f9a377b` | 加固 requestId、degraded、未知字段和公开 VO 脱敏边界 |
| `bf736f5` | 提供 Spring 登录用户客服问答入口 |
| `8aecf2d` | 排除 active physical target，不把 current 计入 retention rollback window |
| `4915fb2` | 校验 retention protected ownership、aliasHash、RPC 不确定性和 cleanup budget |
| `d77210c` | 以 aliasHash 标记并按 marker backlog/cursor 跨 namespace 分页清理 RAG collections |
| `1f12d39` | 加固 retirement cleanup permit fence、stale marker 和 fail-closed 删除语义 |
| `d077c4c` | 实现 Grounded RAG 客服问答、快照检索、严格回答契约和资源边界 |
| `70b3f68` | 将 INDEX ETL 默认并发收敛为 1 并记录 Python 内存预算 |
| `e3d9bc3` | 限制 Spring 响应与 embedding 元素峰值 |
| `b7e9ce5` | 完成 Embedding HTTP clients 独立清理 |
| `34633c9` | 补齐 Embedding client 生命周期和设计约束 |
| `4d9e56a` | 加固客服 ETL lease、健康检查和装配生命周期 |
| `172763a` | 提供认证客服知识 ETL API |
| `e5e7b8d` | 隔离知识文档重定向连接、保持表格顺序并在线程中执行 ETL |
| `4e7d4a6` | 修正标题层级、DNS 超时、表格抽取和 chunk 上限 |
| `51dac9d` | 增加知识文档安全下载、解析与切分 |
| `a0be99a` | 避免将 PDF 正文中的 `/Encrypt` 误判为加密 |
| `312b9f5` | 根据 DOCX 内容类型与关系文件拒绝改名宏部件 |
| `89d1487` | 增加私有 OSS 知识文档操作与文件策略 |
| `e1abf75` | 限制多 Agent Reviewer 输入上下文 |
| `47662be` | 加固审查上下文、反馈格式与致命异常处理 |
| `928a973` | 覆盖多 Agent 审查取消与导入 |
| `42dd584` | 收敛质量审查 checkpoint 状态 |
| `b1360ca` | 接入 Vue 多 Agent 质量审查 |
| `12f7244` | 隔离多 Agent 配置环境 |
| `fef8771` | 增加多 Agent 审查配置 |
| `3b8bc74` | 稳定审查超时竞争语义 |
| `ec9334e` | 保留审查取消与致命异常语义 |
| `0d449f1` | 完善审查任务组异常清理 |
| `3fc43f4` | 并发执行多 Agent 质量审查 |
| `28d386a` | 脱敏审查解析异常链 |
| `9c2fdf9` | 增加多角色质量审查模型接口 |
| `497578b` | 稳定质量审查聚合边界 |
| `67b5e68` | 收紧质量审查数据校验 |
| `9c54244` | 定义多 Agent 质量审查契约 |
| `5eab7d9` | 打通 LangGraph 真实环境验收门基础能力 |
| `ffa72de` | 明确灰度身份键契约 |
| `f6e6f1a` | 取消时释放 LangGraph 响应流 |
| `cae015b` | 覆盖并发大流请求隔离 |
| `2a14a39` | 限制 LangGraph 空闲响应流 |
| `9358f18` | 增加双 Spring 工具竞争验收 |
| `012f93b` | 覆盖 HTTP 连接复用与超时恢复 |
| `2c91f21` | 压测长构建进程树回收 |
| `553a8ca` | 迁移 AI checkpoint 到 PostgreSQL |
| `ad4bbe9` | 增加 PostgreSQL checkpoint opt-in 集成测试 |

Vue 多 Agent 已于 2026-09-30 本地快进合并到 `dev`，合并后 HEAD 为 `65c5ea7`，设计基线为 `b5c665d`；功能分支已删除，隔离 worktree 已归档，尚未推送远端。其余历史交接提交来自原 `codex/langgraph-real-gate` 演进链。未经用户明确要求，不自动推送远端或处理其他工作树。

## 10. 接手检查与禁止事项

开始修改前：

- [ ] 阅读 `AGENTS.md`、本交接文档和相关模块 README/设计文档。
- [ ] 分别运行后端和前端仓库的 `git status --short --branch`。
- [ ] 使用 `rg` 确认实际调用链、配置键和测试，不把规划能力当成已实现能力。
- [ ] 确认真实验收需要的 Spring、Python、PostgreSQL、Redis、前端和模型环境由谁启动。
- [ ] 确认内部令牌只存在于本地环境变量或密钥配置，不进入命令回显和报告。

提交前：

- [ ] Java 改动运行相关测试和 `mvn clean -DskipTests compile`。
- [ ] Python 改动运行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`。
- [ ] 前端改动运行三组 Node 测试、`npm run type-check`、`npm run build-only`。
- [ ] 跨服务变更同步检查 Java 事件适配、Python schema、共享契约、README 和本文。
- [ ] 运行 `git diff --check`，只提交任务相关文件。
- [ ] 对未执行的真实模型、Docker、端到端或全量测试明确说明。

禁止擅自处理：

- `projects/` 下的用户生成文件。
- `.env`、`application-local.yml`、真实密钥、账号、Cookie 和个人路径。
- 前端仓库中与当前任务无关的 `package-lock.json`。
- 与 AI 链路无关的历史代码、乱码注释或大范围重构。
- 未经用户要求，不强制重置、删除分支、清理工作树、推送远端或覆盖用户未提交修改。

## 11. 相关设计与历史记录

- `ai-service/README.md`：Python AI 服务使用、配置和测试说明。
- `doc/ai-service-startup.md`：本地启动与联调步骤。
- `doc/ai-service-langchain-langgraph-refactor-design.md`：重构架构与边界设计。
- `docs/superpowers/specs/2026-09-29-postgres-checkpoint-design.md`：PostgreSQL checkpoint 迁移、故障语义和长期记忆决策。
- `docs/superpowers/specs/2026-09-29-vue-multi-agent-quality-review-design.md`：Vue 三角色质量审查、F1 错误语义、repair 和 checkpoint 边界。
- `docs/superpowers/specs/2026-09-30-customer-service-rag-design.md`：客服机器人、OSS 文档 ETL、CloseAI Embedding、Milvus、GPU Reranker 与 Task 1–13 设计；Task 1–13 已实现，真实 MySQL/OSS/CloseAI/Milvus/GPU/POSIX/多实例/断连/浏览器/E2E 和生产阈值验收仍待完成。
- `docs/superpowers/specs/2026-09-21-bounded-streaming-simple-prompts-design.md`：有限流式窗口和简短优化提示设计。
- `docs/superpowers/specs/2026-09-21-circular-preview-spinner-design.md`：预览加载图正圆修复设计。

需要追溯具体历史阶段时使用 Git 记录和上述设计文档。本文只维护当前有效事实、最新验证证据、尚未关闭的门禁和下一步顺序，不再累计逐轮日志。
