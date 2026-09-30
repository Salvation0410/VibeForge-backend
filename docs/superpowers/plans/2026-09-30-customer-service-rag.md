# Customer Service RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a logged-in, single-turn customer-service module backed by private OSS documents, Python ETL, CloseAI embeddings, Milvus retrieval, a local GPU reranker, and grounded answers with traceable sources.

**Architecture:** Spring owns authentication, knowledge-document metadata, OSS upload, MySQL Outbox, public APIs, and task scheduling. Python owns secure document download, parsing, recursive splitting, CloseAI embeddings, Milvus indexing/query, GPU reranking, and grounded answer generation. Vue provides a dedicated customer-service page and an administrator knowledge-management page; PostgreSQL checkpoint and long-term memory remain outside this feature.

**Tech Stack:** Java 21, Spring Boot 3.5.4, MyBatis-Flex, MySQL, Aliyun OSS, Python 3.12, FastAPI, LangChain, `RecursiveCharacterTextSplitter`, CloseAI OpenAI-compatible embeddings, Milvus, FlagEmbedding, Vue 3, TypeScript, Ant Design Vue.

---

## 0. Execution Boundaries

This plan touches two Git repositories:

- Backend and Python: `D:/VibeForge/yu-ai-code-mother`
- Vue frontend: `D:/VibeForge/yu-ai-code-mother-frontend`

Use an isolated backend worktree when execution starts. Do not include the backend workspace's existing `.gitignore`, `projects/`, or unrelated untracked plan changes. Check the frontend repository independently before every frontend commit.

Feature defaults:

```text
customer-service RAG disabled
single-turn JSON only
logged-in users only
PDF/DOCX/Markdown/TXT only
chunk size 1000
chunk overlap 150
Milvus initial Top K 8
reranked final Top K 3
no PostgresStore
no LangGraph checkpoint writes
```

## 1. Planned File Map

### Spring backend

Create:

- `sql/alter_customer_service_knowledge.sql`
- `src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeDocument.java`
- `src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeEtlOutbox.java`
- `src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeDocumentMapper.java`
- `src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeEtlOutboxMapper.java`
- `src/main/java/com/yupi/yuaicodemother/model/dto/customerservice/CustomerServiceAskRequest.java`
- `src/main/java/com/yupi/yuaicodemother/model/dto/customerservice/KnowledgeDocumentQueryRequest.java`
- `src/main/java/com/yupi/yuaicodemother/model/vo/CustomerServiceAnswerVO.java`
- `src/main/java/com/yupi/yuaicodemother/model/vo/KnowledgeDocumentVO.java`
- `src/main/java/com/yupi/yuaicodemother/model/vo/KnowledgeEtlOutboxVO.java`
- `src/main/java/com/yupi/yuaicodemother/config/CustomerServiceProperties.java`
- `src/main/java/com/yupi/yuaicodemother/ai/customer/CustomerServiceAiClient.java`
- `src/main/java/com/yupi/yuaicodemother/service/CustomerServiceKnowledgeService.java`
- `src/main/java/com/yupi/yuaicodemother/service/CustomerServiceAnswerService.java`
- `src/main/java/com/yupi/yuaicodemother/service/impl/CustomerServiceKnowledgeServiceImpl.java`
- `src/main/java/com/yupi/yuaicodemother/service/impl/CustomerServiceAnswerServiceImpl.java`
- `src/main/java/com/yupi/yuaicodemother/job/CustomerServiceKnowledgeEtlWorker.java`
- `src/main/java/com/yupi/yuaicodemother/controller/CustomerServiceController.java`
- `src/main/java/com/yupi/yuaicodemother/controller/admin/CustomerServiceKnowledgeAdminController.java`
- focused tests under `src/test/java/com/yupi/yuaicodemother/customerservice/`

Modify:

- `src/main/java/com/yupi/yuaicodemother/manager/OssManager.java`
- `src/main/java/com/yupi/yuaicodemother/config/OssProperties.java`
- `src/main/java/com/yupi/yuaicodemother/YuAiCodeMotherApplication.java`
- `src/main/resources/application.yml`
- `doc/ai-service-phase-one-handoff.md`

### Python AI service

Create:

- `ai-service/src/ai_service/infrastructure/knowledge_download.py`
- `ai-service/src/ai_service/infrastructure/milvus_knowledge.py`
- `ai-service/src/ai_service/models/embeddings.py`
- `ai-service/src/ai_service/models/reranker.py`
- `ai-service/src/ai_service/orchestration/document_etl.py`
- `ai-service/src/ai_service/orchestration/customer_service_rag.py`
- `ai-service/src/ai_service/prompts/customer_service.py`
- `ai-service/tests/fixtures/knowledge/sample.md`
- `ai-service/tests/fixtures/knowledge/sample.txt`
- `ai-service/tests/test_knowledge_download.py`
- `ai-service/tests/test_document_etl.py`
- `ai-service/tests/test_milvus_knowledge.py`
- `ai-service/tests/test_reranker.py`
- `ai-service/tests/test_customer_service_rag.py`

Modify:

- `ai-service/pyproject.toml`
- `ai-service/uv.lock`
- `ai-service/.env.example`
- `ai-service/src/ai_service/config.py`
- `ai-service/src/ai_service/app.py`
- `ai-service/src/ai_service/api/routes.py`
- `ai-service/src/ai_service/api/schemas.py`
- `ai-service/src/ai_service/models/base.py`
- `ai-service/src/ai_service/models/openai_compatible.py`
- `ai-service/tests/conftest.py`
- `ai-service/tests/test_api.py`
- `ai-service/tests/test_gateway_and_config.py`
- `ai-service/README.md`

### Vue frontend

Create:

- `src/api/customerService.ts`
- `src/views/customer-service/CustomerServiceView.vue`
- `src/views/admin/CustomerServiceKnowledgeView.vue`
- `src/utils/customerService.ts`
- `tests/customerService.test.ts`

Modify:

- `src/router/index.ts`
- `src/layouts/AppLayout.vue`
- `src/views/admin/AdminLayoutView.vue`

---

### Task 1: Add MySQL Knowledge and Outbox Schema

