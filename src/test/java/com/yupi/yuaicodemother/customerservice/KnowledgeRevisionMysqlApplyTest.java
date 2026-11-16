package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Connection;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** 运维专用一次性入口：停写、备份、迁移；失败不重试、不自动回滚或删除业务表。 */
@EnabledIfEnvironmentVariable(named = "AI_KNOWLEDGE_MYSQL_APPLY_V2", matches = "true")
class KnowledgeRevisionMysqlApplyTest {
    private static final List<String> TABLES = List.of("customer_service_knowledge_document",
            "customer_service_knowledge_etl_outbox", "customer_service_knowledge_mutation_guard",
            "customer_service_knowledge_mutation_lease");

    @Test
    void backupThenApplyOnceAndVerifyEveryIdentity() throws Exception {
        // 必须由操作者先停止 worker 和写入口；数据库快照不能替代跨进程停写。
        try (var connection = KnowledgeRevisionMysqlSupport.connect()) {
            assertReady(connection);
            Path directory = Path.of("tmp", "knowledge-v2-backup-" + UUID.randomUUID());
            Files.createDirectories(directory.getParent());
            Files.createDirectory(directory);
            Path backup = directory.resolve("knowledge.sql");
            Map<String, Long> counts = new LinkedHashMap<>();
            connection.setTransactionIsolation(Connection.TRANSACTION_REPEATABLE_READ);
            connection.setReadOnly(true);
            connection.setAutoCommit(false);
            try {
                try (BufferedWriter writer = Files.newBufferedWriter(backup, StandardCharsets.UTF_8)) {
                    writer.write("-- 本地知识库迁移前快照；恢复必须由操作者单独审查，不由迁移入口自动执行。\nSET NAMES utf8mb4;\n");
                    for (String table : TABLES) counts.put(table, backupTable(connection, table, writer));
                }
                connection.commit();
            } catch (Exception failure) {
                connection.rollback();
                throw failure;
            } finally {
                connection.setAutoCommit(true);
                connection.setReadOnly(false);
            }
            String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(backup)));
            Files.writeString(directory.resolve("sha256.txt"), hash + "  knowledge.sql\n", StandardCharsets.UTF_8);
            System.out.println("KNOWLEDGE_V2_BACKUP path=" + backup.toAbsolutePath() + " rowCounts=" + counts + " sha256=" + hash);
            // 备份后再次拒绝未排空、未知 serving 或已迁移状态，避免覆盖已有状态。
            assertReady(connection);
            KnowledgeRevisionMysqlSupport.script(connection, Files.readString(Path.of("sql", "alter_customer_service_knowledge_v2_revisions.sql")));
            verify(connection, counts.get("customer_service_knowledge_document"));
            System.out.println("KNOWLEDGE_V2_APPLY verified=true documentRows=" + counts.get("customer_service_knowledge_document"));
        }
    }

    private void assertReady(Connection connection) throws Exception {
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_document' AND COLUMN_NAME IN ('activationRevision','indexedEtlVersion','indexedActivationRevision')"), "v2 列已存在，禁止重复迁移");
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_content_revision'"), "registry 已存在，需人工核对");
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')"), "旧任务必须排空");
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_document WHERE isDelete=0 AND status<>'DISABLED' AND indexedVersion>0 AND (indexedVersion<>documentVersion OR status<>'ACTIVE' OR etlVersion<=0)"), "旧 serving 身份无法恢复");
    }

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
        try (var statement = connection.createStatement(); var result = statement.executeQuery("SELECT " + projection + " FROM `" + table + "`")) {
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

    private void verify(Connection connection, long originalRows) throws Exception {
        assertEquals(3, count(connection, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_document' AND COLUMN_NAME IN ('activationRevision','indexedEtlVersion','indexedActivationRevision')"));
        assertEquals(originalRows, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_document"));
        assertEquals(originalRows, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_content_revision"));
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_document d LEFT JOIN customer_service_knowledge_content_revision c ON c.documentId=d.id AND c.documentVersion=d.documentVersion WHERE c.documentId IS NULL OR NOT(c.name<=>d.name) OR NOT(c.fileType<=>d.fileType) OR NOT(c.objectKey<=>d.objectKey) OR NOT(c.fileSize<=>d.fileSize) OR NOT(c.contentHash<=>d.contentHash) OR NOT(c.createTime<=>d.createTime)"));
        assertEquals(0, count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_document WHERE activationRevision<>1 OR indexedEtlVersion<>CASE WHEN isDelete=0 AND status='ACTIVE' AND indexedVersion=documentVersion AND indexedVersion>0 THEN etlVersion ELSE 0 END OR indexedActivationRevision<>CASE WHEN isDelete=0 AND status='ACTIVE' AND indexedVersion=documentVersion AND indexedVersion>0 THEN activationRevision ELSE 0 END"));
    }

    private long count(Connection connection, String sql) throws Exception {
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            if (!result.next()) throw new IllegalStateException("缺少聚合结果");
            return result.getLong(1);
        }
    }
}
