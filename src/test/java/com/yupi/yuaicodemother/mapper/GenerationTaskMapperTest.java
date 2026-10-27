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
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

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
    private final LocalDateTime now = LocalDateTime.of(2026, 10, 27, 12, 0);

    @BeforeEach
    void setup() throws Exception {
        JdbcDataSource source = new JdbcDataSource();
        source.setURL("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        // 仅去除 H2 不支持的 MySQL 字符集与索引方言，保留字段、约束与全部业务 SQL。
        String ddl = Files.readString(Path.of("sql/alter_generation_task.sql"))
                .replace("CHARACTER SET ascii COLLATE ascii_bin ", "")
                .replaceAll("(?m)^    KEY .*\\R", "");
        ddl = ddl.substring(0, ddl.indexOf(") ENGINE=")) + ")";
        try (var connection = source.getConnection(); var statement = connection.createStatement()) {
            statement.execute(ddl);
        }
        Configuration config = new Configuration(new Environment("test", new JdbcTransactionFactory(), source));
        String resource = "mapper/GenerationTaskMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(resource)) {
            new XMLMapperBuilder(input, config, resource, config.getSqlFragments()).parse();
        }
        factory = new SqlSessionFactoryBuilder().build(config);
    }

    private GenerationTask task(String id, long app, long user, LocalDateTime time) {
        GenerationTask task = new GenerationTask();
        task.setRequestId(id);
        task.setAppId(app);
        task.setUserId(user);
        task.setCodeGenType("html");
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
            mapper.finishTask("new", "SUCCEEDED", null, now, now.plusDays(30));
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
            assertEquals(1, mapper.finishTask("cancel", "CANCELLED", "cancelled", now, now.plusDays(30)));
            assertEquals(0, mapper.finishTask("cancel", "SUCCEEDED", null, now, now.plusDays(30)));
            assertEquals(0, mapper.recordCancellation("cancel", now.plusSeconds(2)));
            var stored = mapper.findOwned(42, 7, "cancel", now);
            assertEquals("CANCELLED", stored.getExecutionState());
            assertEquals(now, stored.getCancelRequestedAt());
            assertEquals("cancelled", stored.getErrorCode());
            assertEquals(now, stored.getFinishedAt());
            // 引擎可能先观察到取消并结束，随后取消请求线程才落账；补记元数据不能改写终态。
            mapper.createTask(task("finish-first", 42, 7, now));
            mapper.finishTask("finish-first", "CANCELLED", "cancelled", now, now.plusDays(30));
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

    private CompletableFuture<Integer> terminalWriter(String state, CountDownLatch ready, CountDownLatch start) {
        return CompletableFuture.supplyAsync(() -> {
            try (var session = factory.openSession(true)) {
                ready.countDown();
                if (!start.await(5, java.util.concurrent.TimeUnit.SECONDS)) throw new IllegalStateException("测试同步超时");
                return session.getMapper(GenerationTaskMapper.class).finishTask("race", state, null, now, now.plusDays(30));
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(error);
            }
        });
    }
}