**Files:**
- Create: `sql/alter_customer_service_knowledge.sql`
- Create: `src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeDocument.java`
- Create: `src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeEtlOutbox.java`
- Create: `src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeDocumentMapper.java`
- Create: `src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeEtlOutboxMapper.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeSchemaTest.java`

- [ ] **Step 1: Write the schema contract test**

Create a test that reads the migration and asserts both tables, the unique task key, retry index, logical-delete column, and document status index exist:

```java
@Test
void migrationDefinesKnowledgeAndOutboxContracts() throws Exception {
    String sql = Files.readString(Path.of("sql/alter_customer_service_knowledge.sql"));
    assertThat(sql).contains("customer_service_knowledge_document");
    assertThat(sql).contains("customer_service_knowledge_etl_outbox");
    assertThat(sql).contains("documentVersion");
    assertThat(sql).contains("contentHash");
    assertThat(sql).contains("processingDeadline");
    assertThat(sql).contains("UNIQUE KEY uk_document_operation_version");
    assertThat(sql).contains("KEY idx_etl_claim");
}
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeSchemaTest
```

Expected: FAIL because the migration does not exist.

- [ ] **Step 3: Add the migration**

Define `customer_service_knowledge_document` with Snowflake-compatible `BIGINT` IDs, `objectKey`, `fileType`, `fileSize`, SHA-256 `contentHash`, `documentVersion`, `indexedVersion`, `etlVersion`, `chunkCount`, status, stable error code, creator/updater, timestamps, and logical deletion. Define `customer_service_knowledge_etl_outbox` with operation, task status, retries, claim owner/deadline, next retry time, stable error code, timestamps, and a unique `(documentId, operation, documentVersion, etlVersion)` key.

Use existing camel-case database columns to match the project convention.

- [ ] **Step 4: Add entities and mappers**

Use MyBatis-Flex annotations matching `CommunityTag`:

```java
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("customer_service_knowledge_document")
public class CustomerServiceKnowledgeDocument implements Serializable {
    @Id(keyType = KeyType.Generator, value = KeyGenerators.snowFlakeId)
    private Long id;
    private String name;
    private String fileType;
    private String objectKey;
    private Long fileSize;
    private String contentHash;
    private Integer documentVersion;
    private Integer indexedVersion;
    private Integer etlVersion;
    private Integer chunkCount;
    private String status;
    private String lastErrorCode;
    private Long createdBy;
    private Long updatedBy;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
    @Column(value = "isDelete", isLogicDelete = true)
    private Integer isDelete;
}
```

Add an equivalent task entity and two empty `BaseMapper` interfaces.

- [ ] **Step 5: Run focused test and compile**

