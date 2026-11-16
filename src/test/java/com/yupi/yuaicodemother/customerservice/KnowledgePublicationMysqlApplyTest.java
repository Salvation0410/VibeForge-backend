package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.sql.Connection;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** 一次性维护入口：只新建 B0b 三表；必须先停写、排空并由操作者显式 opt-in。 */
@EnabledIfEnvironmentVariable(named="AI_KNOWLEDGE_MYSQL_APPLY_PUBLICATION",matches="true")
class KnowledgePublicationMysqlApplyTest {
    private static final List<String> TABLES=List.of("customer_service_knowledge_document",
            "customer_service_knowledge_etl_outbox","customer_service_knowledge_mutation_guard",
            "customer_service_knowledge_mutation_lease","customer_service_knowledge_content_revision");
    @Test void backupFiveTablesThenApplyNewEmptyAuthorityOnce() throws Exception {
        try(var connection=KnowledgeRevisionMysqlSupport.connect()) {
            assertReady(connection);
            Path directory=Path.of("tmp","knowledge-publication-backup-"+UUID.randomUUID());
            Files.createDirectories(directory.getParent());Files.createDirectory(directory);
            Path backup=directory.resolve("knowledge.sql");
            Map<String,String> fingerprints=new LinkedHashMap<>();
            connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
            connection.setReadOnly(true);connection.setAutoCommit(false);
            try {
                try(var output=Files.newBufferedWriter(backup,StandardCharsets.UTF_8)) {
                    output.write("-- 本地 B0b 迁移前备份，恢复须单独审查。\nSET NAMES utf8mb4;\n");
                    for(String table:TABLES) {
                        String dump=dump(connection,table);output.write(dump);fingerprints.put(table,hash(dump));
                    }
                }
                connection.commit();
            } catch(Exception failure) {connection.rollback();throw failure;}
            finally {connection.setAutoCommit(true);connection.setReadOnly(false);}
            String digest=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(backup)));
            Files.writeString(directory.resolve("sha256.txt"),digest+"  knowledge.sql\n",StandardCharsets.UTF_8);
            assertReady(connection);
            KnowledgeRevisionMysqlSupport.script(connection,Files.readString(Path.of("sql/alter_customer_service_knowledge_publication.sql")));
            assertEquals(1,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_publication_state WHERE id=1 AND epoch=0 AND enabled=0 AND publicationId IS NULL"));
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_publication"));
            assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_alias_outbox"));
            for(String table:TABLES) assertEquals(fingerprints.get(table),hash(dump(connection,table)),"旧表结构及每个字段必须保持原样："+table);
            System.out.println("KNOWLEDGE_PUBLICATION_APPLY verified=true backup="+backup.toAbsolutePath()+" sha256="+digest);
        }
    }
    private void assertReady(Connection connection) throws Exception {
        assertEquals(0,count(connection,"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('customer_service_knowledge_publication_state','customer_service_knowledge_publication','customer_service_knowledge_alias_outbox')"),"权威表已存在，禁止重复迁移或重置");
        assertEquals(0,count(connection,"SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')"),"先排空任务");
        assertEquals(3,count(connection,"SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_document' AND COLUMN_NAME IN ('activationRevision','indexedEtlVersion','indexedActivationRevision')"),"先完成 v2 迁移");
        assertEquals(1,count(connection,"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_content_revision'"));
    }
    private String dump(Connection connection,String table) throws Exception {
        var buffer=new StringWriter();try(var output=new BufferedWriter(buffer)) {backupTable(connection,table,output);}return buffer.toString();
    }
    private String hash(String value) throws Exception {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}
    long backupTable(Connection connection, String table, BufferedWriter writer) throws Exception {
        if (!TABLES.contains(table)) throw new IllegalArgumentException("非白名单备份表");
        try (var statement = connection.createStatement(); var result = statement.executeQuery("SHOW CREATE TABLE `" + table + "`")) {
            if (!result.next()) throw new IllegalStateException("缺少备份表");
            writer.write(result.getString(2)); writer.write(";\n");
        }
        var names = new java.util.ArrayList<String>();
        try (var statement = connection.prepareStatement("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? AND EXTRA NOT LIKE '%STORED GENERATED%' AND EXTRA NOT LIKE '%VIRTUAL GENERATED%' ORDER BY ORDINAL_POSITION")) {
            statement.setString(1, table);
            try (var result = statement.executeQuery()) {
                while (result.next()) {
                    String column = result.getString(1);
                    if (!column.matches("[A-Za-z][A-Za-z0-9_]*")) throw new IllegalStateException("备份字段名不合法");
                    names.add("`" + column + "`");
                }
            }
        }
        String projection = String.join(",", names);
        long rows = 0;
        try (var statement = connection.createStatement(); var result = statement.executeQuery("SELECT " + projection + " FROM `" + table + "` ORDER BY " + projection)) {
            while (result.next()) {
                writer.write("INSERT INTO `" + table + "` (" + projection + ") VALUES (");
                for (int i = 1; i <= names.size(); i++) {
                    if (i > 1) writer.write(",");
                    // UTF-8 十六进制表达式不受反斜杠、引号或 sql_mode 影响；日期由 MySQL 按列类型转换。
                    String value = result.getString(i);
                    writer.write(value == null ? "NULL" : "CONVERT(X'" + HexFormat.of().formatHex(value.getBytes(StandardCharsets.UTF_8)) + "' USING utf8mb4)");
                }
                writer.write(");\n"); rows++;
            }
        }
        return rows;
    }

    private long count(Connection connection,String sql) throws Exception {
        try(var statement=connection.createStatement();var result=statement.executeQuery(sql)) {
            if(!result.next()) throw new IllegalStateException("聚合结果缺失");return result.getLong(1);
        }
    }
}
