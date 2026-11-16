package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeMutationLeaseMapper;
import com.yupi.yuaicodemother.service.CustomerServiceKnowledgeTaskFinalizer;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mybatis.spring.SqlSessionTemplate;
import org.mybatis.spring.transaction.SpringManagedTransactionFactory;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.jdbc.datasource.AbstractDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import org.springframework.transaction.support.TransactionTemplate;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

/** 只在随机 schema 执行生产 SQL；真实连接和事务不代表完整 Spring/Python/Milvus 发布验收。 */
@EnabledIfEnvironmentVariable(named = "AI_KNOWLEDGE_MYSQL_OWNERSHIP_INTEGRATION", matches = "true")
class KnowledgeOwnershipMysqlIntegrationTest {
    private static final LocalDateTime NOW = LocalDateTime.of(2030, 1, 1, 0, 0);
    private static final Instant INSTANT = NOW.toInstant(ZoneOffset.UTC);

    @Test
    void independentConnectionsCompeteForSingleClaim() throws Exception {
        isolated(f -> {
            f.seed(1);
            // 两个事务先占用不同物理连接，再同时发起竞争，不以线程启动先后冒充并发。
            var results = race(
                    (ready, start) -> claimTransaction(f, "owner-a", ready, start),
                    (ready, start) -> claimTransaction(f, "owner-b", ready, start));
            assertNotEquals(results.first.connectionId, results.second.connectionId);
            assertEquals(1, results.first.changed + results.second.changed);
            assertEquals(results.first.changed == 1 ? "owner-a" : "owner-b", f.task(1).getProcessingOwner());
            assertEquals("PROCESSING", f.task(1).getStatus());
        });
    }

    @Test
    void expiredPersistentProcessingCanBeTakenOverWithoutOldOwnerWrites() throws Exception {
        isolated(f -> {
            f.seed(1);
            assertEquals(1, f.outbox.claim(1, "old", NOW, NOW.plusSeconds(10)));
            // PROCESSING 表示 RPC 回执未落账；这里只模拟持久状态，不声称制造了真实网络丢包。
            assertEquals(0, f.outbox.claim(1, "new", NOW.plusSeconds(10), NOW.plusSeconds(40)));
            assertEquals(1, f.outbox.claim(1, "new", NOW.plusSeconds(11), NOW.plusSeconds(40)));
            var before = f.task(1);
            assertEquals(0, f.outbox.finish(1, "old", "SUCCEEDED", null));
            assertEquals(0, f.outbox.retry(1, "old", "FAILED", 9, NOW.plusDays(1), "OLD_FAILURE"));
            assertEquals(0, f.outbox.refreshClaim(1, "old", NOW.plusDays(2)));
            assertEquals(before, f.task(1));
            assertEquals(1, f.outbox.refreshClaim(1, "new", NOW.plusSeconds(50)));
            assertEquals(1, f.outbox.finish(1, "new", "SUCCEEDED", null));
            assertEquals("SUCCEEDED", f.task(1).getStatus());
        });
    }

    @Test
    void finalizerRollsBackDocumentWhenOldOwnerOutboxCasFailsAndNewOwnerCommitsBoth() throws Exception {
        isolated(f -> {
            f.seed(1);
            assertEquals(1, f.outbox.claim(1, "old", NOW, NOW.plusSeconds(10)));
            assertEquals(1, f.outbox.claim(1, "new", NOW.plusSeconds(11), NOW.plusSeconds(40)));
            var beforeDocument = f.documents.findIncludingDeleted(1);
            var beforeTask = f.task(1);
            // 文档条件本身可命中；事务末尾旧 owner 的 outbox CAS 为零必须撤销前面的 ACTIVE 更新。
            var failure = assertThrows(IllegalStateException.class,
                    () -> f.finalizer.completeIndex(1, "old", 1, 1, 2, 7));
            assertEquals("KNOWLEDGE_TASK_FINALIZATION_CONFLICT", failure.getMessage());
            assertEquals(beforeDocument, f.documents.findIncludingDeleted(1));
            assertEquals(beforeTask, f.task(1));
            f.finalizer.completeIndex(1, "new", 1, 1, 2, 7);
            var document = f.documents.findIncludingDeleted(1);
            assertEquals("ACTIVE", document.getStatus());
            assertEquals(1L, document.getIndexedVersion());
            assertEquals(2L, document.getIndexedEtlVersion());
            assertEquals(1L, document.getIndexedActivationRevision());
            assertEquals(7, document.getChunkCount());
            assertEquals("SUCCEEDED", f.task(1).getStatus());
            assertNull(f.task(1).getProcessingOwner());
            assertNull(f.task(1).getProcessingDeadline());
        });
    }