Run:

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeSchemaTest
mvn clean -DskipTests compile
```

Expected: test PASS and compile success.

- [ ] **Step 6: Commit**

```powershell
git add sql/alter_customer_service_knowledge.sql src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeDocument.java src/main/java/com/yupi/yuaicodemother/model/entity/CustomerServiceKnowledgeEtlOutbox.java src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeDocumentMapper.java src/main/java/com/yupi/yuaicodemother/mapper/CustomerServiceKnowledgeEtlOutboxMapper.java src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeSchemaTest.java
git commit -m "feat: add customer service knowledge schema"
```

### Task 2: Add Private OSS Knowledge Document Operations

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/manager/OssManager.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/config/OssProperties.java`
- Modify: `src/main/resources/application.yml`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/KnowledgeDocumentFilePolicyTest.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/OssKnowledgeDocumentTest.java`

- [ ] **Step 1: Write failing file-policy tests**

Cover PDF/DOCX/MD/TXT, empty input, wrong extension, mismatched magic bytes, 20 MiB limit, normalized display name, and SHA-256 calculation. Use `MockMultipartFile`; do not call real OSS.

```java
@Test
void rejectsExecutableRenamedAsPdf() {
    MockMultipartFile file = new MockMultipartFile(
            "file", "manual.pdf", "application/pdf", "MZ".getBytes(UTF_8));
    assertThatThrownBy(() -> policy.validate(file))
            .isInstanceOf(BusinessException.class)
            .hasMessageContaining("文件内容与类型不匹配");
}
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\mvnw.cmd test -Dtest=KnowledgeDocumentFilePolicyTest,OssKnowledgeDocumentTest
```

Expected: FAIL because knowledge-document OSS methods do not exist.

- [ ] **Step 3: Add OSS configuration**

Extend `OssProperties` with:

```java
private String knowledgeDir = "customer-service-knowledge";
private long maxKnowledgeDocumentSize = 20L * 1024 * 1024;
private long knowledgeSignedUrlTtlSeconds = 600;
```

Add matching environment-backed YAML properties without real credentials.

- [ ] **Step 4: Implement private object methods**

Add focused methods to `OssManager`:

```java
public KnowledgeObject uploadKnowledgeDocument(MultipartFile file) { }
public URL generateKnowledgeDownloadUrl(String objectKey) { }
public void deleteKnowledgeObject(String objectKey) { }
```

`KnowledgeObject` must return only `objectKey`, normalized display name, file type, size, and SHA-256. Upload to a random object key under `knowledgeDir`; never return or persist a permanent public URL. Generate a single-object GET URL with the configured TTL.

- [ ] **Step 5: Verify with mocked OSS client construction**

Refactor client creation behind a package-private factory method so tests can assert bucket, object key, metadata, signed URL expiry, and deletion without network access.

- [ ] **Step 6: Run focused tests**

```powershell
.\mvnw.cmd test -Dtest=KnowledgeDocumentFilePolicyTest,OssKnowledgeDocumentTest
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/main/java/com/yupi/yuaicodemother/manager/OssManager.java src/main/java/com/yupi/yuaicodemother/config/OssProperties.java src/main/resources/application.yml src/test/java/com/yupi/yuaicodemother/customerservice/KnowledgeDocumentFilePolicyTest.java src/test/java/com/yupi/yuaicodemother/customerservice/OssKnowledgeDocumentTest.java
git commit -m "feat: add private OSS knowledge documents"
```

### Task 3: Add Python RAG Dependencies and Configuration

**Files:**
- Modify: `ai-service/pyproject.toml`
- Modify: `ai-service/uv.lock`
- Modify: `ai-service/.env.example`
- Modify: `ai-service/src/ai_service/config.py`
- Test: `ai-service/tests/test_gateway_and_config.py`
- Test: `ai-service/tests/test_package_structure.py`

- [ ] **Step 1: Add failing configuration tests**

Assert the feature is disabled by default, chunk overlap is smaller than chunk size, final K does not exceed retrieval K, and Milvus/CloseAI credentials are not required while disabled.

```python
def test_customer_service_rag_defaults_are_safe(settings):
    assert settings.customer_service_rag_enabled is False
    assert settings.rag_chunk_size == 1000
    assert settings.rag_chunk_overlap == 150
    assert settings.rag_retrieval_top_k == 8
    assert settings.rag_final_top_k == 3
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_gateway_and_config.py tests/test_package_structure.py
```

Expected: FAIL because RAG settings and packages are absent.

- [ ] **Step 3: Add compatible dependencies**

Add and lock compatible releases of:

```toml
"langchain>=0.3,<1",
"langchain-text-splitters>=0.3,<1",
"pymilvus>=2.5,<3",
"FlagEmbedding>=1.3,<2",
"pypdf>=5,<7",
"python-docx>=1.1,<2",
```

Run `uv lock`, then verify the existing `langchain-core>=0.3,<1` and `langchain-openai>=0.3,<1` constraints remain resolved without a major-version upgrade.

- [ ] **Step 4: Add Settings fields and validators**

Add disabled-safe fields for CloseAI, Milvus, chunking, retrieval, reranking, OSS host allowlist, maximum download bytes, and timeouts. In the model validator, reject overlap greater than or equal to chunk size, final K greater than retrieval K, missing Milvus URI when enabled, and non-CUDA device when the configured local model requires GPU.

- [ ] **Step 5: Verify the exact imports**

Run:

```powershell
uv run python -c "from langchain.embeddings import init_embeddings; from langchain_text_splitters import RecursiveCharacterTextSplitter; from pymilvus import MilvusClient; from FlagEmbedding import FlagReranker; print('rag-imports-ok')"
```

Expected: `rag-imports-ok`.

If the pinned LangChain release no longer exposes `langchain.embeddings.init_embeddings`, implement the approved compatibility path with `langchain_openai.OpenAIEmbeddings`, update the design deviation note, and keep the same `EmbeddingProvider` contract.

- [ ] **Step 6: Run Python gates**

```powershell
uv run pytest tests/test_gateway_and_config.py tests/test_package_structure.py
uv lock --check
```

Expected: PASS and lock check success.

- [ ] **Step 7: Commit**

```powershell
git add ai-service/pyproject.toml ai-service/uv.lock ai-service/.env.example ai-service/src/ai_service/config.py ai-service/tests/test_gateway_and_config.py ai-service/tests/test_package_structure.py
git commit -m "feat: add customer service RAG configuration"
```

### Task 4: Implement Secure OSS Download and Document Parsing

**Files:**
- Create: `ai-service/src/ai_service/infrastructure/knowledge_download.py`
- Create: `ai-service/src/ai_service/orchestration/document_etl.py`
- Create: `ai-service/tests/fixtures/knowledge/sample.md`
- Create: `ai-service/tests/fixtures/knowledge/sample.txt`
- Test: `ai-service/tests/test_knowledge_download.py`
- Test: `ai-service/tests/test_document_etl.py`

- [ ] **Step 1: Write secure-download tests**

Use `httpx.MockTransport` to cover allowed HTTPS OSS host, rejected HTTP, loopback/private targets, non-whitelist redirects, size overflow, timeout conversion, and SHA-256 mismatch.

```python
@pytest.mark.asyncio
async def test_download_rejects_non_allowlisted_redirect(settings):
    downloader = KnowledgeDownloader(settings, transport=redirect_to("http://127.0.0.1/meta"))
    with pytest.raises(KnowledgeDownloadError, match="KNOWLEDGE_DOWNLOAD_TARGET_REJECTED"):
        await downloader.download(signed_url(), expected_sha256="0" * 64)
```

- [ ] **Step 2: Write parser and splitter tests**

Generate PDF and DOCX fixtures inside tests using libraries, keep MD/TXT fixtures in the repository, and assert page/title/line locators. Assert Chinese splitting uses size 1000, overlap 150, stable chunk IDs, and empty documents fail.

- [ ] **Step 3: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_knowledge_download.py tests/test_document_etl.py
```

Expected: FAIL because downloader and ETL classes do not exist.

- [ ] **Step 4: Implement `KnowledgeDownloader`**

Provide:

```python
class KnowledgeDownloader:
    async def download(
        self,
        url: str,
        *,
        expected_sha256: str,
        max_bytes: int,
    ) -> DownloadedKnowledgeFile:
        raise NotImplementedError("KnowledgeDownloader implementation belongs to this step")
```

Resolve DNS before request, reject loopback/link-local/private IPs, revalidate each redirect, stream into a temporary file, enforce byte limit, and delete the temporary file on every failure.

- [ ] **Step 5: Implement parsers and recursive splitting**

Create immutable `ParsedSection` and `KnowledgeChunk` dataclasses. Use `pypdf`, `python-docx`, Markdown heading parsing, and TXT line tracking. Instantiate exactly:

```python
RecursiveCharacterTextSplitter(
    chunk_size=settings.rag_chunk_size,
    chunk_overlap=settings.rag_chunk_overlap,
    separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
)
```

Build chunk IDs from document ID, document version, and zero-based chunk index. Do not include signed URLs in output metadata.

- [ ] **Step 6: Run focused tests**

```powershell
uv run pytest tests/test_knowledge_download.py tests/test_document_etl.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add ai-service/src/ai_service/infrastructure/knowledge_download.py ai-service/src/ai_service/orchestration/document_etl.py ai-service/tests/fixtures/knowledge ai-service/tests/test_knowledge_download.py ai-service/tests/test_document_etl.py
git commit -m "feat: parse and split customer service documents"
```

### Task 5: Implement CloseAI Embeddings and Milvus Versioned Storage

**完成状态（2026-09-30）：** Task 5 已完成。架构细化为 Spring/MySQL Outbox 是唯一跨实例 mutation coordinator；本任务只实现 Python 的显式 lease 契约与 fail-closed Milvus 边界，未实现 Task 6 的认证传递或 Task 7 的 MySQL 签发/验证。

