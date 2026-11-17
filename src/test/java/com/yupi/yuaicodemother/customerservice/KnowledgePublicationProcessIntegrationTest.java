package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import java.nio.file.*;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;

/** 子JVM在提交前或提交后回执前直接退出；只访问随机schema，不启动业务应用/模型。 */
@EnabledIfEnvironmentVariable(named="AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION",matches="true")
class KnowledgePublicationProcessIntegrationTest {
    @Test void exitBeforeCommitLeavesNoFactAndOriginalExecutionCanCommit() throws Exception { scenario("before"); }
    @Test void exitAfterCommitBeforeAcknowledgementRepeatsPersistedFact() throws Exception { scenario("after"); }

    void scenario(String phase) throws Exception {
        new KnowledgePublicationMysqlIntegrationTest().isolated(f->{
            String schema=f.jdbc.queryForObject("SELECT DATABASE()",String.class);
            Path directory=Files.createTempDirectory(Path.of("tmp"),"rag-process-");
            Path execution=directory.resolve("execution.json").toAbsolutePath();
            try {
                String java=Path.of(System.getProperty("java.home"),"bin","java.exe").toString();
                String classpath=System.getProperty("surefire.test.class.path",System.getProperty("java.class.path"));
                Process child=new ProcessBuilder(java,"-cp",classpath,Child.class.getName(),schema,phase,execution.toString())
                        .redirectErrorStream(true).redirectOutput(directory.resolve("child.log").toFile()).start();
                try {
                    assertTrue(child.waitFor(45,TimeUnit.SECONDS),"子进程必须有界退出");
                    assertEquals(77,child.exitValue(),"必须到达显式退出点而非启动错误");
                } finally { if(child.isAlive()) {child.destroyForcibly();child.waitFor(10,TimeUnit.SECONDS);} }
                var json=new ObjectMapper();var saved=json.readTree(Files.readString(execution));
                var lease=json.treeToValue(saved.get("lease"),KnowledgeMutationCoordinator.Lease.class);
                var context=json.treeToValue(saved.get("context"),PublicationContext.class);
                var receipt=json.treeToValue(saved.get("receipt"),CandidateReceipt.class);
                boolean committed=phase.equals("after");
                assertEquals(committed?1:0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                assertEquals(committed?2:1,f.scalar("SELECT epoch FROM customer_service_knowledge_publication_state WHERE id=1"));
                assertEquals(committed?1:0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE id=1 AND status='SUCCEEDED'"));
                assertEquals(committed?1:0,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE id=1 AND indexedVersion=1 AND indexedEtlVersion=2"));
                if(committed) f.coordinator.revoke(lease);
                var fact=f.service.commit(1,"owner",lease,context,receipt);
                assertEquals(2,fact.epoch());assertEquals(receipt,fact.receipt());
                assertEquals(1,f.scalar("SELECT COUNT(*) FROM customer_service_knowledge_publication WHERE publicationId='task_1'"));
                var conflict=new CandidateReceipt(receipt.publicationId(),receipt.baseEpoch(),receipt.operationId(),receipt.fence(),
                        receipt.candidateCollection(),"b".repeat(64),receipt.embeddingModelVersion(),receipt.embeddingDimension(),receipt.documents());
                assertThrows(IllegalStateException.class,()->f.service.commit(1,"owner",lease,context,conflict));
                assertEquals(receipt,f.service.fact("task_1").receipt());
            } finally {
                // 仅清理本例精确创建的文件，不递归删除计算路径。
                Files.deleteIfExists(execution);Files.deleteIfExists(directory.resolve("child.log"));Files.deleteIfExists(directory);
            }
        });
    }
    public static class Child {
        public static void main(String[] args) throws Exception {
            if(args.length!=3 || !args[0].matches("rag_publication_it_[a-f0-9]{32}")
                    || !(args[1].equals("before")||args[1].equals("after"))) throw new IllegalArgumentException("隔离参数错误");
            Path target=Path.of(args[2]).toAbsolutePath().normalize();
            if(!target.startsWith(Path.of("tmp").toAbsolutePath().normalize())) throw new IllegalArgumentException("仅限tmp");
            var fixture=new KnowledgePublicationMysqlIntegrationTest.Fixture(args[0],false);
            var execution=fixture.prepare(1);
            var json=new ObjectMapper();var saved=json.createObjectNode();
            saved.set("lease",json.valueToTree(execution.lease()));saved.set("context",json.valueToTree(execution.context()));
            saved.set("receipt",json.valueToTree(execution.receipt()));Files.writeString(target,json.writeValueAsString(saved));
            if(args[1].equals("after")) fixture.service.commit(1,"owner",execution.lease(),execution.context(),execution.receipt());
            // 模拟没有finally/回执的进程退出，parent重新连接持久库核对事实。
            Runtime.getRuntime().halt(77);
        }
    }
}
