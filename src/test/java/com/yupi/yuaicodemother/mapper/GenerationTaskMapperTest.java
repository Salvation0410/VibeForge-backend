package com.yupi.yuaicodemother.mapper;

import com.yupi.yuaicodemother.model.entity.GenerationTask;
import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSession;
import org.apache.ibatis.session.SqlSessionFactory;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;
import org.apache.ibatis.transaction.jdbc.JdbcTransactionFactory;
import org.h2.jdbcx.JdbcDataSource;
import org.mybatis.spring.SqlSessionTemplate;
import org.mybatis.spring.transaction.SpringManagedTransactionFactory;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.core.artifact.GenerationFenceService;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;

import static org.junit.jupiter.api.Assertions.*;

/** 在 H2 MySQL 模式执行真实 XML SQL；不替代真实 MySQL 迁移验收。 */
class GenerationTaskMapperTest {
    private SqlSessionFactory factory;
    private GenerationFenceMapper transactionalFenceMapper;
    private JdbcDataSource dataSource;
    private final LocalDateTime now = LocalDateTime.of(2026, 10, 27, 12, 0);

    @BeforeEach
    void setup() throws Exception {
        dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        // 仅去除 H2 不支持的 MySQL 字符集与索引方言，保留字段、约束与全部业务 SQL。
        String ddl = Files.readString(Path.of("sql/alter_generation_task.sql"))
                .replace("CHARACTER SET ascii COLLATE ascii_bin ", "")
                .replaceAll("(?m)^    KEY .*\\R", "");
        ddl = ddl.substring(0, ddl.indexOf(") ENGINE=")) + ")";
        try (var connection = dataSource.getConnection(); var statement = connection.createStatement()) {
            statement.execute(ddl);
            statement.execute("ALTER TABLE generation_task ADD execution_owner VARCHAR(36)");
            statement.execute("ALTER TABLE generation_task ADD heartbeat_at TIMESTAMP(6)");
            statement.execute("ALTER TABLE generation_task ADD execution_expires_at TIMESTAMP(6)");
            statement.execute("ALTER TABLE generation_task ADD fence_token BIGINT NOT NULL DEFAULT 0");
            statement.execute("CREATE TABLE generation_arbitration (request_id VARCHAR(128) PRIMARY KEY, app_id BIGINT NOT NULL, fence_token BIGINT NOT NULL, decision VARCHAR(16) NOT NULL DEFAULT 'ACTIVE', cancel_requested_at TIMESTAMP, UNIQUE(app_id, fence_token))");
            statement.execute("CREATE TABLE generation_fence (app_id BIGINT PRIMARY KEY, current_token BIGINT NOT NULL DEFAULT 0)");
            statement.execute("CREATE TABLE generation_publication (request_id VARCHAR(128) PRIMARY KEY, app_id BIGINT NOT NULL, code_gen_type VARCHAR(32) NOT NULL, fence_token BIGINT NOT NULL, publication_sequence BIGINT NOT NULL, hashes_json JSON NOT NULL, published_at TIMESTAMP NOT NULL)");
            statement.execute("CREATE TABLE generation_active_artifact (app_id BIGINT NOT NULL, code_gen_type VARCHAR(32) NOT NULL, request_id VARCHAR(128) NOT NULL, fence_token BIGINT NOT NULL, updated_at TIMESTAMP NOT NULL, PRIMARY KEY(app_id, code_gen_type))");
        }
        Configuration config = new Configuration(new Environment("test", new JdbcTransactionFactory(), dataSource));
        String resource = "mapper/GenerationTaskMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(resource)) {
            new XMLMapperBuilder(input, config, resource, config.getSqlFragments()).parse();
        }
        String fenceResource = "mapper/GenerationFenceMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(fenceResource)) {
            new XMLMapperBuilder(input, config, fenceResource, config.getSqlFragments()).parse();
        }
        factory = new SqlSessionFactoryBuilder().build(config);
        Configuration transactionalConfig = new Configuration(new Environment("tx-test",
                new SpringManagedTransactionFactory(), dataSource));
        try (var input = getClass().getClassLoader().getResourceAsStream("mapper/GenerationFenceMapper.xml")) {
            new XMLMapperBuilder(input, transactionalConfig, fenceResource,
                    transactionalConfig.getSqlFragments()).parse();
        }
        transactionalFenceMapper = new SqlSessionTemplate(new SqlSessionFactoryBuilder().build(transactionalConfig))
                .getMapper(GenerationFenceMapper.class);
    }

    private GenerationTask task(String id, long app, long user, LocalDateTime time) {
        GenerationTask task = new GenerationTask();
        task.setRequestId(id);
        task.setAppId(app);
        task.setUserId(user);
        task.setCodeGenType("html");
        task.setFenceToken(0L);
        task.setExecutionOwner("test-owner");
        task.setCreatedAt(time);
        task.setUpdatedAt(time);
        return task;
    }

    @Test
    void uniqueIdentityOwnershipRetentionAndLatestQuery() {
        try (SqlSession session = factory.openSession(true)) {
            var mapper = session.getMapper(GenerationTaskMapper.class);
            mapper.createTask(task("old", 42, 7, now.minusDays(2)));
            mapper.createTask(task("new", 42, 7, now));
            mapper.createTask(task("foreign", 43, 8, now.plusHours(1)));
            assertEquals("new", mapper.findOwned(42, 7, null, now).getRequestId());
            assertNull(mapper.findOwned(43, 7, "new", now));
            assertNull(mapper.findOwned(42, 8, "new", now));
            assertThrows(RuntimeException.class, () -> mapper.createTask(task("new", 43, 8, now)));
            mapper.finishTask("new", "SUCCEEDED", null, now, now.plusDays(30), "test-owner", 0L);
            assertNotNull(mapper.findOwned(42, 7, "new", now.plusDays(29)));
            assertNull(mapper.findOwned(42, 7, "new", now.plusDays(30)));
            assertEquals("old", mapper.findOwned(42, 7, null, now.plusDays(31)).getRequestId());
        }
    }

    @Test
    void cancellationAndTerminalUpdatesAreIdempotentAndCannotOverwriteWinner() {
        try (SqlSession session = factory.openSession(true)) {
            var mapper = session.getMapper(GenerationTaskMapper.class);
            mapper.createTask(task("cancel", 42, 7, now));
            assertEquals(1, mapper.recordCancellation("cancel", now));
            assertEquals(0, mapper.recordCancellation("cancel", now.plusSeconds(1)));
            assertEquals(1, mapper.finishTask("cancel", "CANCELLED", "cancelled", now, now.plusDays(30), "test-owner", 0L));
            assertEquals(0, mapper.finishTask("cancel", "SUCCEEDED", null, now, now.plusDays(30), "test-owner", 0L));
            assertEquals(0, mapper.recordCancellation("cancel", now.plusSeconds(2)));
            var stored = mapper.findOwned(42, 7, "cancel", now);
            assertEquals("CANCELLED", stored.getExecutionState());
            assertEquals(now, stored.getCancelRequestedAt());
            assertEquals("cancelled", stored.getErrorCode());
            assertEquals(now, stored.getFinishedAt());
            // 引擎可能先观察到取消并结束，随后取消请求线程才落账；补记元数据不能改写终态。
            mapper.createTask(task("finish-first", 42, 7, now));
            mapper.finishTask("finish-first", "CANCELLED", "cancelled", now, now.plusDays(30), "test-owner", 0L);
            assertEquals(1, mapper.recordCancellation("finish-first", now.plusSeconds(1)));
            var lateCancellation = mapper.findOwned(42, 7, "finish-first", now.plusSeconds(2));
            assertEquals("CANCELLED", lateCancellation.getExecutionState());
            assertEquals(now.plusDays(30), lateCancellation.getQueryUntil());
        }
    }

    @Test
    void concurrentTerminalWritersHaveOnlyOneWinner() throws Exception {
        try (var session = factory.openSession(true)) {
            session.getMapper(GenerationTaskMapper.class).createTask(task("race", 42, 7, now));
        }
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        var first = terminalWriter("SUCCEEDED", ready, start);
        var second = terminalWriter("FAILED", ready, start);
        assertTrue(ready.await(5, java.util.concurrent.TimeUnit.SECONDS));
        start.countDown();
        assertEquals(1, first.get(5, java.util.concurrent.TimeUnit.SECONDS)
                + second.get(5, java.util.concurrent.TimeUnit.SECONDS));
    }

    @Test
    void fencingMapperKeepsMonotonicGenerationAndAtomicallySwitchesPublicationFacts() {
        try (SqlSession session = factory.openSession(true)) {
            var mapper = session.getMapper(GenerationFenceMapper.class);
            mapper.ensureFenceRow(42);
            assertEquals(1, mapper.incrementFence(42));
            assertEquals(1L, mapper.currentFence(42));
            assertEquals(1, mapper.incrementFence(42));
            assertEquals(2L, mapper.currentFence(42));
            assertEquals(1, mapper.insertPublication(42, "html", "fresh", 2, 7,
                    "{\"index.html\":\"abc\"}", now));
            mapper.updateActive(42, "html", "fresh", 2, now);
            assertEquals("fresh", mapper.findActive(42, "html").requestId());
            assertEquals(2L, mapper.findPublication(42, "html", "fresh").fenceToken());
        }
    }

    @Test
    void serviceRejectsStaleGenerationAndKeepsCommittedPointerAfterLaterClaim() {
        GenerationFenceService fences = new GenerationFenceService(transactionalFenceMapper, new ObjectMapper(),
                new DataSourceTransactionManager(dataSource));
        long oldToken = fences.issue(73, "old");
        long currentToken = fences.issue(73, "fresh");
        assertThrows(GenerationStreamException.class,
                () -> fences.activate(73, "html", "old", oldToken, 1, java.util.Map.of("index.html", "old")));
        fences.activate(73, "html", "fresh", currentToken, 2, java.util.Map.of("index.html", "fresh"));
        assertEquals("fresh", fences.active(73, "html").requestId());
        assertThrows(GenerationStreamException.class,
                () -> fences.activate(73, "html", "old", oldToken, 1, java.util.Map.of("index.html", "old")));
        assertEquals("fresh", fences.active(73, "html").requestId());
        assertNotNull(fences.publication(73, "html", "fresh"));
        assertNull(fences.publication(73, "html", "old"));
    }

    @Test
    void activationFailureRollsBackPublicationAndConcurrentClaimsRemainUnique() throws Exception {
        var transactions = new DataSourceTransactionManager(dataSource);
        var fences = new GenerationFenceService(transactionalFenceMapper, new ObjectMapper(), transactions);
        var fault = org.mockito.Mockito.mock(GenerationFenceMapper.class,
                org.mockito.AdditionalAnswers.delegatesTo(transactionalFenceMapper));
        org.mockito.Mockito.doThrow(new IllegalStateException("模拟指针写入失败")).when(fault)
                .updateActive(org.mockito.ArgumentMatchers.anyLong(), org.mockito.ArgumentMatchers.anyString(),
                        org.mockito.ArgumentMatchers.anyString(), org.mockito.ArgumentMatchers.anyLong(),
                        org.mockito.ArgumentMatchers.any());
        long token = fences.issue(81, "rollback");
        var broken = new GenerationFenceService(fault, new ObjectMapper(), transactions);
        assertThrows(IllegalStateException.class,
                () -> broken.activate(81, "html", "rollback", token, token, java.util.Map.of("index.html", "hash")));
        assertNull(fences.publication(81, "html", "rollback"));
        assertNull(fences.active(81, "html"));
        var first = CompletableFuture.supplyAsync(() -> fences.issue(81, UUID.randomUUID().toString()));
        var second = CompletableFuture.supplyAsync(() -> fences.issue(81, UUID.randomUUID().toString()));
        var tokens = java.util.Set.of(first.get(5, java.util.concurrent.TimeUnit.SECONDS),
                second.get(5, java.util.concurrent.TimeUnit.SECONDS));
        assertEquals(java.util.Set.of(token + 1, token + 2), tokens);
    }

    private CompletableFuture<Integer> terminalWriter(String state, CountDownLatch ready, CountDownLatch start) {
        return CompletableFuture.supplyAsync(() -> {
            try (var session = factory.openSession(true)) {
                ready.countDown();
                if (!start.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new IllegalStateException("测试同步超时");
                return session.getMapper(GenerationTaskMapper.class).finishTask("race", state, null, now, now.plusDays(30), "test-owner", 0L);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(error);
            }
        });
    }

    @Test
    void durableCancellationSurvivesServiceRebuildAndPreventsActivation() {
        var fences = fenceService();
        long token = fences.issue(92, "cancel-first");
        assertTrue(fences.cancel(92, "cancel-first"));
        var rebuilt = fenceService();
        assertTrue(rebuilt.isCancelled(92, "cancel-first"));
        assertEquals("GENERATION_CANCELLED", assertThrows(GenerationStreamException.class,
                () -> rebuilt.beginCommit(92, "cancel-first")).getErrorCode());
        assertEquals("GENERATION_CANCELLED", assertThrows(GenerationStreamException.class,
                () -> rebuilt.activate(92, "html", "cancel-first", token, token,
                        java.util.Map.of("index.html", "hash"))).getErrorCode());
        assertNull(rebuilt.active(92, "html"));
        assertNull(rebuilt.publication(92, "html", "cancel-first"));
        try (var session = factory.openSession(true)) {
            // 取消先于任务插入，查询仍返回权威时间；无需依赖 best-effort 账本补记。
            var task = task("cancel-first", 92, 7, now);
            task.setFenceToken(token);
            var tasks = session.getMapper(GenerationTaskMapper.class);
            tasks.createTask(task);
            var first = tasks.findOwned(92, 7, "cancel-first", now).getCancelRequestedAt();
            assertNotNull(first);
            assertTrue(rebuilt.cancel(92, "cancel-first"));
            session.clearCache();
            assertEquals(first, tasks.findOwned(92, 7, "cancel-first", now).getCancelRequestedAt());
        }
    }

    @Test
    void commitReservationSurvivesRestartAndRejectsCancellationEvenBeforePublication() {
        var fences = fenceService();
        long token = fences.issue(93, "commit-first");
        fences.beginCommit(93, "commit-first");
        var rebuilt = fenceService();
        assertFalse(rebuilt.cancel(93, "commit-first"));
        assertNull(rebuilt.publication(93, "html", "commit-first"));
        rebuilt.activate(93, "html", "commit-first", token, token, java.util.Map.of("index.html", "hash"));
        assertFalse(rebuilt.cancel(93, "commit-first"));
        fences.issue(93, "successor");
        assertEquals("GENERATION_FENCE_REJECTED", assertThrows(GenerationStreamException.class,
                () -> rebuilt.cancel(93, "commit-first")).getErrorCode());
        assertEquals("GENERATION_FENCE_REJECTED", assertThrows(GenerationStreamException.class,
                () -> rebuilt.cancel(94, "successor")).getErrorCode());
    }

    @Test
    void concurrentCancellationAndCommitHaveExactlyOneDurableWinner() throws Exception {
        var fences = fenceService();
        for (int iteration = 0; iteration < 12; iteration++) {
            long app = 100 + iteration;
            String request = "race-" + iteration;
            long token = fences.issue(app, request);
            var start = new CountDownLatch(1);
            var cancelled = CompletableFuture.supplyAsync(() -> {
                awaitStart(start);
                return fenceService().cancel(app, request);
            });
            var committing = CompletableFuture.supplyAsync(() -> {
                awaitStart(start);
                try {
                    fenceService().beginCommit(app, request);
                    return true;
                } catch (GenerationStreamException error) {
                    assertEquals("GENERATION_CANCELLED", error.getErrorCode());
                    return false;
                }
            });
            start.countDown();
            boolean cancellationWon = cancelled.get(5, java.util.concurrent.TimeUnit.SECONDS);
            assertNotEquals(cancellationWon, committing.get(5, java.util.concurrent.TimeUnit.SECONDS));
            if (cancellationWon) {
                assertThrows(GenerationStreamException.class, () -> fences.activate(app, "html", request,
                        token, token, java.util.Map.of("index.html", "hash")));
            } else {
                fences.activate(app, "html", request, token, token, java.util.Map.of("index.html", "hash"));
                assertEquals(request, fences.active(app, "html").requestId());
            }
        }
    }

    private GenerationFenceService fenceService() {
        return new GenerationFenceService(transactionalFenceMapper, new ObjectMapper(),
                new DataSourceTransactionManager(dataSource));
    }

    @Test
    void lostCancellationAcknowledgementKeepsDurableDecisionAndSanitizesError() {
        var fences = fenceService();
        long token = fences.issue(95, "lost-confirmation");
        var uncertainTransactions = new DataSourceTransactionManager(dataSource) {
            @Override
            protected void doCommit(org.springframework.transaction.support.DefaultTransactionStatus status) {
                super.doCommit(status);
                // 注入事务已提交但调用方丢失确认，不能回滚或把该请求重新视为 ACTIVE。
                throw new org.springframework.transaction.TransactionSystemException("模拟底层连接敏感信息");
            }
        };
        var uncertain = new GenerationFenceService(transactionalFenceMapper, new ObjectMapper(), uncertainTransactions);
        var error = assertThrows(GenerationStreamException.class, () -> uncertain.cancel(95, "lost-confirmation"));
        assertEquals("GENERATION_FENCE_UNAVAILABLE", error.getErrorCode());
        assertFalse(error.getMessage().contains("敏感信息"));
        assertTrue(fenceService().isCancelled(95, "lost-confirmation"));
        assertThrows(GenerationStreamException.class, () -> fences.activate(95, "html", "lost-confirmation",
                token, token, java.util.Map.of("index.html", "hash")));
    }

    @Test
    void failureBeforeDecisionCommitRollsBackAndRequestBindingCannotBeReused() {
        var fences = fenceService();
        fences.issue(96, "rollback-decision");
        var fault = org.mockito.Mockito.mock(GenerationFenceMapper.class,
                org.mockito.AdditionalAnswers.delegatesTo(transactionalFenceMapper));
        org.mockito.Mockito.doAnswer(call -> {
            transactionalFenceMapper.decide(call.getArgument(0), call.getArgument(1), call.getArgument(2));
            throw new IllegalStateException("模拟决策更新后事务失败");
        }).when(fault).decide(org.mockito.ArgumentMatchers.anyString(), org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any());
        var failing = new GenerationFenceService(fault, new ObjectMapper(), new DataSourceTransactionManager(dataSource));
        assertEquals("GENERATION_FENCE_UNAVAILABLE", assertThrows(GenerationStreamException.class,
                () -> failing.cancel(96, "rollback-decision")).getErrorCode());
        assertFalse(fences.isCancelled(96, "rollback-decision"));
        fences.beginCommit(96, "rollback-decision");
        assertThrows(GenerationStreamException.class, () -> fences.issue(96, "rollback-decision"));
        assertEquals(1L, transactionalFenceMapper.currentFence(96));
    }

    private void awaitStart(CountDownLatch start) {
        try {
            if (!start.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new IllegalStateException("测试同步超时");
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(error);
        }
    }

    @Test
    void heartbeatUsesDatabaseClockAndRejectsForeignOwnerTokenAndApplication() throws Exception {
        try (var session = factory.openSession(true)) {
            var tasks = session.getMapper(GenerationTaskMapper.class);
            long token = fenceService().issue(201, "heartbeat");
            var record = task("heartbeat", 201, 7, now.minusYears(4));
            record.setFenceToken(token);
            tasks.createTask(record);
            var stored = tasks.findOwned(201, 7, "heartbeat", now);
            assertEquals("LIVE", stored.getHeartbeatState());
            assertTrue(stored.getHeartbeatAt().isAfter(record.getCreatedAt().plusYears(3)));
            assertEquals(90, java.time.Duration.between(stored.getHeartbeatAt(), stored.getExecutionExpiresAt()).toSeconds());
            assertEquals(0, tasks.heartbeat("heartbeat", 201, "other-owner", token));
            assertEquals(0, tasks.heartbeat("heartbeat", 201, "test-owner", token + 1));
            assertEquals(0, tasks.heartbeat("heartbeat", 202, "test-owner", token));
            assertEquals(1, tasks.heartbeat("heartbeat", 201, "test-owner", token));
            assertEquals(0, tasks.finishTask("heartbeat", "FAILED", null, now, now.plusDays(30), "other-owner", token));
            assertEquals(0, tasks.finishTask("heartbeat", "FAILED", null, now, now.plusDays(30), "test-owner", token + 1));
            assertEquals(1, tasks.finishTask("heartbeat", "SUCCEEDED", null, now, now.plusDays(30), "test-owner", token));
            assertEquals(0, tasks.heartbeat("heartbeat", 201, "test-owner", token));
            assertEquals("TERMINAL", tasks.findOwned(201, 7, "heartbeat", now).getHeartbeatState());
        }
    }

    @Test
    void expiredHeartbeatCannotReviveAndQueryNeverChangesRunningState() throws Exception {
        try (var session = factory.openSession(true)) {
            var tasks = session.getMapper(GenerationTaskMapper.class);
            long token = fenceService().issue(202, "expired");
            var record = task("expired", 202, 7, now);
            record.setFenceToken(token);
            tasks.createTask(record);
            try (var connection = dataSource.getConnection(); var statement = connection.createStatement()) {
                statement.executeUpdate("UPDATE generation_task SET execution_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP(6)) WHERE request_id = 'expired'");
            }
            assertEquals(0, tasks.heartbeat("expired", 202, "test-owner", token));
            assertEquals("EXPIRED", tasks.findOwned(202, 7, "expired", now).getHeartbeatState());
            assertEquals("RUNNING", tasks.findOwned(202, 7, "expired", now).getExecutionState());
            assertEquals(0, tasks.heartbeat("expired", 202, "test-owner", token));
        }
    }

    @Test
    void newGenerationStopsOldHeartbeatAndLegacyRowsRemainUnknown() {
        try (var session = factory.openSession(true)) {
            var tasks = session.getMapper(GenerationTaskMapper.class);
            var fences = fenceService();
            long token = fences.issue(203, "superseded");
            var record = task("superseded", 203, 7, now);
            record.setFenceToken(token);
            tasks.createTask(record);
            fences.issue(203, "successor");
            assertEquals(0, tasks.heartbeat("superseded", 203, "test-owner", token));
            assertEquals("SUPERSEDED", tasks.findOwned(203, 7, "superseded", now).getHeartbeatState());
            var legacy = task("legacy", 204, 7, now);
            legacy.setExecutionOwner(null);
            tasks.createTask(legacy);
            var stored = tasks.findOwned(204, 7, "legacy", now);
            assertEquals("UNKNOWN", stored.getHeartbeatState());
            assertNull(stored.getHeartbeatAt());
            assertNull(stored.getExecutionExpiresAt());
            assertEquals(0, tasks.heartbeat("legacy", 204, "new-owner", 0));
        }
    }
}
