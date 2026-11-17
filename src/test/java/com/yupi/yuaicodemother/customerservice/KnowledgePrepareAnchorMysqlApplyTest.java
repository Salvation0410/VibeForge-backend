package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import java.nio.file.*;
import java.sql.Connection;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** 显式本机维护：先停写排空，完整备份知识表后只加空anchor表。 */
@EnabledIfEnvironmentVariable(named="AI_KNOWLEDGE_MYSQL_APPLY_PREPARE_ANCHOR",matches="true")
class KnowledgePrepareAnchorMysqlApplyTest {
    private static final List<String> TABLES=List.of("customer_service_knowledge_document",
        "customer_service_knowledge_etl_outbox","customer_service_knowledge_mutation_guard",
        "customer_service_knowledge_mutation_lease","customer_service_knowledge_content_revision",
        "customer_service_knowledge_publication_state","customer_service_knowledge_publication",
        "customer_service_knowledge_alias_outbox");
    @Test void backupThenCreateEmptyAnchorWithoutChangingExistingTables() throws Exception {
        try(var connection=KnowledgeRevisionMysqlSupport.connect()) {
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')"));
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_prepare_anchor'"));
            Path directory=Path.of("tmp","knowledge-prepare-backup-"+UUID.randomUUID());
            Files.createDirectory(directory);
            Map<String,Object> backup=new LinkedHashMap<>();
            connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
            connection.setReadOnly(true);connection.setAutoCommit(false);
            try {
                for(String table:TABLES) backup.put(table,snapshot(connection,table));
                connection.commit();
            } catch(Exception failure) {connection.rollback();throw failure;}
            finally {connection.setAutoCommit(true);connection.setReadOnly(false);}
            byte[] bytes=new ObjectMapper().writeValueAsBytes(backup);
            Files.write(directory.resolve("knowledge-private.json"),bytes);
            String hash=HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes));
            Files.writeString(directory.resolve("sha256.txt"),hash);
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')"));
            KnowledgeRevisionMysqlSupport.script(connection,Files.readString(Path.of("sql/alter_customer_service_knowledge_prepare_anchor.sql")));
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_prepare_anchor"));
            for(String table:TABLES) assertEquals(backup.get(table),snapshot(connection,table),table);
            System.out.println("KNOWLEDGE_PREPARE_MIGRATION verified=true backup="+directory.toAbsolutePath()+" sha256="+hash);
        }
    }
    private Object snapshot(Connection connection,String table) throws Exception {
        if(!TABLES.contains(table)) throw new IllegalArgumentException("非白名单表");
        String ddl;
        try(var statement=connection.createStatement();var result=statement.executeQuery("SHOW CREATE TABLE "+table)) {assertTrue(result.next());ddl=result.getString(2);}
        List<List<String>> rows=new ArrayList<>();List<String> columns=new ArrayList<>();
        try(var statement=connection.createStatement();var result=statement.executeQuery("SELECT * FROM "+table)) {
            var metadata=result.getMetaData();
            for(int i=1;i<=metadata.getColumnCount();i++) columns.add(metadata.getColumnName(i));
            while(result.next()) {
                List<String> row=new ArrayList<>();
                for(int i=1;i<=metadata.getColumnCount();i++) row.add(result.getString(i));
                rows.add(row);
            }
        }
        // JSON备份保留DDL、字段顺序及NULL；排序用于迁移前后完整比较。
        rows.sort(Comparator.comparing(row->new ObjectMapper().valueToTree(row).toString()));
        return Map.of("ddl",ddl,"columns",columns,"rows",rows);
    }
    private long count(Connection connection,String sql) throws Exception {
        try(var statement=connection.createStatement();var result=statement.executeQuery(sql)) {assertTrue(result.next());return result.getLong(1);}
    }
}