**Files:**
- Create: `ai-service/src/ai_service/models/embeddings.py`
- Create: `ai-service/src/ai_service/infrastructure/milvus_knowledge.py`
- Modify: `ai-service/src/ai_service/config.py`
- Test: `ai-service/tests/test_milvus_knowledge.py`
- Modify: `ai-service/tests/conftest.py`

- [x] **Step 1: Write embedding and Milvus tests**

Cover batched document embeddings, query embeddings, count mismatch, dimension mismatch, NaN rejection, idempotent version writes, stale-version rejection, document disable/delete, staging validation, and alias switch rollback.

```python
def test_embedding_provider_rejects_non_finite_vector():
    provider = FakeEmbeddingProvider(vectors=[[0.2, float("nan")]])
    with pytest.raises(EmbeddingOutputError, match="KNOWLEDGE_EMBEDDING_INVALID_VECTOR"):
        provider.validate([[0.2, float("nan")]], expected_count=1)
```

- [x] **Step 2: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_milvus_knowledge.py
```

Expected: FAIL because providers and store do not exist.

- [x] **Step 3: Implement `EmbeddingProvider`**

Define:

```python
class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError
```

Initialize CloseAI once from settings using model `openai:text-embedding-3-large`. Batch deterministically, validate output, and convert provider exceptions into stable internal errors without including response bodies or keys.

实际实现使用 `langchain.embeddings.init_embeddings` 创建并复用底层客户端，按配置批量执行 document embedding，独立支持 query embedding，校验输出数量、跨批维度和所有值均为有限数。供应商异常只暴露稳定内部错误码，不回显 API Key 或响应正文。真实 CloseAI 尚未调用。

- [x] **Step 4: Implement `MilvusKnowledgeStore`**

Expose focused methods:

```python
class KnowledgeStore(Protocol):
    async def upsert_document_version(
        self, document: IndexedDocument, *, lease: KnowledgeMutationLease
    ) -> IndexResult:
        raise NotImplementedError

    async def delete_document(
        self, document_id: str, document_version: int, *, lease: KnowledgeMutationLease
    ) -> None:
        raise NotImplementedError

    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]:
        raise NotImplementedError

    async def rebuild_collection(
        self,
        documents: AsyncIterator[IndexedDocument],
        *,
        lease: KnowledgeMutationLease,
    ) -> RebuildResult:
        raise NotImplementedError

    async def ping(self) -> bool:
        raise NotImplementedError
