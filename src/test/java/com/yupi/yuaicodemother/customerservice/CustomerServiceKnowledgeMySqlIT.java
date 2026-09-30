package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;

/**
 * Optional isolated integration test for a MySQL 8 server. It creates and drops only a random schema.
 * Set CUSTOMER_SERVICE_MYSQL_IT_EXECUTE=true together with CUSTOMER_SERVICE_MYSQL_IT_URL,
 * CUSTOMER_SERVICE_MYSQL_IT_USER and CUSTOMER_SERVICE_MYSQL_IT_PASSWORD, then run
 * mvn -Dtest=CustomerServiceKnowledgeMySqlIT test.
 */
@EnabledIfEnvironmentVariable(named = "CUSTOMER_SERVICE_MYSQL_IT_URL", matches = ".+")
@EnabledIfEnvironmentVariable(named = "CUSTOMER_SERVICE_MYSQL_IT_EXECUTE", matches = "(?i:true)")
class CustomerServiceKnowledgeMySqlIT {
    private static final String URL = System.getenv("CUSTOMER_SERVICE_MYSQL_IT_URL");
    private static final String USER = System.getenv().getOrDefault("CUSTOMER_SERVICE_MYSQL_IT_USER", "root");
    private static final String PASSWORD = System.getenv().getOrDefault("CUSTOMER_SERVICE_MYSQL_IT_PASSWORD", "");
    private static final String SCHEMA = "cs_knowledge_it_" + UUID.randomUUID().toString().replace("-", "");
    private static boolean schemaCreated;

