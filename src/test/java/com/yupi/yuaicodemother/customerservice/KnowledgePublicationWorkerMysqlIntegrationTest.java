package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.mapper.*;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.service.*;
import com.yupi.yuaicodemother.service.impl.CustomerServiceKnowledgeServiceImpl;
import com.yupi.yuaicodemother.worker.CustomerServiceKnowledgeEtlWorker;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.net.URI;
import java.nio.file.*;
import java.sql.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

import static com.yupi.yuaicodemother.customerservice.KnowledgePublicationMysqlIntegrationTest.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.mockito.AdditionalAnswers.delegatesTo;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 真实 MySQL/管理事务/worker 的发布竞态；候选默认合成，Milvus 复验另需显式开启。 */
@EnabledIfEnvironmentVariable(named="AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION",matches="true")
class KnowledgePublicationWorkerMysqlIntegrationTest {
    private static final ObjectMapper JSON=new ObjectMapper().registerModule(new com.fasterxml.jackson.datatype.jsr310.JavaTimeModule());
    private static final MockMultipartFile FILE=new MockMultipartFile("file","v2.txt","text/plain",new byte[]{2});

    @Test void lateV1WorkerCannotPolluteCommittedV2() throws Exception { lateWorker(false,false); }

    @Test void lateFinallyCannotRevokeLiveReplacementLease() throws Exception { lateWorker(true,false); }

    @Test
    @EnabledIfEnvironmentVariable(named="AI_SERVICE_MILVUS_INTEGRATION",matches="true")
    void lateV1WorkerLeavesRealMilvusReadsOnSameV2Publication() throws Exception { lateWorker(false,true); }