```

Store document/version/chunk metadata, enforce a single vector dimension per collection, filter `isActive`, and switch a stable alias only after complete staging validation.

实际实现还包括：

- `upsert/delete/rebuild` 显式要求不可伪造的 `scope / operation / fence / expiry / proof`；默认 `DenyAllKnowledgeMutationCoordinator`，缺失、伪造、过期、撤销或不匹配一律 fail-closed。
- 同一 document scope 串行；collection rebuild scope 与全部 document scope 互斥。进程内 `asyncio.Lock` 仅是优化，不承担跨实例正确性。
- versioned chunk/manifest、旧版本保护、写后 readback、增量收敛、全量 staging + alias、确定性 tombstone，以及 fence metadata。
- 写入与校验统一 float32 canonicalization；文档历史使用 query iterator 分页并设置 10000 条硬上限。
- 同步 RPC 使用可配置 timeout；取消时 shield 并 drain 已启动 RPC，确定结束后才释放 permit/锁。所有 drop 前 readback alias，不确定时保留 staging。
- pymilvus 2.6 COSINE `distance` 按相似度处理；完整 alias 进入 canonical/staging fingerprint，control 名包含完整 alias hash，所有派生名合法且不超过 255 字符。

- [x] **Step 5: Run focused tests**

```powershell
uv run pytest tests/test_milvus_knowledge.py
```

Expected: PASS without connecting to real Milvus.

实际结果：`tests/test_milvus_knowledge.py` 49 项通过；完整 Python 测试 375 项通过、1 项跳过；`compileall`、`uv lock --check` 和 `git diff --check` 通过。测试使用 Fake CloseAI/Fake Milvus，未连接真实服务。

- [x] **Step 6: Commit**

```powershell
git add ai-service/src/ai_service/models/embeddings.py ai-service/src/ai_service/infrastructure/milvus_knowledge.py ai-service/tests/conftest.py ai-service/tests/test_milvus_knowledge.py
git commit -m "feat: add CloseAI embeddings and Milvus store"
```

实际提交链包含初始实现与双轮安全加固，可概括为 `e052d20`、`6a6771f`、`6970873`、`2a865b6`、`a5841dd`、`a6ee768`。

人工待验：真实 CloseAI；真实 Docker Milvus 的 schema、dynamic fields、Strong consistency、分页、批量写入、alias 切换和重启恢复；Task 6/7 完成后的 Spring lease 集成。

### Task 6: Expose Authenticated Python ETL API

**Files:**
- Modify: `ai-service/src/ai_service/api/schemas.py`
- Modify: `ai-service/src/ai_service/api/routes.py`
- Modify: `ai-service/src/ai_service/app.py`
- Modify: `ai-service/src/ai_service/orchestration/document_etl.py`
- Test: `ai-service/tests/test_api.py`
- Test: `ai-service/tests/test_document_etl.py`

- [ ] **Step 1: Write failing ETL API tests**

Test missing auth, valid `INDEX`, repeated idempotency key, stale document version, `DELETE`, parse failure, and disabled feature. Assert responses never echo the signed URL or document body.

```python
def test_etl_requires_internal_auth(app_factory):
    response = TestClient(app_factory()).post(
        "/internal/v1/customer-service/knowledge:etl",
        json=etl_payload(),
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_api.py -k customer_service_etl
```

Expected: FAIL because the route is absent.

- [ ] **Step 3: Add camel-case schemas**

Add `KnowledgeEtlRequest`, `KnowledgeEtlResponse`, `KnowledgeDeleteRequest`, and stable error models. Limit IDs to 128 characters, file names to 255, URL length, allowed file types, and SHA-256 format. Mutation 请求必须通过认证 schema 携带 Spring 签发的 `scope / operation / fence / expiry / proof`，并原样传给 Milvus store；不得在 Python 内构造替代 lease。

固定 Spring Bearer 验证契约如下：

- `POST /api/internal/customer-service/knowledge-mutation-leases:validate`，请求体只允许 camelCase 六字段 `scope / operationId / operation / fence / expiresAt / proof`；`operation` 为 `INDEX / DELETE / REBUILD`，`expiresAt` 为 Unix epoch seconds。
- 成功响应是 `{code: 0, data: {verified: true, current: true, scope, operationId, operation, fence, expiresAt}, message: "ok"}`；Python 逐项匹配原请求和当前 store 动作，禁止自行生成或覆盖 operation 等 lease 字段。
- `GET /api/internal/customer-service/knowledge-mutation-leases/health` 是无请求体、无 mutation 的只读探测，成功响应 `{code: 0, data: {ready: true}, message: "ok"}`。
- 404、timeout、非 JSON、非零 code、撤销、过期、verified/current 非 true 或任意字段不匹配全部 fail-closed，且不得记录 Bearer、proof、vendor body 或签名 URL。

- [ ] **Step 4: Compose ETL dependencies in `create_app`**

Only instantiate downloader, embedding provider, Milvus store, and ETL service when `customer_service_rag_enabled` is true. Add optional injectable parameters so unit tests use fakes. Close HTTP/Milvus resources in lifespan without changing checkpoint lifecycle. `MilvusClient` 构造是同步操作，必须通过 async factory/offload 初始化，禁止阻塞 FastAPI event loop。

- [ ] **Step 5: Implement routes**

Register authenticated internal endpoints:

```text
POST /internal/v1/customer-service/knowledge:etl
POST /internal/v1/customer-service/knowledge:delete
GET  /internal/v1/customer-service/health
```

Map stable ETL errors to bounded JSON details. Do not alter existing generation routes.

- [ ] **Step 6: Run focused and full Python tests**

```powershell
uv run pytest tests/test_api.py -k customer_service_etl
uv run pytest
```

Expected: focused PASS and full suite PASS.

- [ ] **Step 7: Commit**

```powershell
git add ai-service/src/ai_service/api/schemas.py ai-service/src/ai_service/api/routes.py ai-service/src/ai_service/app.py ai-service/src/ai_service/orchestration/document_etl.py ai-service/tests/test_api.py ai-service/tests/test_document_etl.py
git commit -m "feat: expose customer service ETL API"
```

### Task 7: Implement Spring Knowledge Upload, Outbox, and ETL Worker

**Files:**
- Create: Spring DTOs, VOs, services, implementations, admin controller, `CustomerServiceAiClient`, and `CustomerServiceKnowledgeEtlWorker` listed in the file map
- Modify: `src/main/java/com/yupi/yuaicodemother/YuAiCodeMotherApplication.java`
- Modify: `src/main/resources/application.yml`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeServiceTest.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeEtlWorkerTest.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeAdminControllerTest.java`

- [ ] **Step 1: Write service transaction tests**

Assert upload creates document and Outbox in one transaction, OSS failure creates neither, duplicate hash is rejected, replacement increments version, disable creates delete task, and new-version ETL failure leaves old indexed version active.

- [ ] **Step 2: Write worker claim tests**

Assert two workers cannot claim the same task, expired claims are recoverable, stale results do not overwrite a newer document version, deterministic errors stop retrying, transient errors back off to a maximum of five attempts, and Python receives a short-lived signed URL only during execution. 另需断言 MySQL coordinator 签发和验证不可伪造 lease、fence 单调递增、同 document scope 不重叠、collection rebuild scope 与全部 document scope 冲突，以及过期/撤销 lease 被 Python fail-closed 拒绝。

- [ ] **Step 3: Run tests and verify failure**

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeServiceTest,CustomerServiceKnowledgeEtlWorkerTest,CustomerServiceKnowledgeAdminControllerTest
```

Expected: FAIL because the services and endpoints are absent.

- [ ] **Step 4: Add `CustomerServiceProperties` and scheduling**

Create `CustomerServiceProperties` with `@Component` and `@ConfigurationProperties(prefix = "ai.customer-service")`. Configure feature enablement, Python timeout, worker polling interval, batch size, claim timeout, retry maximum, and retry base delay. Bind them in `application.yml` to environment variables such as `AI_CUSTOMER_SERVICE_ENABLED` with a default of `false`. Add `@EnableScheduling` to the application only after the worker test proves scheduling is needed; guard the worker with `@ConditionalOnProperty(prefix = "ai.customer-service", name = "enabled", havingValue = "true")`.

- [ ] **Step 5: Implement `CustomerServiceAiClient`**

Follow the JDK `HttpClient` and Bearer pattern from `LangGraphAiGenerationGateway`, but use bounded JSON responses and a short ETL timeout. Error messages must include stable codes, not response bodies or signed URLs.

- [ ] **Step 6: Implement service and worker**

Spring/MySQL Outbox 是唯一跨实例 mutation coordinator。Task 7 必须把任务 claim、document/collection 冲突域、lease expiry、proof 验证和 fencing token 放在可审计的 MySQL 状态转换中；Redis 或 Python 本地锁不得成为正确性来源。Worker 只有持有有效 permit 时才能调用 Task 6 接口，并必须把 lease 传到 Python。

Task 7 还必须实现上述两个 Spring Bearer 内部接口：validate POST 从 MySQL coordinator 状态验证六字段并返回逐字段匹配结果；health GET 只读检查 coordinator 是否可验证 lease，不签发或构造虚假 mutation lease。接口缺失或不可用时 Task 6 health 必须保持 degraded/503。

Use conditional updates for claiming:

```sql
UPDATE customer_service_knowledge_etl_outbox
SET status = 'PROCESSING', processingOwner = ?, processingDeadline = ?
WHERE id = ?
  AND (status = 'PENDING' OR (status = 'PROCESSING' AND processingDeadline < NOW()))
```

After Python success, update `indexedVersion`, `chunkCount`, `etlVersion`, and status only if the current `documentVersion` still matches the task.

- [ ] **Step 7: Implement admin endpoints**

Use `@AuthCheck(mustRole = UserConstant.ADMIN_ROLE)` for upload, page, detail, reindex, disable, enable, delete, ETL task history, collection rebuild, and health. Return the concrete service result with `ResultUtils.success(serviceResult)`.

- [ ] **Step 8: Run focused tests and compile**

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeServiceTest,CustomerServiceKnowledgeEtlWorkerTest,CustomerServiceKnowledgeAdminControllerTest
mvn clean -DskipTests compile
```

Expected: PASS and compile success.

- [ ] **Step 9: Commit**

Stage only customer-service Spring files, application configuration, and tests:

```powershell
git commit -m "feat: manage customer service knowledge documents"
```

**完成状态（2026-09-30）：** Task 7 的 Spring/MySQL 文档管理、Outbox claim、全局 guard + 审计 lease、HMAC proof、内部验证接口、INDEX/DELETE worker 和管理员接口已实现。proof 不使用新的配置明文，而是从现有 `ai.token` 加固定上下文经 SHA-256 派生 HMAC-SHA256 密钥，proof 不落库也不记日志。collection rebuild 被建模为独立 `REBUILD` outbox，worker 执行期持有 collection lease 并调用 `/internal/v1/customer-service/knowledge:rebuild`；Task 6 尚未提供该 Python endpoint，因此当前真实 rebuild 会 fail-closed 而不会误报完成。需在后续修复中补齐 Python staging + alias 原子发布接口。

### Task 8: Implement Local GPU Reranker

**Files:**
- Create: `ai-service/src/ai_service/models/reranker.py`
- Test: `ai-service/tests/test_reranker.py`
- Modify: `ai-service/src/ai_service/app.py`

- [ ] **Step 1: Write reranker tests**

Cover one-time model loading, CUDA device selection, stable ordering, batch execution, score normalization, timeout, OOM conversion, and disabled mode.

```python
@pytest.mark.asyncio
async def test_reranker_returns_only_requested_top_n():
    reranker = LocalCrossEncoderReranker(model=FakeCrossEncoder([0.1, 0.9, 0.4]))
    result = await reranker.rerank("deploy app", chunks(3), top_n=2)
    assert [item.chunk.chunk_id for item in result] == ["chunk-1", "chunk-2"]
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_reranker.py
```

Expected: FAIL because reranker classes are absent.

- [ ] **Step 3: Implement provider interface and local model**

Define `RerankerProvider`, `DisabledReranker`, and `LocalCrossEncoderReranker`. Load `BAAI/bge-reranker-v2-m3` once at app startup, run blocking GPU inference through a bounded thread executor, use batches from settings, and convert CUDA OOM to `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE` without exposing device diagnostics to callers.

- [ ] **Step 4: Run focused tests**

```powershell
uv run pytest tests/test_reranker.py
```

Expected: PASS without downloading the real model because tests inject a fake cross-encoder.

- [ ] **Step 5: Commit**

```powershell
git add ai-service/src/ai_service/models/reranker.py ai-service/src/ai_service/app.py ai-service/tests/test_reranker.py
git commit -m "feat: add GPU customer service reranker"
```

### Task 9: Implement Grounded Customer-Service RAG

**Files:**
- Create: `ai-service/src/ai_service/orchestration/customer_service_rag.py`
- Create: `ai-service/src/ai_service/prompts/customer_service.py`
- Modify: `ai-service/src/ai_service/models/base.py`
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/src/ai_service/api/schemas.py`
- Modify: `ai-service/src/ai_service/api/routes.py`
- Modify: `ai-service/src/ai_service/app.py`
- Modify: `ai-service/tests/conftest.py`
- Test: `ai-service/tests/test_customer_service_rag.py`
- Test: `ai-service/tests/test_api.py`

- [ ] **Step 1: Write retrieval and grounding tests**

Cover Top 8 retrieval, stale/inactive filtering, deduplication, reranked Top 3, no-hit fallback, reranker degradation, prompt-injection text treated as data, invalid model citations, maximum three sources, excerpt truncation, and no checkpoint interaction.

- [ ] **Step 2: Write API tests**

Test auth, feature-disabled response, bounded question length, successful JSON, no-answer JSON, embedding/Milvus unavailable errors, and model invalid-output fallback.

- [ ] **Step 3: Run tests and verify failure**

```powershell
Set-Location ai-service
uv run pytest tests/test_customer_service_rag.py tests/test_api.py -k customer_service
```

Expected: FAIL because the RAG service and route are absent.

- [ ] **Step 4: Extend the model protocol**

Add a structured method that reuses the existing OpenAI-compatible chat client:

```python
async def answer_customer_service(
    self,
    question: str,
    contexts: list[CustomerServiceContext],
) -> CustomerServiceModelAnswer:
    raise NotImplementedError
```

Implement strict JSON parsing in `OpenAICompatibleModel`; validate `answered`, bounded answer text, unique cited chunk IDs, and reject citations outside the provided context.

- [ ] **Step 5: Implement prompt and RAG service**

The system prompt must state that retrieved documents are untrusted data, facts must be grounded in provided chunk IDs, and insufficient evidence returns `answered=false`. The service must never call the answer model when retrieval is empty or below the evaluated threshold.

- [ ] **Step 6: Add authenticated answer route**

Register:

```text
POST /internal/v1/customer-service/answers
```

Return answer, sources, and internal `degraded` state. Do not include raw vector scores, prompts, or model reasoning.

- [ ] **Step 7: Run focused and full Python gates**

```powershell
uv run pytest tests/test_customer_service_rag.py tests/test_api.py -k customer_service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all commands succeed.

- [ ] **Step 8: Commit**

```powershell
git add ai-service/src/ai_service/orchestration/customer_service_rag.py ai-service/src/ai_service/prompts/customer_service.py ai-service/src/ai_service/models/base.py ai-service/src/ai_service/models/openai_compatible.py ai-service/src/ai_service/api/schemas.py ai-service/src/ai_service/api/routes.py ai-service/src/ai_service/app.py ai-service/tests/conftest.py ai-service/tests/test_customer_service_rag.py ai-service/tests/test_api.py
git commit -m "feat: answer customer questions with grounded RAG"
```

### Task 10: Add Spring Logged-In Customer-Service API

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/model/dto/customerservice/CustomerServiceAskRequest.java`
- Create: `src/main/java/com/yupi/yuaicodemother/model/vo/CustomerServiceAnswerVO.java`
- Create: `src/main/java/com/yupi/yuaicodemother/service/CustomerServiceAnswerService.java`
- Create: `src/main/java/com/yupi/yuaicodemother/service/impl/CustomerServiceAnswerServiceImpl.java`
- Create: `src/main/java/com/yupi/yuaicodemother/controller/CustomerServiceController.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/customer/CustomerServiceAiClient.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceControllerTest.java`
- Test: `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceAnswerServiceTest.java`

- [ ] **Step 1: Write failing authentication and contract tests**

Assert anonymous requests receive the existing not-login response, logged-in requests call Python once, blank or oversized questions fail, unknown source document IDs are rejected, and the public response excludes `degraded` and raw scores.

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceControllerTest,CustomerServiceAnswerServiceTest
```

Expected: FAIL because the endpoint is absent.

- [ ] **Step 3: Implement bounded DTO and VO**

Use Bean Validation or explicit `ThrowUtils` checks for one nonblank question with a fixed maximum length. Define nested source VO with document ID, name, version, chunk ID, locator, and bounded excerpt.

- [ ] **Step 4: Implement service validation**

Generate a UUID request ID, call Python with no user profile or business data, verify every returned source references a currently READY document/version, cap sources at three, and map internal failures to ordinary user messages plus stable codes.

- [ ] **Step 5: Implement controller and rate limit**

Create `POST /customer-service/ask`, resolve the current login user through `SysUserService`, apply a user-level `@RateLimit`, and return `ResultUtils.success(answer)`.

- [ ] **Step 6: Run tests and compile**

```powershell
.\mvnw.cmd test -Dtest=CustomerServiceControllerTest,CustomerServiceAnswerServiceTest
mvn clean -DskipTests compile
```

Expected: PASS and compile success.

- [ ] **Step 7: Commit**

```powershell
git add src/main/java/com/yupi/yuaicodemother/model/dto/customerservice src/main/java/com/yupi/yuaicodemother/model/vo/CustomerServiceAnswerVO.java src/main/java/com/yupi/yuaicodemother/service/CustomerServiceAnswerService.java src/main/java/com/yupi/yuaicodemother/service/impl/CustomerServiceAnswerServiceImpl.java src/main/java/com/yupi/yuaicodemother/controller/CustomerServiceController.java src/main/java/com/yupi/yuaicodemother/ai/customer/CustomerServiceAiClient.java src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceControllerTest.java src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceAnswerServiceTest.java
git commit -m "feat: expose logged-in customer service API"
```

### Task 11: Add Vue Customer-Service API and Page

**Repository:** `D:/VibeForge/yu-ai-code-mother-frontend`

**Files:**
- Create: `src/api/customerService.ts`
- Create: `src/utils/customerService.ts`
- Create: `src/views/customer-service/CustomerServiceView.vue`
- Create: `tests/customerService.test.ts`
- Modify: `src/router/index.ts`
- Modify: `src/layouts/AppLayout.vue`

- [ ] **Step 1: Write pure TypeScript tests**

Test source deduplication, three-source cap, excerpt trimming, and distinct UI states for answered, no-answer, service unavailable, and cancelled requests.

```typescript
test('normalizeSources keeps at most three unique chunks', () => {
  assert.deepEqual(normalizeSources(sourceFixtures), expectedThreeSources)
})
```

- [ ] **Step 2: Run test and verify failure**

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
node --test --experimental-strip-types tests/customerService.test.ts
```

Expected: FAIL because the utility does not exist.

- [ ] **Step 3: Implement API types and utility**

Define request, answer, and source types. Use the shared Axios request instance with credentials. Preserve the backend `BaseResponse` shape and do not expose internal degradation fields.

- [ ] **Step 4: Add route and navigation**

Add `/customer-service` under `AppLayout` with `requiresAuth: true`. Add “智能客服” to the logged-in navigation and update `resolveMenuKey`.

- [ ] **Step 5: Build the page**

Implement one question input, submit button, request cancellation with `AbortController`, answer area, up to three expandable sources, and separate no-answer/error messages. Do not add conversation history or streaming state.

- [ ] **Step 6: Run frontend gates**

```powershell
node --test --experimental-strip-types tests/customerService.test.ts
npm run type-check
npm run build-only
```

Expected: PASS.

- [ ] **Step 7: Commit in frontend repository**

```powershell
git add src/api/customerService.ts src/utils/customerService.ts src/views/customer-service/CustomerServiceView.vue tests/customerService.test.ts src/router/index.ts src/layouts/AppLayout.vue
git commit -m "feat: add customer service question page"
```

### Task 12: Add Vue Knowledge Administration Page

**Repository:** `D:/VibeForge/yu-ai-code-mother-frontend`

**Files:**
- Modify: `src/api/customerService.ts`
- Create: `src/views/admin/CustomerServiceKnowledgeView.vue`
- Modify: `src/router/index.ts`
- Modify: `src/views/admin/AdminLayoutView.vue`
- Modify: `tests/customerService.test.ts`

- [ ] **Step 1: Extend tests for admin normalization**

Cover allowed extensions, 20 MiB client-side guard, status labels, retry availability, and error-code display without signed URLs.

- [ ] **Step 2: Run test and verify failure**

```powershell
node --test --experimental-strip-types tests/customerService.test.ts
```

Expected: FAIL for missing admin helpers.

- [ ] **Step 3: Add admin API methods**

Implement multipart upload, paged list, reindex, enable, disable, delete, ETL history, rebuild, and health methods. Do not store the selected file after upload completes.

- [ ] **Step 4: Add admin route and menu**

Add `/admin/customer-service/knowledge` with `roles: ['admin']` and a “客服知识库” menu item.

- [ ] **Step 5: Implement admin page**

Display document name, type, size, version, status, chunk count, update time, and stable error code. Provide upload, retry, enable/disable, delete confirmation, and collection rebuild confirmation. Never display credentials, signed URLs, vectors, or full document content.

- [ ] **Step 6: Run frontend gates**

```powershell
node --test --experimental-strip-types tests/customerService.test.ts
npm run type-check
npm run build-only
```

Expected: PASS.

- [ ] **Step 7: Commit in frontend repository**

```powershell
git add src/api/customerService.ts src/views/admin/CustomerServiceKnowledgeView.vue src/router/index.ts src/views/admin/AdminLayoutView.vue tests/customerService.test.ts
git commit -m "feat: add customer service knowledge admin"
```

### Task 13: Add Health Isolation, Evaluation Fixtures, and Operational Documentation

**Files:**
- Modify: `ai-service/src/ai_service/api/routes.py`
- Modify: `ai-service/src/ai_service/app.py`
- Create: `ai-service/tests/fixtures/customer_service_eval.json`
- Create: `ai-service/tests/test_customer_service_health.py`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `docs/superpowers/specs/2026-09-30-customer-service-rag-design.md` only if implementation discovers an approved deviation

- [ ] **Step 1: Write health-isolation tests**

Assert disabled RAG does not initialize Milvus/Reranker and does not affect `/health/ready`; enabled but unhealthy RAG reports a separate degraded customer-service status while code-generation readiness remains determined by checkpoint health.

- [ ] **Step 2: Add a versioned evaluation fixture**

Use synthetic, non-sensitive entries with expected document IDs for answerable questions and explicit `expectedAnswerable=false` for no-answer and prompt-injection cases. Include at least one Chinese paraphrase, typo, exact error-code query, and malicious instruction.

- [ ] **Step 3: Implement health summary and offline evaluator entry point**

Provide an authenticated customer-service health endpoint and a CLI/test helper that calculates Recall@8, MRR@3/NDCG@3, no-answer accuracy, citation validity, and latency from injected providers. The default test must not connect to OSS, CloseAI, Milvus, or GPU.

- [ ] **Step 4: Update documentation**

Document configuration, startup order, knowledge upload, ETL states, Milvus alias strategy, GPU model loading, rollback, stable error codes, and manual validation. Mark real OSS, CloseAI, Milvus, GPU, and end-to-end tests as pending until executed.

- [ ] **Step 5: Run documentation and service gates**

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeSchemaTest,KnowledgeDocumentFilePolicyTest,OssKnowledgeDocumentTest,CustomerServiceKnowledgeServiceTest,CustomerServiceKnowledgeEtlWorkerTest,CustomerServiceKnowledgeAdminControllerTest,CustomerServiceControllerTest,CustomerServiceAnswerServiceTest
mvn clean -DskipTests compile
git diff --check
```

Expected: all commands succeed; any known unrelated full-Java-test limitation remains explicitly documented.

- [ ] **Step 6: Commit backend documentation and health changes**

```powershell
git add ai-service/src/ai_service/api/routes.py ai-service/src/ai_service/app.py ai-service/tests/fixtures/customer_service_eval.json ai-service/tests/test_customer_service_health.py ai-service/README.md doc/ai-service-startup.md doc/ai-service-phase-one-handoff.md docs/superpowers/specs/2026-09-30-customer-service-rag-design.md
git commit -m "docs: add customer service RAG operations"
```

### Task 14: Run Opt-In Real Environment Validation

**Files:**
- Create: `scripts/verify-customer-service-rag.ps1`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Do not commit credentials, generated reports containing content, model caches, or uploaded fixtures.

- [ ] **Step 1: Create a dry-run-first validation script**

The script must default to printing planned checks. `-Execute` runs non-secret health probes; separate explicit switches enable real OSS/CloseAI/Milvus/GPU checks. Reports store only step name, status, exit code, duration, stable error code, document ID/version, chunk count, and evidence reference.

- [ ] **Step 2: Validate local Docker Milvus**

Confirm connection, collection creation, vector dimension, insert/search/delete, staging collection, alias switch, and restart recovery. Do not print URI tokens.

- [ ] **Step 3: Validate CloseAI embeddings**

Embed a non-sensitive synthetic sentence, record returned dimension and latency, verify batching and rate-limit behavior, and confirm logs do not contain the API key or response body.

- [ ] **Step 4: Validate GPU reranker**

Record GPU model load success, batch Top 8 latency, P50/P95, peak memory, concurrent request behavior, and controlled OOM conversion. Do not record GPU serial numbers or unrelated host inventory.

- [ ] **Step 5: Validate real OSS and four document formats**

Upload non-sensitive fixtures, verify short-lived signed downloads, page/title/line locators, Hash checks, ETL retries, document replacement, disable/delete, and seven-day retention configuration.

- [ ] **Step 6: Validate full Spring/Python/Vue flow**

Test logged-in access, anonymous rejection, answer with sources, no-answer fallback, prompt injection, dependency outage, old-version preservation, collection rollback, and frontend error states.

- [ ] **Step 7: Update handoff with actual evidence**

Mark each item `passed`, `failed`, `blocked`, or `not-run`; include exact date, sanitized environment version, request/document IDs, stable error codes, and evidence references. Never claim real validation passed unless this task executed successfully.

- [ ] **Step 8: Commit validation tooling and sanitized status only**

```powershell
git add scripts/verify-customer-service-rag.ps1 doc/ai-service-phase-one-handoff.md
git commit -m "test: add customer service RAG validation gate"
```

## 2. Final Verification Checklist

Backend/Python repository:

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check

Set-Location D:/VibeForge/yu-ai-code-mother
mvn clean -DskipTests compile
.\mvnw.cmd test -Dtest=CustomerServiceKnowledgeSchemaTest,KnowledgeDocumentFilePolicyTest,OssKnowledgeDocumentTest,CustomerServiceKnowledgeServiceTest,CustomerServiceKnowledgeEtlWorkerTest,CustomerServiceKnowledgeAdminControllerTest,CustomerServiceControllerTest,CustomerServiceAnswerServiceTest
git diff --check
git status --short
```

Frontend repository:

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
node --test --experimental-strip-types tests/customerService.test.ts tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
git diff --check
git status --short
```

Completion requires:

- Customer-service RAG remains disabled by default.
- Existing code-generation Python tests still pass.
- PostgreSQL checkpoint behavior is unchanged.
- No long-term memory or `PostgresStore` is introduced.
- Backend and frontend commits contain only feature-related files.
- Real OSS, CloseAI, Milvus, GPU, and end-to-end results are recorded honestly as passed or pending.
