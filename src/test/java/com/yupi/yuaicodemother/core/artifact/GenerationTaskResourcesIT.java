package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.ai.gateway.*;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.mapper.GenerationTaskMapper;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.*;
import com.yupi.yuaicodemother.service.impl.AppServiceImpl;
import com.yupi.yuaicodemother.service.impl.GenerationTaskServiceImpl;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mockito.AdditionalAnswers;
import org.springframework.dao.TransientDataAccessResourceException;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 真实 MySQL/Redis/版本文件的故障矩阵；不调用模型，不修改真实应用或生成项目。 */
@EnabledIfEnvironmentVariable(named = "AI_TASK_INTEGRATION", matches = "true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class GenerationTaskResourcesIT {
    private RealGenerationTaskResources resources;
    private final List<String> requests = new ArrayList<>();
    private long appId;
    private GenerationLeaseService first;
    private GenerationLeaseService second;
    private GenerationTaskServiceImpl tasks;
    private GenerationFenceService fences;
    private ArtifactPathResolver resolver;
    private VersionedArtifactStore store;
    @org.junit.jupiter.api.io.TempDir Path root;
    private static final long USER = 7L;

    @BeforeAll
    void connect() throws Exception {
        resources = new RealGenerationTaskResources();
    }

    @BeforeEach
    void prepare() {
        appId = ThreadLocalRandom.current().nextLong(800_000_000_000_000_000L, 900_000_000_000_000_000L);
        requests.clear();
        var json = new ObjectMapper().registerModule(new JavaTimeModule());
        fences = new GenerationFenceService(resources.fenceMapper, json, new DataSourceTransactionManager(resources.dataSource()));
        first = new GenerationLeaseService(resources.first, fences);
        second = new GenerationLeaseService(resources.second, fences);
        tasks = new GenerationTaskServiceImpl(resources.mapper);
        resolver = new ArtifactPathResolver(root, json, fences);
        store = new VersionedArtifactStore(resolver, json);
    }

    @AfterEach
    void cleanup() throws Exception {
        // 只删除本测试记录和三个精确键；禁止 DROP、FLUSHDB 或按通配符清理共享资源。
        try (var connection = resources.connection();
             var delete = connection.prepareStatement("DELETE FROM generation_task WHERE app_id = ? AND user_id = ?");
             var deletePublications = connection.prepareStatement("DELETE FROM generation_publication WHERE app_id = ?");
             var deleteActive = connection.prepareStatement("DELETE FROM generation_active_artifact WHERE app_id = ?");
             var deleteArbitration = connection.prepareStatement("DELETE FROM generation_arbitration WHERE app_id = ?");
             var deleteFence = connection.prepareStatement("DELETE FROM generation_fence WHERE app_id = ?")) {
            delete.setLong(1, appId);
            delete.setLong(2, USER);
            delete.executeUpdate();
            deletePublications.setLong(1, appId);
            deletePublications.executeUpdate();
            deleteActive.setLong(1, appId);
            deleteActive.executeUpdate();
            deleteFence.setLong(1, appId);
            deleteFence.executeUpdate();
            deleteArbitration.setLong(1, appId);
            deleteArbitration.executeUpdate();
        } finally {
            resources.first.getLock("ai:generation:app:" + appId).forceUnlock();
            resources.first.getLock("ai:generation:transition:" + appId).forceUnlock();
            resources.first.getBucket("ai:generation:state:" + appId).delete();
        }
    }

    @AfterAll
    void disconnect() {
        if (resources != null) resources.close();
    }

    private String request() {
        String value = "ledger-it-" + UUID.randomUUID();
        requests.add(value);
        return value;
    }

    private GenerationTaskMapper faultyMapper() {
        return mock(GenerationTaskMapper.class, AdditionalAnswers.delegatesTo(resources.mapper));
    }

    private AppServiceImpl application(GenerationTaskService ledger, GenerationLeaseService leases,
                                       AiGenerationGateway gateway, ChatHistoryService history) {
        var service = spy(new AppServiceImpl(mock(SysUserService.class), gateway, history,
                resolver, leases, mock(HtmlOutputBudgetGuard.class)));
        ReflectionTestUtils.setField(service, "generationTaskService", ledger);
        doReturn(App.builder().id(appId).userId(USER).codeGenType("html").build()).when(service).getById(appId);
        return service;
    }

    private AppServiceImpl reader(GenerationTaskService ledger, GenerationLeaseService leases) {
        return application(ledger, leases, mock(AiGenerationGateway.class), mock(ChatHistoryService.class));
    }

    @Test
    void realSchemaEnforcesBinaryRequestIdentityStateAndConcurrentTerminalCas() throws Exception {
        String id = request();
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, 1);
        String upper = id.toUpperCase(java.util.Locale.ROOT);
        requests.add(upper);
        tasks.start(appId, USER, upper, CodeGenTypeEnum.HTML, 2);
        assertEquals("GENERATION_REQUEST_EXISTS", assertThrows(GenerationStreamException.class,
                () -> tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, 3)).getErrorCode());
        try (var connection = resources.connection(); var statement = connection.prepareStatement(
                "UPDATE generation_task SET execution_state = 'INVALID' WHERE request_id = ?")) {
            statement.setString(1, id);
            assertThrows(java.sql.SQLException.class, statement::executeUpdate);
        }
        String owner = tasks.findOwned(appId, USER, id).getExecutionOwner();
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch begin = new CountDownLatch(1);
        try (var workers = Executors.newFixedThreadPool(2)) {
            List<Future<Integer>> results = new ArrayList<>();
            for (String state : List.of("SUCCEEDED", "FAILED")) {
                results.add(workers.submit(() -> {
                    ready.countDown();
                    if (!begin.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("测试同步超时");
                    var now = LocalDateTime.now();
                    return resources.mapper.finishTask(id, state, null, now, now.plusDays(30), owner, 1L);
                }));
            }
            assertTrue(ready.await(5, TimeUnit.SECONDS));
            begin.countDown();
            assertEquals(1, results.get(0).get(10, TimeUnit.SECONDS) + results.get(1).get(10, TimeUnit.SECONDS));
        }
        assertEquals("RUNNING", tasks.findOwned(appId, USER, upper).getExecutionState());
        assertNull(resources.mapper.findOwned(appId, USER + 1, id, LocalDateTime.now()));
    }

    @Test
    void acknowledgedInsertLossLeavesQueryableRunningTaskButNeverInvokesModel() {
        var mapper = faultyMapper();
        doAnswer(call -> {
            com.yupi.yuaicodemother.model.entity.GenerationTask task = call.getArgument(0);
            requests.add(task.getRequestId());
            resources.mapper.createTask(task);
            // 数据已真实提交，注入“调用方未收到确认”的应用层异常，不停止共享数据库。
            throw new TransientDataAccessResourceException("模拟插入确认丢失");
        }).when(mapper).createTask(any());
        var ledger = new GenerationTaskServiceImpl(mapper);
        var gateway = mock(AiGenerationGateway.class);
        var history = mock(ChatHistoryService.class);
        var service = application(ledger, first, gateway, history);
        var error = assertThrows(GenerationStreamException.class,
                () -> service.chatToGenCode(appId, "测试页面", SysUser.builder().id(USER).build()).blockLast());
        assertEquals("GENERATION_TASK_UNAVAILABLE", error.getErrorCode());
        assertEquals("RUNNING", tasks.findOwned(appId, USER, error.getRequestId()).getExecutionState());
        assertEquals(GenerationLeaseService.PublicState.IDLE, second.getStatus(appId).state());
        var status = reader(tasks, second).getGenerationTask(appId, error.getRequestId(), SysUser.builder().id(USER).build());
        assertEquals("UNKNOWN", status.getLeaseState());
        assertEquals("NOT_CONFIRMED", status.getPublicationState());
        verifyNoInteractions(gateway, history);
    }

    @Test
    void durableCancellationStillRejectsPublishWhenLedgerCancellationWriteFails() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        var mapper = faultyMapper();
        doThrow(new TransientDataAccessResourceException("模拟取消落账失败")).when(mapper).recordCancellation(eq(id), any());
        var gateway = mock(AiGenerationGateway.class);
        var service = application(new GenerationTaskServiceImpl(mapper), second, gateway, mock(ChatHistoryService.class));
        assertEquals("STOPPING", service.cancelGeneration(appId, SysUser.builder().id(USER).build()).getState());
        verify(gateway).cancel(appId, USER, id);
        assertNotNull(tasks.findOwned(appId, USER, id).getCancelRequestedAt());
        AtomicBoolean published = new AtomicBoolean();
        assertEquals("GENERATION_CANCELLED", assertThrows(GenerationStreamException.class,
                () -> first.commit(appId, id, () -> { published.set(true); return null; })).getErrorCode());
        assertFalse(published.get());
        tasks.finish(id, "CANCELLED", "GENERATION_CANCELLED");
        tasks.recordCancellation(id);
        assertNotNull(tasks.findOwned(appId, USER, id).getCancelRequestedAt());
        first.release(lease);
        var status = reader(tasks, second).getGenerationTask(appId, id, SysUser.builder().id(USER).build());
        assertEquals("CANCELLED", status.getExecutionState());
        assertEquals("UNKNOWN", status.getLeaseState());
        assertEquals("NOT_CONFIRMED", status.getPublicationState());
    }

    @Test
    void durableCancellationRejectsActivationAfterRedisMirrorIsLost() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        assertTrue(second.cancelActive(appId).cancellationRequested());
        resources.first.getBucket("ai:generation:state:" + appId).set(id + ":ACTIVE");
        var restarted = new GenerationFenceService(resources.newFenceMapper(), new ObjectMapper(),
                new DataSourceTransactionManager(resources.dataSource()));
        assertEquals("GENERATION_CANCELLED", assertThrows(GenerationStreamException.class,
                () -> restarted.activate(appId, "html", id, lease.fenceToken(), lease.fenceToken(),
                        Map.of("index.html", "hash"))).getErrorCode());
        AtomicBoolean invoked = new AtomicBoolean();
        assertEquals("GENERATION_CANCELLED", assertThrows(GenerationStreamException.class,
                () -> first.commit(appId, id, () -> { invoked.set(true); return null; })).getErrorCode());
        assertFalse(invoked.get());
        assertNull(fences.active(appId, "html"));
        assertNull(fences.publication(appId, "html", id));
        assertNotNull(tasks.findOwned(appId, USER, id).getCancelRequestedAt());
        first.release(lease);
    }

    @Test
    void realExecutionHeartbeatUsesOwnerAndDatabaseDeadlineWithoutResurrectingExpiredWork() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        var initial = tasks.findOwned(appId, USER, id);
        String owner = initial.getExecutionOwner();
        assertNotNull(owner);
        assertEquals("LIVE", initial.getHeartbeatState());
        assertEquals(90, java.time.Duration.between(initial.getHeartbeatAt(), initial.getExecutionExpiresAt()).toSeconds());
        assertEquals(0, resources.mapper.heartbeat(id, appId, UUID.randomUUID().toString(), lease.fenceToken()));
        assertEquals(0, resources.mapper.heartbeat(id, appId, owner, lease.fenceToken() + 1));
        assertEquals(1, resources.mapper.heartbeat(id, appId, owner, lease.fenceToken()));
        var rebuilt = new GenerationTaskServiceImpl(resources.newMapper());
        rebuilt.finish(id, "FAILED", "OLD_OWNER");
        assertEquals("RUNNING", tasks.findOwned(appId, USER, id).getExecutionState());
        try (var connection = resources.connection(); var expire = connection.prepareStatement(
                "UPDATE generation_task SET execution_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP(6)) WHERE app_id = ? AND request_id = ?")) {
            expire.setLong(1, appId);
            expire.setString(2, id);
            assertEquals(1, expire.executeUpdate());
        }
        tasks.heartbeatOwnedExecutions();
        assertEquals(0, resources.mapper.heartbeat(id, appId, owner, lease.fenceToken()));
        assertEquals("EXPIRED", rebuilt.findOwned(appId, USER, id).getHeartbeatState());
        assertEquals("RUNNING", rebuilt.findOwned(appId, USER, id).getExecutionState());
        // 心跳期限不是发布/终态授权；同一真实执行到终态后仍能条件落账，查询不会自动续跑。
        tasks.finish(id, "CANCELLED", "GENERATION_CANCELLED");
        assertEquals("TERMINAL", rebuilt.findOwned(appId, USER, id).getHeartbeatState());
        assertEquals("CANCELLED", rebuilt.findOwned(appId, USER, id).getExecutionState());
        first.release(lease);
    }

    @Test
    void realSuccessorGenerationSupersedesHeartbeatWithoutRewritingOldExecutionOutcome() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        first.release(lease);
        var successor = second.acquire(appId, request());
        tasks.heartbeatOwnedExecutions();
        assertEquals("SUPERSEDED", tasks.findOwned(appId, USER, id).getHeartbeatState());
        assertEquals("RUNNING", tasks.findOwned(appId, USER, id).getExecutionState());
        assertThrows(GenerationStreamException.class, () -> fences.activate(appId, "html", id,
                lease.fenceToken(), lease.fenceToken(), Map.of("index.html", "hash")));
        second.release(successor);
    }

    @Test
    void committedFilesAndRunningLedgerRemainSeparateAfterTerminalWriteFailureAndRebuild() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        var mapper = faultyMapper();
        var ownerLedger = new GenerationTaskServiceImpl(mapper);
        ownerLedger.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        assertTrue(first.commit(appId, id, () -> store.publishFenced(CodeGenTypeEnum.HTML, appId, id,
                Map.of("index.html", "<html>测试</html>"), "integration", "STOP", null,
                lease.fenceToken(), fences)).published());
        doThrow(new TransientDataAccessResourceException("模拟终态落账失败")).when(mapper)
                .finishTask(eq(id), anyString(), any(), any(), any(), anyString(), anyLong());
        assertDoesNotThrow(() -> ownerLedger.finish(id, "SUCCEEDED", null));
        first.release(lease);
        // 重建 Mapper/任务服务，使用另一 Redis 客户端；不借助旧实例的请求内存。
        var rebuiltLedger = new GenerationTaskServiceImpl(resources.newMapper());
        var rebuilt = reader(rebuiltLedger, second);
        var before = rebuiltLedger.findOwned(appId, USER, id).getUpdatedAt();
        var status = rebuilt.getGenerationTask(appId, id, SysUser.builder().id(USER).build());
        assertEquals("RUNNING", status.getExecutionState());
        assertEquals("UNKNOWN", status.getLeaseState());
        assertEquals("COMMITTED", status.getPublicationState());
        assertEquals(before, rebuiltLedger.findOwned(appId, USER, id).getUpdatedAt());
        assertEquals(id, Files.readString(root.resolve("html_" + appId).resolve(".current")));
    }

    @Test
    void lostLeaseWithStaleActiveStateCannotPublishOrAppearRunning() throws Exception {
        String id = request();
        var lease = first.acquire(appId, id);
        tasks.start(appId, USER, id, CodeGenTypeEnum.HTML, lease.fenceToken());
        assertTrue(lease.lock().forceUnlock());
        AtomicBoolean invoked = new AtomicBoolean();
        assertEquals("GENERATION_LEASE_LOST", assertThrows(GenerationStreamException.class,
                () -> first.commit(appId, id, () -> { invoked.set(true); return null; })).getErrorCode());
        assertFalse(invoked.get());
        var status = reader(tasks, second).getGenerationTask(appId, id, SysUser.builder().id(USER).build());
        assertEquals("RUNNING", status.getExecutionState());
        assertEquals("UNKNOWN", status.getLeaseState());
        assertEquals("NOT_CONFIRMED", status.getPublicationState());
    }

    @Test
    void newerDatabaseFenceRejectsPausedOldPublisherAndIgnoresLateFileMirror() throws Exception {
        String oldId = request();
        var oldLease = first.acquire(appId, oldId);
        tasks.start(appId, USER, oldId, CodeGenTypeEnum.HTML, oldLease.fenceToken());
        assertTrue(oldLease.lock().forceUnlock());

        String newId = request();
        var newLease = second.acquire(appId, newId);
        tasks.start(appId, USER, newId, CodeGenTypeEnum.HTML, newLease.fenceToken());
        second.commit(appId, newId, () -> store.publishFenced(CodeGenTypeEnum.HTML, appId, newId,
                Map.of("index.html", "<html>新版本</html>"), "integration", "STOP", null,
                newLease.fenceToken(), fences));

        var stale = assertThrows(GenerationStreamException.class,
                () -> store.publishFenced(CodeGenTypeEnum.HTML, appId, oldId,
                        Map.of("index.html", "<html>旧版本</html>"), "integration", "STOP", null,
                        oldLease.fenceToken(), fences));
        assertEquals("GENERATION_FENCE_REJECTED", stale.getErrorCode());
        Path appRoot = root.resolve("html_" + appId);
        Files.writeString(appRoot.resolve(".current"), oldId);
        assertEquals(appRoot.resolve(".releases").resolve(newId).toAbsolutePath(),
                resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, appId));
        assertEquals(newId, fences.active(appId, "html").requestId());
        assertNull(fences.publication(appId, "html", oldId));
        second.release(newLease);
        first.release(oldLease);
    }

    @Test
    void publisherPausedInsideFileVerificationCannotActivateAfterSuccessorCommits() throws Exception {
        String oldId = request();
        var oldLease = first.acquire(appId, oldId);
        var verified = new CountDownLatch(1);
        var resume = new CountDownLatch(1);
        // 使用不同 store 模拟两个 Spring 实例；共享临时文件根目录和真实数据库。
        var oldStore = new VersionedArtifactStore(resolver, new ObjectMapper().registerModule(new JavaTimeModule()));
        var paused = CompletableFuture.supplyAsync(() -> {
            try {
                oldStore.publishFenced(CodeGenTypeEnum.HTML, appId, oldId,
                        Map.of("index.html", "<html>paused</html>"), "integration", "STOP", path -> {
                            verified.countDown();
                            if (!resume.await(10, TimeUnit.SECONDS)) throw new IllegalStateException("等待恢复超时");
                        }, oldLease.fenceToken(), fences);
                return "unexpected-success";
            } catch (GenerationStreamException error) {
                return error.getErrorCode();
            } catch (Exception error) {
                throw new CompletionException(error);
            }
        });
        try {
            assertTrue(verified.await(5, TimeUnit.SECONDS));
            oldLease.lock().forceUnlock();
            String newId = request();
            var successor = second.acquire(appId, newId);
            try {
                store.publishFenced(CodeGenTypeEnum.HTML, appId, newId,
                        Map.of("index.html", "<html>successor</html>"), "integration", "STOP", null,
                        successor.fenceToken(), fences);
                resume.countDown();
                assertEquals("GENERATION_FENCE_REJECTED", paused.get(5, TimeUnit.SECONDS));
                assertTrue(resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, appId).endsWith(newId));
            } finally {
                second.release(successor);
            }
        } finally {
            resume.countDown();
            first.release(oldLease);
        }
    }

    @Test
    void successorLeaseAndStateSurviveOldRequestCommitAndRelease() throws Exception {
        String oldId = request();
        var old = first.acquire(appId, oldId);
        tasks.start(appId, USER, oldId, CodeGenTypeEnum.HTML, old.fenceToken());
        assertTrue(old.lock().forceUnlock());
        String newId = request();
        var successor = second.acquire(appId, newId);
        tasks.start(appId, USER, newId, CodeGenTypeEnum.HTML, successor.fenceToken());
        assertEquals("GENERATION_NOT_ACTIVE", assertThrows(GenerationStreamException.class,
                () -> first.commit(appId, oldId, () -> "不可发布")).getErrorCode());
        first.release(old);
        assertEquals(newId, second.getStatus(appId).requestId());
        assertTrue(successor.lock().isLocked());
        assertEquals("有效发布", second.commit(appId, newId, () -> "有效发布"));
        second.release(successor);
    }

    @Test
    void sameClientSameThreadCannotReenterWithDifferentGenerationRequest() {
        var lease = first.acquire(appId, request());
        assertEquals("GENERATION_IN_PROGRESS", assertThrows(GenerationStreamException.class,
                () -> first.acquire(appId, request())).getErrorCode());
        first.release(lease);
    }
}
