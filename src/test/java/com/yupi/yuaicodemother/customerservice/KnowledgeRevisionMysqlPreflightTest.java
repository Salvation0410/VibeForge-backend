package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 不启动 Spring、不写业务库；只输出字段名、版本身份与聚合计数。 */
@EnabledIfEnvironmentVariable(named = "AI_KNOWLEDGE_MYSQL_PREFLIGHT", matches = "true")
class KnowledgeRevisionMysqlPreflightTest {
    @Test
    void reportReadOnlyMigrationReadiness() throws Exception {
        Map<String, Object> report = new LinkedHashMap<>();
        try (var connection = KnowledgeRevisionMysqlSupport.connect()) {
            connection.setReadOnly(true);
            connection.setAutoCommit(false);
            try {
                var columns = rows(connection, "SELECT TABLE_NAME,COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('customer_service_knowledge_document','customer_service_knowledge_etl_outbox','customer_service_knowledge_content_revision') ORDER BY TABLE_NAME,ORDINAL_POSITION");
                report.put("columns", columns);
                boolean v2 = columns.stream().anyMatch(row -> "activationRevision".equals(row.get("COLUMN_NAME")));
                report.put("schemaV2", v2);
                report.put("documents", rows(connection, "SELECT id,documentVersion,indexedVersion,etlVersion,status,isDelete" + (v2 ? ",activationRevision,indexedEtlVersion,indexedActivationRevision" : "") + " FROM customer_service_knowledge_document ORDER BY id"));
                report.put("outboxStatusCounts", rows(connection, "SELECT status,COUNT(*) AS total FROM customer_service_knowledge_etl_outbox GROUP BY status ORDER BY status"));
                // 只读验收保留任务身份和稳定错误码，不导出原文地址或租约凭据。
                report.put("recentTasks", rows(connection, "SELECT id,documentId,documentVersion,etlVersion,operation,status,retryCount,lastErrorCode FROM customer_service_knowledge_etl_outbox ORDER BY id DESC LIMIT 20"));
                long pending = count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')");
                long unknown = count(connection, "SELECT COUNT(*) FROM customer_service_knowledge_document WHERE isDelete=0 AND status<>'DISABLED' AND indexedVersion>0 AND (indexedVersion<>documentVersion OR status<>'ACTIVE' OR etlVersion<=0)");
                report.put("pendingOrProcessing", pending);
                report.put("legacyServingUnavailable", unknown);
                report.put("migrationReady", !v2 && pending == 0 && unknown == 0);
                report.put("businessSchemaMutated", false);
            } finally { connection.rollback(); }
        }
        String json = new ObjectMapper().writerWithDefaultPrettyPrinter().writeValueAsString(report);
        Path path = Path.of("tmp", "knowledge-revision-mysql-preflight.json");
        Files.createDirectories(path.getParent());
        Files.writeString(path, json);
        System.out.println("KNOWLEDGE_MYSQL_PREFLIGHT=" + json);
    }

    private static long count(java.sql.Connection connection, String sql) throws Exception {
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            result.next(); return result.getLong(1);
        }
    }

    private static List<Map<String, Object>> rows(java.sql.Connection connection, String sql) throws Exception {
        var rows = new ArrayList<Map<String, Object>>();
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            var metadata = result.getMetaData();
            while (result.next()) {
                var row = new LinkedHashMap<String, Object>();
                for (int i = 1; i <= metadata.getColumnCount(); i++) row.put(metadata.getColumnLabel(i), result.getObject(i));
                rows.add(row);
            }
        }
        return rows;
    }
}
