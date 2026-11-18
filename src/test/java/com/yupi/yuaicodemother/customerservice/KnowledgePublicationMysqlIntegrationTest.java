package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.*;
import com.yupi.yuaicodemother.mapper.*;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.service.*;
import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.mapping.Environment;
import com.mybatisflex.core.mybatis.FlexConfiguration;
import com.mybatisflex.core.mybatis.FlexSqlSessionFactoryBuilder;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.mybatis.spring.SqlSessionTemplate;
import org.mybatis.spring.transaction.SpringManagedTransactionFactory;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.*;
import org.springframework.transaction.annotation.AnnotationTransactionAttributeSource;
import org.springframework.transaction.interceptor.TransactionInterceptor;
import java.nio.file.*;
import java.sql.*;
import java.time.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** 随机独立 schema 中使用真实 Mapper/事务；不改业务库，不调用模型或 Milvus。 */
@EnabledIfEnvironmentVariable(named="AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION",matches="true")
class KnowledgePublicationMysqlIntegrationTest {
    @Test void observationUsesOwnedTaskAndDoesNotWaitForExecutionRowLock() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);
            var cfgMapperField=KnowledgePublicationService.class.getDeclaredField("mapper");cfgMapperField.setAccessible(true);
            // 从独立只读Mapper查询，不借助仲裁的FOR UPDATE读路径。
            var target=((org.springframework.aop.framework.Advised)f.service).getTargetSource().getTarget();
            var mapper=(KnowledgePublicationMapper)cfgMapperField.get(target);
            var observer=new KnowledgePrepareObservationService(mapper,Clock.fixed(NOW,ZoneOffset.UTC));
            assertEquals("NOT_REGISTERED",observer.observe(1,1).observationState());
            assertThrows(com.yupi.yuaicodemother.exception.BusinessException.class,()->observer.observe(2,1));
            f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"BEGIN",null));
            try(var locked=KnowledgeRevisionMysqlSupport.connect()) {
                locked.setCatalog(f.jdbc.queryForObject("SELECT DATABASE()",String.class));locked.setAutoCommit(false);
                try(var statement=locked.createStatement()) {statement.executeQuery("SELECT taskId FROM customer_service_knowledge_prepare_execution WHERE taskId=1 FOR UPDATE").close();}
                try(var pool=java.util.concurrent.Executors.newSingleThreadExecutor()) {
                    var observation=pool.submit(()->observer.observe(1,1)).get(5,java.util.concurrent.TimeUnit.SECONDS);
                    assertEquals("BUSY",observation.observationState());
                    assertEquals(NOW.plusSeconds(300),observation.observationDeadline());
                } finally {locked.rollback();}
            }
            assertEquals("STARTED",f.jdbc.queryForObject("SELECT status FROM customer_service_knowledge_prepare_execution WHERE taskId=1",String.class));
        });
    }
    @Test void grantedExecutorCannotCompleteAfterEpochRevocation() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            assertTrue(f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"BEGIN",null)).granted());
            f.service.beforeAdministrativeMutation();
            assertEquals("KNOWLEDGE_PREPARE_CONTEXT_CONFLICT",assertThrows(IllegalStateException.class,()->
                    f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"COMPLETE",execution.receipt()))).getMessage());
            assertEquals("STARTED",f.jdbc.queryForObject("SELECT status FROM customer_service_knowledge_prepare_execution WHERE taskId=1",String.class));
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
        });
    }
    @Test void grantedExecutorCannotCompleteAfterLeaseRevocation() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            assertTrue(f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"BEGIN",null)).granted());
            f.coordinator.revoke(execution.lease());
            assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",assertThrows(IllegalStateException.class,()->
                    f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"COMPLETE",execution.receipt()))).getMessage());
            assertEquals("STARTED",f.jdbc.queryForObject("SELECT status FROM customer_service_knowledge_prepare_execution WHERE taskId=1",String.class));
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
        });
    }
    @Test void preparationExecutionNeverRegrantsAndCompletesIdempotently() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);
            f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            var begin=new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"BEGIN",null);
            assertEquals(new PrepareExecutionResult(true,"STARTED"),f.service.arbitratePrepare(begin));
            assertEquals(new PrepareExecutionResult(false,"BUSY"),f.service.arbitratePrepare(begin));
            var other=new PrepareExecutionRequest(execution.context(),execution.lease(),"b".repeat(32),"BEGIN",null);
            assertEquals(new PrepareExecutionResult(false,"BUSY"),f.service.arbitratePrepare(other));
            f.jdbc.update("UPDATE customer_service_knowledge_prepare_execution SET observationDeadline='2029-01-01' WHERE taskId=1");
            assertEquals(new PrepareExecutionResult(false,"UNKNOWN"),f.service.arbitratePrepare(other));
            var finish=new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"COMPLETE",execution.receipt());
            assertEquals(new PrepareExecutionResult(false,"COMPLETE"),f.service.arbitratePrepare(finish));
            assertEquals(new PrepareExecutionResult(false,"COMPLETE"),f.service.arbitratePrepare(finish));
            assertEquals(new PrepareExecutionResult(false,"COMPLETE"),f.service.arbitratePrepare(other));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_execution"));
            var wrong=new PrepareExecutionRequest(execution.context(),execution.lease(),"b".repeat(32),"COMPLETE",execution.receipt());
            assertEquals("KNOWLEDGE_PREPARE_EXECUTOR_CONFLICT",assertThrows(IllegalStateException.class,
                ()->f.service.arbitratePrepare(wrong)).getMessage());
        });
    }
    @Test void preparationExecutionConcurrentRequestsHaveOnlyOneGrant() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            var pool=java.util.concurrent.Executors.newFixedThreadPool(2);
            var ready=new java.util.concurrent.CountDownLatch(2);var start=new java.util.concurrent.CountDownLatch(1);
            try {
                var futures=new ArrayList<java.util.concurrent.Future<PrepareExecutionResult>>();
                for(String executor:List.of("a".repeat(32),"b".repeat(32))) futures.add(pool.submit(()->{
                    ready.countDown();assertTrue(start.await(5,java.util.concurrent.TimeUnit.SECONDS));
                    return f.service.arbitratePrepare(new PrepareExecutionRequest(execution.context(),execution.lease(),executor,"BEGIN",null));
                }));
                assertTrue(ready.await(5,java.util.concurrent.TimeUnit.SECONDS));start.countDown();
                int grants=0;for(var future:futures) if(future.get(10,java.util.concurrent.TimeUnit.SECONDS).granted()) grants++;
                assertEquals(1,grants);assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_execution"));
            } finally {start.countDown();pool.shutdownNow();}
        });
    }
    @Test void preparationExecutionRejectsEpochAndRevokedLease() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.registerPrepareAnchor(1,"owner",execution.lease(),execution.context());
            var begin=new PrepareExecutionRequest(execution.context(),execution.lease(),"a".repeat(32),"BEGIN",null);
            f.service.beforeAdministrativeMutation();
            assertEquals("KNOWLEDGE_PREPARE_CONTEXT_CONFLICT",assertThrows(IllegalStateException.class,
                ()->f.service.arbitratePrepare(begin)).getMessage());
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_execution"));
            f.coordinator.revoke(execution.lease());
            assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",assertThrows(IllegalStateException.class,
                ()->f.service.arbitratePrepare(begin)).getMessage());
        });
    }
    @Test void replacementClaimRejectsOldAnchorRegistrationAndCommit() throws Exception {
        isolated(f->{
            var first=f.prepare(1);
            var anchor=f.service.registerPrepareAnchor(1,"owner",first.lease(),first.context());
            // 显式模拟已由业务调度完成的claim替换，不自动认领过期任务。
            f.jdbc.update("UPDATE customer_service_knowledge_etl_outbox SET processingOwner='replacement' WHERE id=1");
            f.coordinator.revoke(first.lease());
            var replacement=f.coordinator.acquire("document:1","new_claim_attempt","INDEX","replacement");
            assertEquals("KNOWLEDGE_PUBLICATION_CLAIM_LOST",assertThrows(IllegalStateException.class,
                ()->f.service.registerPrepareAnchor(1,"owner",first.lease(),first.context())).getMessage());
            assertEquals("KNOWLEDGE_PUBLICATION_CLAIM_LOST",assertThrows(IllegalStateException.class,
                ()->f.service.commit(1,"owner",first.lease(),first.context(),first.receipt())).getMessage());
            assertEquals(anchor,f.service.registerPrepareAnchor(1,"replacement",replacement,first.context()));
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
            assertEquals(1,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE processingOwner='replacement' AND status='PROCESSING'"));
        });
    }
    @Test void epochChangedAfterCandidatePreparationRejectsOldCommit() throws Exception {
        isolated(f->{
            var first=f.prepare(1);
            f.service.registerPrepareAnchor(1,"owner",first.lease(),first.context());
            f.service.beforeAdministrativeMutation();
            assertEquals("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT",assertThrows(IllegalStateException.class,
                ()->f.service.commit(1,"owner",first.lease(),first.context(),first.receipt())).getMessage());
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
            assertEquals(2,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
            assertEquals(0,f.scalar("SELECT indexedVersion FROM customer_service_knowledge_document WHERE id=1"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_anchor"));
        });
    }
    @Test void prepareAnchorSurvivesLeaseChangeAndRejectsEpochDrift() throws Exception {
        isolated(f->{
            var first=f.prepare(1);
            var anchor=f.service.registerPrepareAnchor(1,"owner",first.lease(),first.context());
            assertEquals(anchor,f.service.registerPrepareAnchor(1,"owner",first.lease(),first.context()));
            f.coordinator.revoke(first.lease());
            var next=f.coordinator.acquire("document:1","next_attempt","INDEX","owner");
            assertEquals(anchor,f.service.registerPrepareAnchor(1,"owner",next,first.context()));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_anchor"));
            f.service.beforeAdministrativeMutation();
            var changed=f.service.prepare(1,"owner",next);
            assertEquals("KNOWLEDGE_PREPARE_CONTEXT_CONFLICT",assertThrows(IllegalStateException.class,
                ()->f.service.registerPrepareAnchor(1,"owner",next,changed)).getMessage());
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_prepare_anchor"));
        });
    }
    static final Instant NOW=Instant.parse("2030-01-01T00:00:00Z");
    static final String HASH="a".repeat(64);
    @Test void inventoryUsesImmutableEpochOrderAndBoundedProjection() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt);
            var first=f.service.inventory(0,1);assertTrue(first.hasMore());assertEquals(1,first.nextEpoch());
            var second=f.service.inventory(first.nextEpoch(),1);assertFalse(second.hasMore());assertEquals("task_1",second.entries().getFirst().publicationId());
            assertEquals(2,second.nextEpoch());assertEquals(first.epoch(),second.epoch());
            assertNotNull(second.entries().getFirst().createTime());assertEquals(execution.receipt.candidateCollection(),second.entries().getFirst().collectionName());
        });
    }
    @Test void bootstrapCommitsFirstFactAndRepeatsAfterLeaseRevocation() throws Exception {
        isolated(f->{
            f.jdbc.update("UPDATE customer_service_knowledge_publication_state SET enabled=0,epoch=0,publicationId=NULL WHERE id=1");
            f.jdbc.update("DELETE FROM customer_service_knowledge_publication");
            f.properties.setPublicationBootstrapAllowed(true);
            String id="bootstrap_"+"f".repeat(32);
            var lease=f.coordinator.acquire("collection:"+f.properties.getCollectionAlias(),id,"REBUILD","maintenance");
            var context=f.service.prepareBootstrap(id,"legacy_specific",lease);
            var receipt=new CandidateReceipt(id,0,id,lease.fence(),"customer_service_knowledge_pub_"+"f".repeat(32),HASH,"model-v1",3,List.of());
            var fact=f.service.commitBootstrap(context,receipt,lease);
            assertEquals(1,fact.epoch());assertTrue(f.service.snapshot().enabled());
            f.coordinator.revoke(lease);f.properties.setPublicationBootstrapAllowed(false);
            assertEquals(fact,f.service.commitBootstrap(context,receipt,lease));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication"));
        });
    }
    @Test void deletingLastServingDocumentCanPublishEmptySnapshot() throws Exception {
        isolated(f->{
            var index=f.prepare(1);f.service.commit(1,"owner",index.lease,index.context,index.receipt);f.coordinator.revoke(index.lease);
            new org.springframework.transaction.support.TransactionTemplate(f.transactions).executeWithoutResult(status->{
                f.service.beforeAdministrativeMutation();
                f.jdbc.update("UPDATE customer_service_knowledge_document SET status='DISABLED',etlVersion=3 WHERE id=1");
                f.jdbc.update("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status,nextRetryTime) VALUES(2,1,1,3,'DELETE','PENDING','2029-12-31 00:00:00')");
            });
            assertEquals(1,f.tasks.claim(2,"owner",LocalDateTime.ofInstant(NOW,ZoneOffset.UTC),LocalDateTime.ofInstant(NOW.plusSeconds(60),ZoneOffset.UTC)));
            var lease=f.coordinator.acquire("document:1","delete-attempt","DELETE","owner");
            var context=f.service.prepare(2,"owner",lease);assertTrue(context.documents().isEmpty());
            var receipt=new CandidateReceipt("task_2",context.baseEpoch(),lease.operationId(),lease.fence(),"customer_service_knowledge_pub_"+"e".repeat(32),HASH,"model-v1",3,List.of());
            f.service.commit(2,"owner",lease,context,receipt);assertTrue(f.service.snapshot().documents().isEmpty());
            assertEquals(receipt.candidateCollection(),f.service.snapshot().collectionName());
        });
    }
    @Test void commitsAllFactsAndExpiredRetryReturnsSameFact() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);
            var fact=f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt);
            assertEquals(2,fact.epoch());assertEquals(1,f.scalar("SELECT indexedVersion FROM customer_service_knowledge_document WHERE id=1"));
            assertEquals(2,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status='SUCCEEDED'"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox WHERE status='PENDING'"));
            f.coordinator.revoke(execution.lease);
            assertEquals(fact,f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt));
            var conflict=new CandidateReceipt("task_1",1,execution.lease.operationId(),execution.lease.fence(),
                    execution.receipt.candidateCollection(),"b".repeat(64),"model-v1",3,execution.receipt.documents());
            assertEquals("KNOWLEDGE_PUBLICATION_RECEIPT_CONFLICT",assertThrows(IllegalStateException.class,
                    ()->f.service.commit(1,"owner",execution.lease,execution.context,conflict)).getMessage());
            assertEquals(HASH,f.service.snapshot().snapshotHash());
        });
    }
    @Test void lateAliasOutboxFailureRollsBackServingHeadFactAndTask() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);
            f.jdbc.update("INSERT INTO customer_service_knowledge_alias_outbox(publicationId,epoch,status) VALUES('task_1',99,'PENDING')");
            assertThrows(RuntimeException.class,()->f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt));
            assertEquals(0,f.scalar("SELECT indexedVersion FROM customer_service_knowledge_document WHERE id=1"));
            assertEquals(1,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status='PROCESSING'"));
        });
    }
    @Test void administrativeEpochAndOwnerTakeoverRejectOldCandidate() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);
            f.service.beforeAdministrativeMutation();
            assertEquals("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT",assertThrows(IllegalStateException.class,
                    ()->f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt)).getMessage());
            f.jdbc.update("UPDATE customer_service_knowledge_etl_outbox SET processingOwner='replacement' WHERE id=1");
            assertEquals("KNOWLEDGE_PUBLICATION_CLAIM_LOST",assertThrows(IllegalStateException.class,
                    ()->f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt)).getMessage());
            assertEquals(0,f.scalar("SELECT indexedVersion FROM customer_service_knowledge_document WHERE id=1"));
        });
    }
    @Test void disableImmediatelyRemovesReadWhitelistAndAliasAcknowledgementCannotAcknowledgeReplacement() throws Exception {
        isolated(f->{
            var execution=f.prepare(1);f.service.commit(1,"owner",execution.lease,execution.context,execution.receipt);
            var before=f.service.snapshot();assertEquals(1,before.documents().size());
            new org.springframework.transaction.support.TransactionTemplate(f.transactions).executeWithoutResult(status->{
                f.service.beforeAdministrativeMutation();
                f.jdbc.update("UPDATE customer_service_knowledge_document SET status='DISABLED' WHERE id=1");
            });
            var after=f.service.snapshot();assertTrue(after.documents().isEmpty());assertTrue(after.epoch()>before.epoch());
            f.service.acknowledgeMirror(before);
            assertEquals(0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox WHERE status='ACK'"));
            f.service.acknowledgeMirror(after);
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox WHERE status='ACK'"));
        });
    }
    @Test void twoPreparedDocumentsCannotCommitSameEpoch() throws Exception {
        isolated(f->{
            var first=f.prepare(1);var second=f.prepare(2);
            try(var executor=java.util.concurrent.Executors.newFixedThreadPool(2)) {
                var start=new java.util.concurrent.CountDownLatch(1);
                var a=executor.submit(()->{start.await();return f.tryCommit(1,first);});
                var b=executor.submit(()->{start.await();return f.tryCommit(2,second);});start.countDown();
                assertEquals(1,a.get(15,java.util.concurrent.TimeUnit.SECONDS)+b.get(15,java.util.concurrent.TimeUnit.SECONDS));
            }
            assertEquals(2,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
            assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status='SUCCEEDED'"));
        });
    }
    void isolated(Scenario scenario) throws Exception {
        isolated(Clock.fixed(NOW,ZoneOffset.UTC),scenario);
    }
    void isolated(Clock clock,Scenario scenario) throws Exception {
        String schema="rag_publication_it_"+UUID.randomUUID().toString().replace("-","");
        if(!schema.matches("rag_publication_it_[a-f0-9]{32}")) throw new IllegalArgumentException("隔离库名称无效");
        try(var admin=KnowledgeRevisionMysqlSupport.connect()) {
            boolean created=false;
            try {
                try(var stmt=admin.createStatement()) {stmt.execute("CREATE DATABASE `"+schema+"` CHARACTER SET utf8mb4");created=true;}
                admin.setCatalog(schema);assertEquals(schema,admin.getCatalog());
                KnowledgeRevisionMysqlSupport.script(admin,Files.readString(Path.of("sql/alter_customer_service_knowledge.sql")));
                KnowledgeRevisionMysqlSupport.script(admin,Files.readString(Path.of("sql/alter_customer_service_knowledge_publication.sql")));
                KnowledgeRevisionMysqlSupport.script(admin,Files.readString(Path.of("sql/alter_customer_service_knowledge_prepare_anchor.sql")));
                KnowledgeRevisionMysqlSupport.script(admin,Files.readString(Path.of("sql/alter_customer_service_knowledge_prepare_execution.sql")));
                scenario.run(new Fixture(schema,true,clock));
            } finally {
                com.mybatisflex.core.FlexGlobalConfig.getGlobalConfigs().remove(schema);
                if(created) {
                    admin.setCatalog("yu_ai_code_mother");
                    try(var stmt=admin.createStatement()) {stmt.execute("DROP DATABASE `"+schema+"`");}
                    System.out.println("RAG_PUBLICATION_IT_SCHEMA_CLEANED");
                }
            }
        }
    }
    static class Fixture {
        final JdbcTemplate jdbc;
        final DataSourceTransactionManager transactions;
        final KnowledgeMutationCoordinator coordinator;
        final KnowledgePublicationService service;
        final CustomerServiceKnowledgeEtlOutboxMapper tasks;
        final CustomerServiceKnowledgeDocumentMapper documentMapper;
        final KnowledgePublicationMapper publicationMapper;
        final CustomerServiceKnowledgeMutationLeaseMapper leaseMapper;
        final CustomerServiceProperties properties=new CustomerServiceProperties();
        Fixture(String schema) throws Exception {
            this(schema,true);
        }
        Fixture(String schema, boolean initialize) throws Exception {
            this(schema,initialize,Clock.fixed(NOW,ZoneOffset.UTC));
        }
        Fixture(String schema, boolean initialize,Clock clock) throws Exception {
            var isolatedSource=new AbstractDataSource() {
                @Override public Connection getConnection() throws SQLException {
                    Connection connection=null;
                    try {connection=KnowledgeRevisionMysqlSupport.connect();connection.setCatalog(schema);
                        if(!schema.equals(connection.getCatalog())) throw new SQLException("隔离库切换失败");return connection;
                    } catch(Exception error) {if(connection!=null) connection.close();throw new SQLException("无法建立隔离连接");}
                }
                @Override public Connection getConnection(String user,String password) throws SQLException {throw new SQLException("拒绝非受控凭据");}
            };
            var source=new com.mybatisflex.core.datasource.FlexDataSource(schema,isolatedSource,
                    com.mybatisflex.core.dialect.DbType.MYSQL,false);
            jdbc=new JdbcTemplate(source);transactions=new DataSourceTransactionManager(source);
            // 管理上传通过 BaseMapper 更新文档及插入任务，需要与生产一致的 Flex 映射注册。
            var cfg=new FlexConfiguration(new Environment(schema,new SpringManagedTransactionFactory(),source));
            // Flex在注册insert时查全局配置；需像Spring工厂一样先注册，不能依赖前一个夹具的残留配置。
            var global=new com.mybatisflex.core.FlexGlobalConfig();global.setConfiguration(cfg);
            com.mybatisflex.core.FlexGlobalConfig.setConfig(schema,global,false);
            cfg.addMapper(CustomerServiceKnowledgeDocumentMapper.class);cfg.addMapper(CustomerServiceKnowledgeEtlOutboxMapper.class);
            cfg.addMapper(CustomerServiceKnowledgeMutationLeaseMapper.class);cfg.addMapper(KnowledgePublicationMapper.class);
            try(var input=Files.newInputStream(Path.of("src/main/resources/mapper/KnowledgePublicationMapper.xml"))) {
                new XMLMapperBuilder(input,cfg,"publication-mapper",cfg.getSqlFragments()).parse();
            }
            var sessions=new SqlSessionTemplate(new FlexSqlSessionFactoryBuilder().build(cfg));
            assertInstanceOf(com.mybatisflex.core.keygen.CustomKeyGenerator.class,
                    cfg.getMappedStatement(CustomerServiceKnowledgeEtlOutboxMapper.class.getName()+".insert").getKeyGenerator(),
                    "夹具必须由生产注解注册雪花ID生成器");
            tasks=sessions.getMapper(CustomerServiceKnowledgeEtlOutboxMapper.class);
            documentMapper=sessions.getMapper(CustomerServiceKnowledgeDocumentMapper.class);
            publicationMapper=sessions.getMapper(KnowledgePublicationMapper.class);
            leaseMapper=sessions.getMapper(CustomerServiceKnowledgeMutationLeaseMapper.class);
            properties.setPublicationEnabled(true);
            var ai=new AiEngineProperties();ai.setToken("isolated-signing-secret");
            coordinator=transactional(new KnowledgeMutationCoordinator(leaseMapper,ai,properties,clock));
            service=transactional(new KnowledgePublicationService(publicationMapper,
                    sessions.getMapper(CustomerServiceKnowledgeDocumentMapper.class),tasks,sessions.getMapper(CustomerServiceKnowledgeMutationLeaseMapper.class),
                    coordinator,properties,clock));
            if(!initialize) return;
            var initial=new CandidateReceipt("bootstrap_"+"0".repeat(32),0,"bootstrap",1,"customer_service_knowledge_pub_"+"0".repeat(32),HASH,"model-v1",3,List.of());
            jdbc.update("INSERT INTO customer_service_knowledge_publication(publicationId,epoch,collectionName,receiptJson) VALUES(?,1,?,?)",initial.publicationId(),initial.candidateCollection(),new ObjectMapper().writeValueAsString(initial));
            jdbc.update("UPDATE customer_service_knowledge_publication_state SET enabled=1,epoch=1,publicationId=? WHERE id=1",initial.publicationId());
        }
        <T> T transactional(T target) {
            var proxy=new ProxyFactory(target);proxy.setProxyTargetClass(true);
            proxy.addAdvice(new TransactionInterceptor(transactions,new AnnotationTransactionAttributeSource()));
            @SuppressWarnings("unchecked") T result=(T)proxy.getProxy();return result;
        }
        Execution prepare(long id) {
            seed(id);
            assertEquals(1,tasks.claim(id,"owner",LocalDateTime.ofInstant(NOW,ZoneOffset.UTC),LocalDateTime.ofInstant(NOW.plusSeconds(60),ZoneOffset.UTC)));
            var lease=coordinator.acquire("document:"+id,"attempt_"+id,"INDEX","owner");
            var context=service.prepare(id,"owner",lease);
            var receipt=new CandidateReceipt(context.publicationId(),context.baseEpoch(),lease.operationId(),lease.fence(),
                    "customer_service_knowledge_pub_"+String.format("%032d",id),HASH,"model-v1",3,
                    context.documents().stream().map(d->new ReceiptDocument(d.documentId(),d.documentVersion(),d.indexRevision(),d.activationRevision(),d.sha256(),d.fileName(),d.fileType(),4)).toList());
            return new Execution(lease,context,receipt);
        }
        void seed(long id) {
            jdbc.update("INSERT INTO customer_service_knowledge_document(id,name,fileType,objectKey,fileSize,contentHash,documentVersion,etlVersion,status,createdBy,updatedBy) VALUES(?,'隔离原文','TXT','fake.txt',1,?,1,2,'INDEXING',1,1)",id,String.format("%064d",id));
            jdbc.update("INSERT INTO customer_service_knowledge_content_revision(documentId,documentVersion,name,fileType,objectKey,fileSize,contentHash) VALUES(?,1,'隔离原文','TXT','fake.txt',1,?)",id,String.format("%064d",id));
            jdbc.update("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status,nextRetryTime) VALUES(?,?,1,2,'INDEX','PENDING','2029-12-31 00:00:00')",id,id);
        }
        int tryCommit(long id,Execution execution) {
            try {service.commit(id,"owner",execution.lease,execution.context,execution.receipt);return 1;}
            catch(IllegalStateException rejected) {assertEquals("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT",rejected.getMessage());return 0;}
        }
        long scalar(String sql) {return jdbc.queryForObject(sql,Long.class);}
    }
    record Execution(KnowledgeMutationCoordinator.Lease lease,PublicationContext context,CandidateReceipt receipt) { }
    interface Scenario {void run(Fixture fixture) throws Exception;}
}