    @Test
    void failIndexRollsBackForOldOwnerAndCommitsDocumentAndRetryForNewOwner() throws Exception {
        isolated(f -> {
            f.seed(1);
            assertEquals(1, f.outbox.claim(1, "old", NOW, NOW.plusSeconds(10)));
            assertEquals(1, f.outbox.claim(1, "new", NOW.plusSeconds(11), NOW.plusSeconds(40)));
            var beforeDocument = f.documents.findIncludingDeleted(1);
            var beforeTask = f.task(1);
            LocalDateTime failedAt = NOW.plusMinutes(2);
            // 文档失败更新可以命中，但旧 owner 的 retry CAS 失败时不能留下孤立 FAILED 状态。
            var failure = assertThrows(IllegalStateException.class,
                    () -> f.finalizer.failIndex(1, "old", 1, 1, 2, 3, failedAt, "OLD_FAILURE"));
            assertEquals("KNOWLEDGE_TASK_FINALIZATION_CONFLICT", failure.getMessage());
            assertEquals(beforeDocument, f.documents.findIncludingDeleted(1));
            assertEquals(beforeTask, f.task(1));

            f.finalizer.failIndex(1, "new", 1, 1, 2, 2, failedAt, "CURRENT_FAILURE");
            var document = f.documents.findIncludingDeleted(1);
            var task = f.task(1);
            assertEquals("FAILED", document.getStatus());
            assertEquals("CURRENT_FAILURE", document.getLastErrorCode());
            assertEquals(beforeDocument.getIndexedVersion(), document.getIndexedVersion());
            assertEquals(beforeDocument.getIndexedEtlVersion(), document.getIndexedEtlVersion());
            assertEquals(beforeDocument.getIndexedActivationRevision(), document.getIndexedActivationRevision());
            assertEquals("FAILED", task.getStatus());
            assertEquals(2, task.getRetryCount());
            assertEquals(failedAt, task.getNextRetryTime());
            assertEquals("CURRENT_FAILURE", task.getLastErrorCode());
            assertNull(task.getProcessingOwner());
            assertNull(task.getProcessingDeadline());
        });
    }

    @Test
    void lateIndexCannotActivateDisabledDeletedOrAdvancedDocument() throws Exception {
        isolated(f -> {
            String[] transitions = {
                    "status='DISABLED',etlVersion=3",
                    "isDelete=1,etlVersion=3",
                    "documentVersion=2,etlVersion=3",
                    "etlVersion=3",
                    // 与正式 enable 一致，激活修订和 ETL 修订一起推进；不把单独 activation CAS 写成现有能力。
                    "activationRevision=2,etlVersion=3"
            };
            for (int i = 0; i < transitions.length; i++) {
                long id = i + 1;
                f.seed(id);
                assertEquals(1, f.outbox.claim(id, "late", NOW, NOW.plusMinutes(1)));
                f.execute("UPDATE customer_service_knowledge_document SET " + transitions[i] + " WHERE id=" + id);
                var before = f.documents.findIncludingDeleted(id);
                f.finalizer.completeIndex(id, "late", id, 1, 2, 99);
                assertEquals(before, f.documents.findIncludingDeleted(id));
                assertNotEquals("ACTIVE", f.documents.findIncludingDeleted(id).getStatus());
                assertEquals("SKIPPED", f.task(id).getStatus());
                assertEquals("KNOWLEDGE_TASK_STALE", f.task(id).getLastErrorCode());
            }
        });
    }

    @Test
    void twoTransactionalCoordinatorsSerializeDocumentAndCollectionCompetition() throws Exception {
        isolated(f -> {
            var first = f.coordinator(INSTANT);
            var second = f.coordinator(INSTANT);
            String collection = "collection:customer_service_knowledge";
            String[][] pairs = {{"document:1", "document:1"}, {"document:1", collection},
                    {collection, "document:1"}, {collection, collection}};
            for (int i = 0; i < pairs.length; i++) {
                String left = pairs[i][0], right = pairs[i][1];
                String opA = "race_a_" + i, opB = "race_b_" + i;
                var result = race(
                        (ready, start) -> acquire(first, left, opA, ready, start),
                        (ready, start) -> acquire(second, right, opB, ready, start));
                assertEquals(1, (result.first != null ? 1 : 0) + (result.second != null ? 1 : 0));
                var winner = result.first != null ? result.first : result.second;
                assertTrue(first.validate(winner).current());
                second.revoke(winner);
                assertTrue(first.validate(winner).verified());
                assertFalse(first.validate(winner).current());
            }
            // 不同文档允许并行；collection 必须等待所有活动文档租约撤销。
            var a = first.acquire("document:11", "distinct_a", "INDEX", "a");
            var b = second.acquire("document:12", "distinct_b", "DELETE", "b");
            assertTrue(b.fence() > a.fence());
            assertThrows(IllegalStateException.class, () -> first.acquire(collection, "blocked", "REBUILD", "a"));
            first.revoke(a);
            second.revoke(b);
            var rebuild = first.acquire(collection, "rebuild", "REBUILD", "a");
            assertThrows(IllegalStateException.class, () -> second.acquire("document:99", "blocked_doc", "INDEX", "b"));
            first.revoke(rebuild);
        });
    }