    @BeforeAll
    static void migrateLegacySchema() throws Exception {
        try {
            try (Connection connection = openServer(); var statement = connection.createStatement()) {
                statement.execute("CREATE DATABASE `" + SCHEMA + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
                schemaCreated = true;
            }
            try (Connection connection = open(); var statement = connection.createStatement()) {
                statement.execute("CREATE TABLE customer_service_knowledge_document (id BIGINT PRIMARY KEY, contentHash CHAR(64) NOT NULL, lastErrorCode VARCHAR(64), isDelete TINYINT NOT NULL DEFAULT 0) ENGINE=InnoDB");
                statement.execute("CREATE TABLE customer_service_knowledge_etl_outbox (id BIGINT PRIMARY KEY, documentId BIGINT NOT NULL, documentVersion BIGINT NOT NULL, etlVersion BIGINT NOT NULL, operation VARCHAR(32) NOT NULL, status VARCHAR(32) NOT NULL, retryCount INT NOT NULL DEFAULT 0, nextRetryTime DATETIME NOT NULL, lastErrorCode VARCHAR(64), processingOwner VARCHAR(128), processingDeadline DATETIME) ENGINE=InnoDB");
            }
            executeUpgrade();
            executeUpgrade();
        } catch (Exception error) {
            dropSchema();
            throw error;
        }
    }

    @AfterAll
    static void cleanUp() throws Exception {
        dropSchema();
    }

    @Test
    void migrationAndGlobalFenceWorkAcrossConnections() {
        assertTimeoutPreemptively(Duration.ofSeconds(10), () -> {
            try (var executor = Executors.newFixedThreadPool(2)) {
                Future<Long> first = executor.submit(() -> issueFence("it_a"));
                Future<Long> second = executor.submit(() -> issueFence("it_b"));
                assertEquals(Set.of(1L, 2L), Set.of(first.get(), second.get()));
            }
        });
    }

    @Test
    void claimCasAndExpiredReclaimWorkAcrossConnections() throws Exception {
        try (Connection connection = open(); var statement = connection.createStatement()) {
            statement.executeUpdate("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status,nextRetryTime) VALUES(1,1,1,1,'INDEX','PENDING',NOW())");
        }
        try (var executor = Executors.newFixedThreadPool(2)) {
            Future<Integer> first = executor.submit(() -> claim("owner-a"));
            Future<Integer> second = executor.submit(() -> claim("owner-b"));
            assertEquals(1, first.get() + second.get());
        }
        try (Connection connection = open(); var statement = connection.createStatement()) {
            statement.executeUpdate("UPDATE customer_service_knowledge_etl_outbox SET status='PROCESSING',processingDeadline=DATE_SUB(NOW(), INTERVAL 1 SECOND)");
        }
        assertEquals(1, claim("owner-c"));
    }

    @Test
    void migrationPersistsFullLengthErrorCodes() throws Exception {
        String code = "E".repeat(128);
        try (Connection connection = open(); var statement = connection.createStatement()) {
            statement.executeUpdate("INSERT INTO customer_service_knowledge_document(id,contentHash,lastErrorCode,isDelete) VALUES(2,'" + "b".repeat(64) + "','" + code + "',0)");
            statement.executeUpdate("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status,nextRetryTime,lastErrorCode) VALUES(2,2,1,1,'INDEX','FAILED',NOW(),'" + code + "')");
            try (ResultSet result = statement.executeQuery("SELECT d.lastErrorCode,o.lastErrorCode FROM customer_service_knowledge_document d JOIN customer_service_knowledge_etl_outbox o ON o.documentId=d.id WHERE d.id=2")) {
                result.next();
                assertEquals(code, result.getString(1));
                assertEquals(code, result.getString(2));
            }
        }
    }

    private static long issueFence(String operationId) throws Exception {
        try (Connection connection = open()) {
            connection.setAutoCommit(false);
            long fence;
            try (var statement = connection.createStatement();
                 ResultSet result = statement.executeQuery("SELECT nextFence FROM customer_service_knowledge_mutation_guard WHERE id=1 FOR UPDATE")) {
                result.next(); fence = result.getLong(1);
            }
            try (var statement = connection.prepareStatement("UPDATE customer_service_knowledge_mutation_guard SET nextFence=nextFence+1 WHERE id=1 AND nextFence=?")) {
                statement.setLong(1, fence); assertEquals(1, statement.executeUpdate());
            }
            try (var statement = connection.prepareStatement("INSERT INTO customer_service_knowledge_mutation_lease(operationId,scope,operation,fence,expiresAt,owner) VALUES(?,'document:1','INDEX',?,DATE_ADD(NOW(), INTERVAL 1 MINUTE),'it')")) {
                statement.setString(1, operationId); statement.setLong(2, fence); statement.executeUpdate();
            }
            connection.commit();
            return fence;
        }
    }

    private static int claim(String owner) throws Exception {
        try (Connection connection = open(); var statement = connection.prepareStatement(
                "UPDATE customer_service_knowledge_etl_outbox SET status='PROCESSING',processingOwner=?,processingDeadline=DATE_ADD(NOW(), INTERVAL 1 MINUTE) WHERE id=1 AND ((status='PENDING' AND nextRetryTime<=NOW()) OR (status='PROCESSING' AND processingDeadline<NOW()))")) {
            statement.setString(1, owner);
            return statement.executeUpdate();
        }
    }

    private static void executeUpgrade() throws Exception {
        String sql = Files.readString(Path.of("sql", "alter_customer_service_knowledge_task7.sql"));
        try (Connection connection = open(); var statement = connection.createStatement()) {
            for (String command : sql.split(";\\s*(?:\\R|$)")) {
                if (!command.isBlank()) statement.execute(command);
            }
        }
    }

    private static Connection open() throws Exception {
        Connection connection = openServer();
        connection.setCatalog(SCHEMA);
        return connection;
    }

    private static Connection openServer() throws Exception {
        if (URL == null || !URL.matches("jdbc:mysql://[^/]+/[^?]*(?:\\?.*)?")) {
            throw new IllegalArgumentException("CUSTOMER_SERVICE_MYSQL_IT_URL must be a jdbc:mysql URL");
        }
        int pathStart = URL.indexOf('/', "jdbc:mysql://".length());
        int queryStart = URL.indexOf('?', pathStart);
        String serverUrl = URL.substring(0, pathStart + 1)
                + (queryStart < 0 ? "" : URL.substring(queryStart));
        return DriverManager.getConnection(serverUrl, USER, PASSWORD);
    }

    private static void dropSchema() throws Exception {
        if (!schemaCreated) return;
        try (Connection connection = openServer(); var statement = connection.createStatement()) {
            statement.execute("DROP DATABASE `" + SCHEMA + "`");
            schemaCreated = false;
        }
    }
}
