package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.controller.InternalKnowledgePublicationController;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.SpringBootConfiguration;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.web.bind.annotation.*;
import java.nio.file.*;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

/** 测试专用Spring Web容器与独立Python进程，真实随机库；不加载业务应用配置。 */
@EnabledIfEnvironmentVariable(named="AI_RAG_HTTP_INTEGRATION",matches="true")
class KnowledgePublicationHttpIntegrationTest {
    static KnowledgePublicationMysqlIntegrationTest.Fixture fixture;
    static KnowledgePublicationMysqlIntegrationTest.Execution execution;
    static final ObjectMapper JSON=new ObjectMapper();
    static com.yupi.yuaicodemother.worker.CustomerServiceKnowledgeEtlWorker worker;
    @Test void prepareResponseLossRetriesWithoutPublishingUnknownCandidate() throws Exception { runScenario(true); }
    @Test void socketHttpFaultMatrixWithPersistentAuthority() throws Exception {
        runScenario(false);
    }
    void runScenario(boolean prepareMode) throws Exception {
        new KnowledgePublicationMysqlIntegrationTest().isolated(f->{
            fixture=f;execution=f.prepare(1);
            String alias="rag_http_it_"+java.util.UUID.randomUUID().toString().replace("-","");
            f.properties.setCollectionAlias(alias);
            Path directory=Files.createTempDirectory(Path.of("tmp"),"rag-http-").toAbsolutePath();
            SpringApplication application=new SpringApplication(Web.class);
            application.setDefaultProperties(Map.of("server.port","0","server.address","127.0.0.1","spring.config.location","optional:classpath:/rag-http-test-empty.properties",
                    "spring.main.banner-mode","off","spring.autoconfigure.exclude",
                    "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration,org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration,org.springframework.boot.autoconfigure.session.SessionAutoConfiguration"));
            try(var context=(ServletWebServerApplicationContext)application.run()) {
                int port=context.getWebServer().getPort();
                java.util.List<String> command=new java.util.ArrayList<>(java.util.List.of("uv","run","python","tests/http_publication_runner.py","--spring-port",String.valueOf(port),"--alias",alias,"--report",directory.resolve("report.json").toString()));
                if(prepareMode) command.add("--prepare-mode");
                Process runner=new ProcessBuilder(command)
                        .directory(Path.of("ai-service").toFile()).redirectErrorStream(true)
                        .redirectOutput(directory.resolve("runner.log").toFile()).start();
                try {
                    assertTrue(runner.waitFor(120,TimeUnit.SECONDS),"跨服务验收必须有界");
                    assertEquals(0,runner.exitValue(),"检查tmp/rag-http-*/runner.log诊断");
                    var report=JSON.readTree(Files.readString(directory.resolve("report.json")));
                    if(prepareMode) {
                        assertTrue(report.get("prepareResponseDropped").asBoolean());
                        assertEquals(1,report.get("embeddingCalls").asInt());
                        assertTrue(report.get("restartedPrepareRecovered").asBoolean());
                        assertTrue(report.get("persistedAnchorUnchanged").asBoolean());
                        assertTrue(report.get("concurrentPrepareRejected").asBoolean());
                        assertEquals(3,report.get("candidates").asInt());
                        assertTrue(report.get("oldLeaseRevoked").asBoolean());
                        assertTrue(report.get("attemptIdentityIsolated").asBoolean());
                        assertTrue(report.get("publishedSecondReceipt").asBoolean());
                        assertTrue(report.get("oldHeadPreserved").asBoolean());
                    } else {
                    assertTrue(report.get("commitResponseDropped").asBoolean());
                    assertTrue(report.get("inflightAnswerDiscarded").asBoolean());
                    assertTrue(report.get("restartedInstanceConfirmed").asBoolean());
                    assertTrue(report.get("mirrorConverged").asBoolean());
                    }
                    assertEquals(2,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='SUCCEEDED'"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND indexedVersion=1 AND indexedEtlVersion=2"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                    Files.copy(directory.resolve("report.json"),Path.of(prepareMode?"tmp/round9-prepare-report.json":"tmp/round9-http-report.json"),StandardCopyOption.REPLACE_EXISTING);
                } finally { if(runner.isAlive()) {runner.destroyForcibly();runner.waitFor(10,TimeUnit.SECONDS);} }
            } finally {fixture=null;execution=null;worker=null;}
        });
    }
    @SpringBootConfiguration
    @org.springframework.boot.autoconfigure.ImportAutoConfiguration({
            org.springframework.boot.autoconfigure.web.servlet.ServletWebServerFactoryAutoConfiguration.class,
            org.springframework.boot.autoconfigure.web.servlet.DispatcherServletAutoConfiguration.class,
            org.springframework.boot.autoconfigure.web.servlet.WebMvcAutoConfiguration.class,
            org.springframework.boot.autoconfigure.http.HttpMessageConvertersAutoConfiguration.class,
            org.springframework.boot.autoconfigure.jackson.JacksonAutoConfiguration.class})
    static class Web {
        @Bean InternalKnowledgePublicationController publicationController() {
            var token=new AiEngineProperties();token.setToken("isolated-signing-secret");
            return new InternalKnowledgePublicationController(fixture.service,token);
        }
        @Bean Harness harness() {return new Harness();}
        @Bean com.yupi.yuaicodemother.controller.InternalCustomerServiceKnowledgeController leaseController() {
            var token=new AiEngineProperties();token.setToken("isolated-signing-secret");
            return new com.yupi.yuaicodemother.controller.InternalCustomerServiceKnowledgeController(fixture.coordinator,token);
        }
    }
    @RestController
    @RequestMapping("/test")
    static class Harness {
        // 仅测试classpath/回环容器中存在；不增加任何正式管理入口。
        @GetMapping("/context") Object context() {return execution.context();}
        @GetMapping("/lease-identity") Object leaseIdentity() {return Map.of("operationId",execution.lease().operationId(),"fence",execution.lease().fence());}
        @PostMapping("/commit") Object commit(@RequestBody CandidateReceipt receipt) {
            return fixture.service.commit(1,"owner",execution.lease(),execution.context(),receipt);
        }
        @PostMapping("/revoke") Object revoke() {fixture.coordinator.revoke(execution.lease());return Map.of("revoked",true);}
        @PostMapping("/initialize") Object initialize(@RequestBody CandidateReceipt receipt) throws Exception {
            fixture.jdbc.update("UPDATE customer_service_knowledge_publication SET collectionName=?,receiptJson=? WHERE epoch=1",receipt.candidateCollection(),JSON.writeValueAsString(receipt));
            String hash=java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest("隔离知识说明".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
            fixture.jdbc.update("UPDATE customer_service_knowledge_document SET contentHash=? WHERE id=1",hash);
            fixture.jdbc.update("UPDATE customer_service_knowledge_content_revision SET contentHash=? WHERE documentId=1",hash);
            fixture.coordinator.revoke(execution.lease());
            fixture.jdbc.update("UPDATE customer_service_knowledge_etl_outbox SET status='PENDING',processingOwner=NULL,processingDeadline=NULL,nextRetryTime='2029-12-31 00:00:00' WHERE id=1");
            return Map.of("initialized",true);
        }
        @PostMapping("/worker") Object worker(@RequestBody Map<String,Integer> request) throws Exception {
            if(worker==null) {
                fixture.properties.setServiceUrl("http://127.0.0.1:"+request.get("port"));
                fixture.properties.setRetryMax(3);fixture.properties.setTimeoutSeconds(30);
                var token=new AiEngineProperties();token.setToken("isolated-signing-secret");
                var oss=org.mockito.Mockito.mock(com.yupi.yuaicodemother.manager.OssManager.class);
                org.mockito.Mockito.when(oss.generateKnowledgeDownloadUrl(org.mockito.ArgumentMatchers.anyString())).thenReturn(java.net.URI.create("https://synthetic.invalid/manual.txt").toURL());
                var finalizer=fixture.transactional(new com.yupi.yuaicodemother.service.CustomerServiceKnowledgeTaskFinalizer(fixture.documentMapper,fixture.tasks));
                org.springframework.test.util.ReflectionTestUtils.setField(finalizer,"publicationService",fixture.service);
                worker=new com.yupi.yuaicodemother.worker.CustomerServiceKnowledgeEtlWorker(fixture.documentMapper,fixture.tasks,oss,
                        new com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient(fixture.properties,token),fixture.coordinator,finalizer,fixture.properties,java.time.Clock.fixed(KnowledgePublicationMysqlIntegrationTest.NOW,java.time.ZoneOffset.UTC));
                org.springframework.test.util.ReflectionTestUtils.setField(worker,"publicationService",fixture.service);
            }
            fixture.jdbc.update("UPDATE customer_service_knowledge_etl_outbox SET nextRetryTime='2029-12-31 00:00:00' WHERE id=1 AND status='PENDING'");
            worker.poll();
            return fixture.jdbc.queryForMap("SELECT status,retryCount FROM customer_service_knowledge_etl_outbox WHERE id=1");
        }
    }
}