    @Test
    void expiredAndRevokedLeaseProofNeverBecomesCurrentAgainOrRevokesReplacement() throws Exception {
        isolated(f -> {
            var first = f.coordinator(INSTANT);
            var later = f.coordinator(INSTANT.plusSeconds(31));
            var old = first.acquire("document:1", "expired", "INDEX", "old");
            assertTrue(first.validate(old).verified());
            assertTrue(first.validate(old).current());
            assertTrue(later.validate(old).verified());
            assertFalse(later.validate(old).current());
            var replacement = later.acquire("document:1", "replacement", "INDEX", "new");
            assertTrue(replacement.fence() > old.fence());
            first.revoke(old);
            assertTrue(later.validate(replacement).current());
            later.revoke(replacement);
            assertTrue(later.validate(replacement).verified());
            assertFalse(later.validate(replacement).current());
            var finalLease = later.acquire("document:1", "final", "DELETE", "new");
            first.revoke(old);
            later.revoke(replacement);
            assertTrue(later.validate(finalLease).current());
        });
    }

    private ClaimResult claimTransaction(Fixture f, String owner, CountDownLatch ready, CountDownLatch start) {
        return new TransactionTemplate(f.transactions).execute(status -> {
            long connectionId = f.scalar("SELECT CONNECTION_ID()");
            awaitStart(ready, start);
            return new ClaimResult(connectionId, f.outbox.claim(1, owner, NOW, NOW.plusMinutes(1)));
        });
    }

    private KnowledgeMutationCoordinator.Lease acquire(KnowledgeMutationCoordinator coordinator, String scope,
                                                        String operationId, CountDownLatch ready, CountDownLatch start) {
        awaitStart(ready, start);
        try {
            return coordinator.acquire(scope, operationId, scope.startsWith("collection:") ? "REBUILD" : "INDEX", operationId);
        } catch (IllegalStateException failure) {
            assertEquals("KNOWLEDGE_MUTATION_SCOPE_BUSY", failure.getMessage());
            return null;
        }
    }

    private static void awaitStart(CountDownLatch ready, CountDownLatch start) {
        ready.countDown();
        try {
            assertTrue(start.await(10, TimeUnit.SECONDS), "并发起点超时");
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AssertionError("并发线程被中断", error);
        }
    }

    private <T> Results<T> race(Racer<T> first, Racer<T> second) throws Exception {
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        try (var executor = Executors.newFixedThreadPool(2)) {
            var a = executor.submit((Callable<T>) () -> first.run(ready, start));
            var b = executor.submit((Callable<T>) () -> second.run(ready, start));
            try {
                assertTrue(ready.await(10, TimeUnit.SECONDS), "两个竞争者未就绪");
                start.countDown();
                return new Results<>(a.get(15, TimeUnit.SECONDS), b.get(15, TimeUnit.SECONDS));
            } finally {
                start.countDown();
                a.cancel(true);
                b.cancel(true);
                executor.shutdownNow();
            }
        }
    }

