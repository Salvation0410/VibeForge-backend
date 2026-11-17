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
    @Test void socketHttpFaultMatrixWithPersistentAuthority() throws Exception {
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
                String uv="uv";
                Process runner=new ProcessBuilder(uv,"run","python","tests/http_publication_runner.py","--spring-port",String.valueOf(port),
                        "--alias",alias,"--report",directory.resolve("report.json").toString())
                        .directory(Path.of("ai-service").toFile()).redirectErrorStream(true)
                        .redirectOutput(directory.resolve("runner.log").toFile()).start();
                try {
                    assertTrue(runner.waitFor(120,TimeUnit.SECONDS),"跨服务验收必须有界");
                    assertEquals(0,runner.exitValue(),"检查tmp/rag-http-*/runner.log诊断");
                    var report=JSON.readTree(Files.readString(directory.resolve("report.json")));
                    assertTrue(report.get("commitResponseDropped").asBoolean());
                    assertTrue(report.get("inflightAnswerDiscarded").asBoolean());
                    assertTrue(report.get("restartedInstanceConfirmed").asBoolean());
                    assertTrue(report.get("mirrorConverged").asBoolean());
                    assertEquals(2,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='SUCCEEDED'"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND indexedVersion=1 AND indexedEtlVersion=2"));
                    assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                    Files.copy(directory.resolve("report.json"),Path.of("tmp/round5-http-report.json"),StandardCopyOption.REPLACE_EXISTING);
                } finally { if(runner.isAlive()) {runner.destroyForcibly();runner.waitFor(10,TimeUnit.SECONDS);} }
            } finally {fixture=null;execution=null;}
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
    }
}