    private void lateWorker(boolean retainReplacementLease,boolean milvus) throws Exception {
        var clock=new MutableClock();
        new KnowledgePublicationMysqlIntegrationTest().isolated(clock,f->{
            f.properties.setLeaseTimeoutSeconds(30);
            f.properties.setClaimTimeoutSeconds(120);
            f.seed(1);
            Path evidence=evidenceDirectory(retainReplacementLease?"replacement":"late-v1");
            try(var candidates=new Candidates(milvus,evidence)) {
                if(milvus) candidates.bootstrap(f);
                var reached=new CountDownLatch(1);var release=new CountDownLatch(1);
                var rejection=new AtomicReference<String>();
                var oldLease=new AtomicReference<KnowledgeMutationCoordinator.Lease>();
                var boundary=delegate(KnowledgePublicationService.class,f.service);
                doAnswer(call->{
                    // 暂停发生在真实事务代理外；管理更新和替代发布不被本例的人为锁阻塞。
                    assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
                    oldLease.set(call.getArgument(2));reached.countDown();await(release);
                    try { return f.service.commit(call.getArgument(0),call.getArgument(1),call.getArgument(2),call.getArgument(3),call.getArgument(4)); }
                    catch(IllegalStateException refused) { rejection.set(refused.getMessage());throw refused; }
                }).when(boundary).commit(anyLong(),anyString(),any(),any(),any());
                var a=worker(f,clock,boundary,candidates.client);
                var b=worker(f,clock,f.service,candidates.client);
                var pool=Executors.newSingleThreadExecutor();
                var running=pool.submit(a.worker::poll);
                KnowledgeMutationCoordinator.Lease replacement=null;
                try {
                    await(reached);
                    assertEquals(1,candidates.calls.get());
                    assertEquals(1,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
                    // 只推进签发方与worker共享的时钟；不改租约/任务状态来绕过业务认领规则。
                    clock.advance(Duration.ofSeconds(31));
                    assertFalse(f.coordinator.validate(oldLease.get()).current());
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='PROCESSING'"));
                    administrator(f,oss()).upload(FILE,1L,8);
                    long taskB=f.jdbc.queryForObject("SELECT id FROM customer_service_knowledge_etl_outbox WHERE documentId=1 AND documentVersion=2",Long.class);
                    b.worker.poll();
                    assertEquals(2,candidates.calls.get());
                    var snapshot=f.service.snapshot();
                    assertEquals("task_"+taskB,snapshot.publicationId());assertEquals(3,snapshot.epoch());
                    assertEquals(2,snapshot.documents().getFirst().documentVersion());
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id="+taskB+" AND status='SUCCEEDED' AND retryCount=0 AND lastErrorCode IS NULL"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND documentVersion=2 AND etlVersion=3 AND indexedVersion=2 AND indexedEtlVersion=3 AND activationRevision=1 AND indexedActivationRevision=1 AND status='ACTIVE' AND lastErrorCode IS NULL AND contentHash='"+"b".repeat(64)+"' AND objectKey='isolated/v2.txt'"));
                    if(retainReplacementLease) {
                        replacement=f.coordinator.acquire("document:1","live_replacement","INDEX","replacement");
                        assertTrue(f.coordinator.validate(replacement).current());
                    }
                    Map<String,Object> before=independentState(f,taskB);
                    JSON.writeValue(evidence.resolve("before-a-release.json").toFile(),before);
                    if(milvus) candidates.read(snapshot);
                    release.countDown();running.get(20,TimeUnit.SECONDS);
                    assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",rejection.get(),"旧claim仍有效而租约已过期，必须在租约边界明确拒绝");
                    Map<String,Object> after=independentState(f,taskB);
                    JSON.writeValue(evidence.resolve("after-a-finally.json").toFile(),after);
                    assertEquals(before,after,"独立连接观察到的B状态和全部不可变事实必须逐列一致");
                    assertEquals(snapshot,f.service.snapshot());
                    assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                    assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox WHERE publicationId='task_1'"));
                    assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",f.jdbc.queryForObject("SELECT lastErrorCode FROM customer_service_knowledge_etl_outbox WHERE id=1",String.class));
                    verify(a.tasks).retry(eq(1L),anyString(),eq("PENDING"),eq(1),any(),eq("KNOWLEDGE_PUBLICATION_LEASE_INVALID"));
                    verify(a.coordinator).revoke(oldLease.get());
                    assertFalse(f.coordinator.validate(oldLease.get()).current());
                    if(replacement!=null) assertTrue(f.coordinator.validate(replacement).current(),"旧finally不能撤销独立operationId/fence的有效替代租约");
                    if(milvus) candidates.read(snapshot);
                    JSON.writeValue(evidence.resolve("result.json").toFile(),Map.of("epoch",snapshot.epoch(),"head",snapshot.publicationId(),"oldError",rejection.get(),"milvus",milvus));
                } finally {
                    release.countDown();
                    try {running.get(20,TimeUnit.SECONDS);} finally {pool.shutdownNow();assertTrue(pool.awaitTermination(5,TimeUnit.SECONDS));}
                    if(replacement!=null) f.coordinator.revoke(replacement);
                }
            }
        });
    }

    @Test void epochConflictHasCurrentClaimLeaseAndUnchangedDocumentIdentity() throws Exception {
        new KnowledgePublicationMysqlIntegrationTest().isolated(f->{
            var a=f.prepare(1);
            f.service.beforeAdministrativeMutation();
            assertTrue(f.coordinator.validate(a.lease()).current());
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND processingOwner='owner' AND status='PROCESSING' AND processingDeadline>'2030-01-01'"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND documentVersion=1 AND etlVersion=2 AND status='INDEXING'"));
            Map<String,Object> before=independentState(f,1);
            assertEquals("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT",assertThrows(IllegalStateException.class,()->
                    f.service.commit(1,"owner",a.lease(),a.context(),a.receipt())).getMessage());
            assertEquals(before,independentState(f,1));
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox"));
        });
    }

    @Test void administrativeReplacementWaitsForRealPublicationRowLock() throws Exception {
        var clock=new MutableClock();
        new KnowledgePublicationMysqlIntegrationTest().isolated(clock,f->{
            var a=f.prepare(1);var reached=new CountDownLatch(1);var release=new CountDownLatch(1);
            var lockMapper=delegate(KnowledgePublicationMapper.class,f.publicationMapper);
            var connectionA=new AtomicLong();var connectionB=new AtomicLong();
            doAnswer(call->{
                var state=f.publicationMapper.lockState();
                assertTrue(TransactionSynchronizationManager.isActualTransactionActive());
                connectionA.set(f.jdbc.queryForObject("SELECT CONNECTION_ID()",Long.class));
                reached.countDown();await(release);return state;
            }).when(lockMapper).lockState();
            var publisher=f.transactional(new KnowledgePublicationService(lockMapper,f.documentMapper,f.tasks,f.leaseMapper,f.coordinator,f.properties,clock));
            var managerOss=oss();var enteringB=new CountDownLatch(1);
            doAnswer(call->{
                connectionB.set(f.jdbc.queryForObject("SELECT CONNECTION_ID()",Long.class));enteringB.countDown();
                return uploaded();
            }).when(managerOss).uploadKnowledgeDocument(any());
            var administrator=administrator(f,managerOss);
            var pool=Executors.newFixedThreadPool(2);
            Future<PublicationFact> publishing=null;Future<?> updating=null;
            Path evidence=evidenceDirectory("row-lock");
            try {
                publishing=pool.submit(()->publisher.commit(1,"owner",a.lease(),a.context(),a.receipt()));
                await(reached);
                updating=pool.submit(()->administrator.upload(FILE,1L,8));await(enteringB);
                var wait=observeLockWait(f,connectionA.get(),connectionB.get());
                JSON.writeValue(evidence.resolve("mysql-lock-wait.json").toFile(),wait);
                assertNotEquals(connectionA.get(),connectionB.get());
                release.countDown();
                assertEquals(2,publishing.get(20,TimeUnit.SECONDS).epoch());updating.get(20,TimeUnit.SECONDS);
                assertEquals(3,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
                assertEquals("task_1",f.service.snapshot().publicationId());
                assertEquals(1,f.service.snapshot().documents().getFirst().documentVersion());
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND documentVersion=2 AND etlVersion=3 AND indexedVersion=1 AND indexedEtlVersion=2 AND status='UPLOADED'"));
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE documentVersion=2 AND status='PENDING'"));
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1' AND epoch=2"));
                JSON.writeValue(evidence.resolve("after-commit-order.json").toFile(),independentState(f,1));
            } finally {
                release.countDown();
                try { if(publishing!=null) publishing.get(20,TimeUnit.SECONDS);if(updating!=null) updating.get(20,TimeUnit.SECONDS); }
                finally {pool.shutdownNow();assertTrue(pool.awaitTermination(5,TimeUnit.SECONDS));}
            }
        });
    }

    @Test void committedButAcknowledgementLostReconcilesExactFactWithoutRepeatingPrepare() throws Exception { unknownAcknowledgement(false); }

    @Test void committedButFactLookupUnavailableNeverWritesFailure() throws Exception { unknownAcknowledgement(true); }

    @Test void unknownCommitAndUnavailableFactPreserveProcessingWithoutRetry() throws Exception {
        var clock=new MutableClock();
        new KnowledgePublicationMysqlIntegrationTest().isolated(clock,f->{
            f.seed(1);Path evidence=evidenceDirectory("uncommitted-unknown");
            try(var candidates=new Candidates(false,evidence)) {
                var boundary=delegate(KnowledgePublicationService.class,f.service);
                var attempted=new AtomicBoolean();
                doAnswer(call->{attempted.set(true);throw new DataAccessResourceFailureException("验收注入：提交结果未知");})
                        .when(boundary).commit(anyLong(),anyString(),any(),any(),any());
                doAnswer(call->{
                    if(attempted.get()) throw new DataAccessResourceFailureException("验收注入：事实读取不可用");
                    return f.service.findTaskFact(call.getArgument(0));
                }).when(boundary).findTaskFact(anyLong());
                var worker=worker(f,clock,boundary,candidates.client);worker.worker.poll();
                assertEquals(1,candidates.calls.get());
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='PROCESSING' AND retryCount=0 AND lastErrorCode IS NULL AND processingOwner IS NOT NULL"));
                assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox"));
                verify(worker.tasks,never()).retry(anyLong(),anyString(),anyString(),anyInt(),any(),any());
                verify(worker.finalizer,never()).failIndex(anyLong(),anyString(),anyLong(),anyLong(),anyLong(),anyInt(),any(),anyString());
                verify(boundary,times(1)).commit(anyLong(),anyString(),any(),any(),any());
                JSON.writeValue(evidence.resolve("processing-preserved.json").toFile(),independentState(f,1));
            }
        });
    }

    private void unknownAcknowledgement(boolean factUnavailable) throws Exception {
        var clock=new MutableClock();
        new KnowledgePublicationMysqlIntegrationTest().isolated(clock,f->{
            f.seed(1);Path evidence=evidenceDirectory(factUnavailable?"unknown-fact":"matched-fact");
            try(var candidates=new Candidates(false,evidence)) {
                var boundary=delegate(KnowledgePublicationService.class,f.service);
                var commits=new AtomicInteger();var committed=new AtomicBoolean();
                doAnswer(call->{
                    PublicationFact fact=f.service.commit(call.getArgument(0),call.getArgument(1),call.getArgument(2),call.getArgument(3),call.getArgument(4));
                    assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
                    assertEquals(fact.receipt(),f.service.findTaskFact(1).receipt());
                    committed.set(true);commits.incrementAndGet();
                    // 先让真实代理提交，再模拟驱动丢失提交确认；不是在事务中抛异常导致回滚。
                    throw new DataAccessResourceFailureException("验收注入：提交确认丢失");
                }).when(boundary).commit(anyLong(),anyString(),any(),any(),any());
                if(factUnavailable) doAnswer(call->{
                    if(committed.get()) throw new DataAccessResourceFailureException("验收注入：事实读取不可用");
                    return f.service.findTaskFact(call.getArgument(0));
                }).when(boundary).findTaskFact(anyLong());
                var worker=worker(f,clock,boundary,candidates.client);worker.worker.poll();
                assertTrue(committed.get());
                assertEquals(1,commits.get());assertEquals(1,candidates.calls.get());
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='SUCCEEDED' AND retryCount=0 AND lastErrorCode IS NULL"));
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1' AND epoch=2"));
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox WHERE publicationId='task_1'"));
                assertEquals(2,f.service.snapshot().epoch());
                verify(worker.tasks,never()).retry(anyLong(),anyString(),anyString(),anyInt(),any(),any());
                verify(worker.finalizer,never()).failIndex(anyLong(),anyString(),anyLong(),anyLong(),anyLong(),anyInt(),any(),anyString());
                worker.worker.poll();
                assertEquals(1,candidates.calls.get());assertEquals(1,commits.get());assertEquals(2,f.service.snapshot().epoch());
                verify(boundary,times(2)).findTaskFact(1);
                JSON.writeValue(evidence.resolve("committed-state.json").toFile(),independentState(f,1));
            }
        });
    }

    private static Worker worker(Fixture f,Clock clock,KnowledgePublicationService publication,CustomerServiceAiClient client) {
        var tasks=delegate(CustomerServiceKnowledgeEtlOutboxMapper.class,f.tasks);
        var coordinator=delegate(KnowledgeMutationCoordinator.class,f.coordinator);
        var target=new CustomerServiceKnowledgeTaskFinalizer(f.documentMapper,tasks);
        ReflectionTestUtils.setField(target,"publicationService",f.service);
        var finalizer=f.transactional(target);
        var countedFinalizer=delegate(CustomerServiceKnowledgeTaskFinalizer.class,finalizer);
        var worker=new CustomerServiceKnowledgeEtlWorker(f.documentMapper,tasks,oss(),client,coordinator,countedFinalizer,f.properties,clock);
        ReflectionTestUtils.setField(worker,"publicationService",publication);
        return new Worker(worker,tasks,coordinator,countedFinalizer);
    }

    private static CustomerServiceKnowledgeServiceImpl administrator(Fixture f,OssManager oss) {
        var target=new CustomerServiceKnowledgeServiceImpl(f.documentMapper,f.tasks,oss);
        ReflectionTestUtils.setField(target,"publicationService",f.service);return f.transactional(target);
    }

    private static OssManager oss() {
        var oss=mock(OssManager.class);
        try {when(oss.generateKnowledgeDownloadUrl(anyString())).thenReturn(URI.create("http://localhost/isolated.txt").toURL());}
        catch(java.net.MalformedURLException invalid) {throw new IllegalStateException(invalid);}
        when(oss.uploadKnowledgeDocument(any())).thenReturn(uploaded());return oss;
    }

    private static OssManager.KnowledgeObject uploaded() {
        return new OssManager.KnowledgeObject("isolated/v2.txt","v2.txt","txt",1,"b".repeat(64));
    }

    private static <T> T delegate(Class<T> type,T real) { return mock(type,delegatesTo(real)); }

    private static void await(CountDownLatch latch) throws InterruptedException {
        assertTrue(latch.await(45,TimeUnit.SECONDS),"并发暂停点必须有界到达/放行");
    }

    private static Path evidenceDirectory(String label) throws Exception {
        Path parent=Path.of("tmp");Files.createDirectories(parent);
        return Files.createTempDirectory(parent,"rag-publication-worker-"+label+"-").toAbsolutePath();
    }

    /** 每次从新连接读取；不继承事务视图，也不通过service读补写任何发布事实。 */
    private static Map<String,Object> independentState(Fixture f,long taskB) throws Exception {
        String schema=f.jdbc.queryForObject("SELECT DATABASE()",String.class);
        try(var connection=KnowledgeRevisionMysqlSupport.connect()) {
            connection.setCatalog(schema);assertEquals(schema,connection.getCatalog());
            var jdbc=new JdbcTemplate(new SingleConnectionDataSource(connection,true));
            Map<String,Object> result=new LinkedHashMap<>();
            for(String table:List.of("publication_state","document","content_revision","publication","alias_outbox"))
                result.put(table,jdbc.queryForList("SELECT * FROM customer_service_knowledge_"+table+" ORDER BY 1"));
            result.put("taskB",jdbc.queryForList("SELECT * FROM customer_service_knowledge_etl_outbox WHERE id=?",taskB));
            result.put("anchorB",jdbc.queryForList("SELECT * FROM customer_service_knowledge_prepare_anchor WHERE taskId=?",taskB));
            result.put("executionB",jdbc.queryForList("SELECT * FROM customer_service_knowledge_prepare_execution WHERE taskId=?",taskB));
            return result;
        }
    }

    private static Map<String,Object> observeLockWait(Fixture f,long blocker,long waiter) throws Exception {
        String schema=f.jdbc.queryForObject("SELECT DATABASE()",String.class);
        List<String> queries=List.of(
            "SELECT w.REQUESTING_THREAD_ID,w.BLOCKING_THREAD_ID,r.OBJECT_SCHEMA,r.OBJECT_NAME,r.LOCK_STATUS FROM performance_schema.data_lock_waits w JOIN performance_schema.threads a ON a.THREAD_ID=w.BLOCKING_THREAD_ID JOIN performance_schema.threads b ON b.THREAD_ID=w.REQUESTING_THREAD_ID JOIN performance_schema.data_locks r ON r.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID AND r.ENGINE=w.ENGINE WHERE a.PROCESSLIST_ID="+blocker+" AND b.PROCESSLIST_ID="+waiter+" AND r.OBJECT_SCHEMA='"+schema+"' AND r.OBJECT_NAME='customer_service_knowledge_publication_state'",
            "SELECT w.requesting_trx_id,w.blocking_trx_id,l.lock_table FROM information_schema.INNODB_LOCK_WAITS w JOIN information_schema.INNODB_TRX a ON a.trx_id=w.blocking_trx_id JOIN information_schema.INNODB_TRX b ON b.trx_id=w.requesting_trx_id JOIN information_schema.INNODB_LOCKS l ON l.lock_id=w.requested_lock_id WHERE a.trx_mysql_thread_id="+blocker+" AND b.trx_mysql_thread_id="+waiter+" AND l.lock_table LIKE '%"+schema+"%customer_service_knowledge_publication_state%'"
        );
        String supported=null;
        for(String query:queries) try {f.jdbc.queryForList(query);supported=query;break;} catch(org.springframework.dao.DataAccessException unavailable) { /* 降级只尝试兼容锁视图，不把Future未完成当作锁证据。 */ }
        assumeTrue(supported!=null,"SKIP_LOCK_OBSERVATION_UNAVAILABLE：MySQL锁等待视图不存在或当前账户无权限，未宣称事务内等待覆盖");
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(12);
        while(System.nanoTime()<deadline) {
            var rows=f.jdbc.queryForList(supported);
            if(!rows.isEmpty()) return Map.of("blocker",blocker,"waiter",waiter,"rows",rows);
            Thread.sleep(25);
        }
        fail("真实MySQL锁等待视图未观察到管理连接等待发布state行锁");return Map.of();
    }

    private record Worker(CustomerServiceKnowledgeEtlWorker worker,CustomerServiceKnowledgeEtlOutboxMapper tasks,
                          KnowledgeMutationCoordinator coordinator,CustomerServiceKnowledgeTaskFinalizer finalizer) { }

    private static final class MutableClock extends Clock {
        private final AtomicReference<Instant> current=new AtomicReference<>(NOW);
        void advance(Duration duration) {current.updateAndGet(value->value.plus(duration));}
        @Override public ZoneId getZone() {return ZoneOffset.UTC;}
        @Override public Clock withZone(ZoneId zone) {if(!zone.equals(ZoneOffset.UTC)) throw new IllegalArgumentException("测试只允许UTC");return this;}
        @Override public Instant instant() {return current.get();}
    }

    private static final class Candidates implements AutoCloseable {
        final CustomerServiceAiClient client=mock(CustomerServiceAiClient.class);
        final AtomicInteger calls=new AtomicInteger();
        final AtomicReference<KnowledgePublicationOwnedProcess> activeProcess=new AtomicReference<>();
        final boolean milvus;final Path directory;
        Candidates(boolean milvus,Path directory) throws Exception {
            this.milvus=milvus;this.directory=directory;
            when(client.preparePublication(any())).thenAnswer(call->{
                CandidateRequest request=call.getArgument(0);calls.incrementAndGet();
                if(milvus) {
                    Path input=directory.resolve("candidate-"+calls.get()+".json");JSON.writeValue(input.toFile(),request);
                    return JSON.readValue(run("candidate",input),CandidateReceipt.class);
                }
                var lease=((CustomerServiceAiClient.IndexRequest)request.mutation()).lease();
                return new CandidateReceipt(request.context().publicationId(),request.context().baseEpoch(),lease.operationId(),lease.fence(),
                        "customer_service_knowledge_pub_"+UUID.randomUUID().toString().replace("-",""),HASH,"model-v1",3,
                        request.context().documents().stream().map(d->new ReceiptDocument(d.documentId(),d.documentVersion(),d.indexRevision(),d.activationRevision(),d.sha256(),d.fileName(),d.fileType(),1)).toList());
            });
        }
        void bootstrap(Fixture f) throws Exception {
            CandidateReceipt initial=JSON.readValue(run("bootstrap",null),CandidateReceipt.class);
            f.properties.setCollectionAlias(initial.candidateCollection().substring(0,initial.candidateCollection().lastIndexOf("_pub_")));
            assertEquals(1,f.jdbc.update("UPDATE customer_service_knowledge_publication SET collectionName=?,receiptJson=? WHERE epoch=1",initial.candidateCollection(),JSON.writeValueAsString(initial)));
        }
        void read(ReadSnapshot snapshot) throws Exception {
            Path input=directory.resolve("read-snapshot.json");JSON.writeValue(input.toFile(),snapshot);
            assertEquals("B_READ_CONFIRMED",run("read",input).strip());
        }
        private String run(String action,Path input) throws Exception {
            Path output=directory.resolve(action+"-"+System.nanoTime()+".out");
            Path python=KnowledgePublicationOwnedProcess.python();
            // 避免uv包装层；Windows虚拟环境本身仍有启动器，交由受控进程树夹具排空全部后代。
            List<String> command=new ArrayList<>(List.of(python.toString(),"-X","utf8","tests/publication_worker_milvus_runner.py",action,directory.toString()));
            if(input!=null) command.add(input.toString());
            var process=KnowledgePublicationOwnedProcess.start(new ProcessBuilder(command).directory(Path.of("ai-service").toFile())
                    .redirectError(directory.resolve(action+"-error.log").toFile()).redirectOutput(output.toFile()));
            activeProcess.set(process);
            try {assertTrue(process.await(Duration.ofSeconds(90)),"Milvus子进程必须有界完成");assertEquals(0,process.exitValue(),"查看tmp中的隔离Milvus子进程报告");return Files.readString(output);}
            finally {
                process.terminateAndConfirm();
                activeProcess.compareAndSet(process,null);
            }
        }
        @Override public void close() throws Exception {
            var process=activeProcess.get();
            // 无法确认进程退出时保留随机资源和证据，禁止一边清理一边容许迟到写入。
            assertNull(process,"隔离进程树未确认全部退出，禁止启动Milvus清理");
            if(milvus) run("cleanup",null);
        }
    }
}