    private void isolated(Scenario scenario) throws Exception {
        String schema = "rag_owner_it_" + UUID.randomUUID().toString().replace("-", "");
        validateSchema(schema);
        try (var admin = KnowledgeRevisionMysqlSupport.connect()) {
            boolean created = false;
            try {
                try (var statement = admin.createStatement()) {
                    statement.execute("CREATE DATABASE `" + schema + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
                    created = true;
                }
                admin.setCatalog(schema);
                assertEquals(schema, admin.getCatalog());
                String ddl = Files.readString(Path.of("sql/alter_customer_service_knowledge.sql"));
                // 复用受控生产 DDL，仅需要 document/outbox/guard/lease 四表。
                KnowledgeRevisionMysqlSupport.script(admin, ddl.substring(0, ddl.indexOf("-- 内容版本原文登记")));
                scenario.run(new Fixture(schema));
            } finally {
                if (created) {
                    validateSchema(schema);
                    admin.setCatalog("yu_ai_code_mother");
                    try (var statement = admin.createStatement()) {
                        statement.execute("DROP DATABASE `" + schema + "`");
                    }
                    try (var statement = admin.prepareStatement("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=?")) {
                        statement.setString(1, schema);
                        try (var result = statement.executeQuery()) {
                            assertTrue(result.next());
                            assertEquals(0, result.getInt(1), "本次隔离 schema 必须清理完成");
                        }
                    }
                    System.out.println("RAG_OWNER_IT_SCHEMA_CLEANED");
                }
            }
        }
    }

    private static void validateSchema(String schema) {
        if (!schema.matches("rag_owner_it_[0-9a-f]{32}")) throw new IllegalArgumentException("隔离 schema 名称不合法");
    }

    private static final class Fixture {
        private final AbstractDataSource source;
        private final DataSourceTransactionManager transactions;
        private final SqlSessionTemplate sessions;
        private final CustomerServiceKnowledgeEtlOutboxMapper outbox;
        private final CustomerServiceKnowledgeDocumentMapper documents;
        private final CustomerServiceKnowledgeTaskFinalizer finalizer;

        Fixture(String schema) {
            source = new AbstractDataSource() {
                @Override
                public Connection getConnection() throws SQLException {
                    Connection connection = null;
                    try {
                        connection = KnowledgeRevisionMysqlSupport.connect();
                        connection.setCatalog(schema);
                        if (!schema.equals(connection.getCatalog())) throw new SQLException("隔离 schema 切换失败");
                        return connection;
                    } catch (Exception error) {
                        if (connection != null) connection.close();
                        // 配置错误不得把认证 URL 或密码通过异常正文带进测试日志。
                        throw new SQLException("无法建立隔离 MySQL 连接");
                    }
                }

                @Override
                public Connection getConnection(String username, String password) throws SQLException {
                    throw new SQLException("测试连接只能使用受控私有配置");
                }
            };
            transactions = new DataSourceTransactionManager(source);
            var configuration = new Configuration(new Environment("rag-owner-it", new SpringManagedTransactionFactory(), source));
            configuration.addMapper(CustomerServiceKnowledgeDocumentMapper.class);
            configuration.addMapper(CustomerServiceKnowledgeEtlOutboxMapper.class);
            configuration.addMapper(CustomerServiceKnowledgeMutationLeaseMapper.class);
            sessions = new SqlSessionTemplate(new SqlSessionFactoryBuilder().build(configuration));
            outbox = sessions.getMapper(CustomerServiceKnowledgeEtlOutboxMapper.class);
            documents = sessions.getMapper(CustomerServiceKnowledgeDocumentMapper.class);
            finalizer = transactional(new CustomerServiceKnowledgeTaskFinalizer(documents, outbox));
        }

        private <T> T transactional(T target) {
            var factory = new ProxyFactory(target);
            factory.setProxyTargetClass(true);
            factory.addAdvice(new TransactionInterceptor(transactions, new AnnotationTransactionAttributeSource()));
            @SuppressWarnings("unchecked") T proxy = (T) factory.getProxy();
            return proxy;
        }

        KnowledgeMutationCoordinator coordinator(Instant time) {
            var ai = new AiEngineProperties();
            ai.setToken("isolated-test-signing-secret");
            var properties = new CustomerServiceProperties();
            properties.setLeaseTimeoutSeconds(30);
            return transactional(new KnowledgeMutationCoordinator(
                    sessions.getMapper(CustomerServiceKnowledgeMutationLeaseMapper.class), ai, properties,
                    Clock.fixed(time, ZoneOffset.UTC)));
        }

        void seed(long id) {
            execute("INSERT INTO customer_service_knowledge_document(id,name,fileType,objectKey,fileSize,contentHash,documentVersion,etlVersion,status,createdBy,updatedBy) VALUES("
                    + id + ",'隔离所有权测试','txt','isolated/fake.txt',1,'" + String.format("%064d", id) + "',1,2,'INDEXING',1,1)");
            execute("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status,nextRetryTime) VALUES("
                    + id + "," + id + ",1,2,'INDEX','PENDING','2029-12-31 23:00:00')");
        }

        com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox task(long id) {
            return outbox.history(id, 1).getFirst();
        }

        void execute(String sql) {
            new org.springframework.jdbc.core.JdbcTemplate(source).execute(sql);
        }

        long scalar(String sql) {
            return new org.springframework.jdbc.core.JdbcTemplate(source).queryForObject(sql, Long.class);
        }
    }

    private record ClaimResult(long connectionId, int changed) { }
    private record Results<T>(T first, T second) { }
    @FunctionalInterface private interface Racer<T> { T run(CountDownLatch ready, CountDownLatch start); }
    @FunctionalInterface private interface Scenario { void run(Fixture fixture) throws Exception; }
}
