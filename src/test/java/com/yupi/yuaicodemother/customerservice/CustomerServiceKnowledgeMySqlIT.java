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
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;

/**
 * Optional destructive integration test for a disposable MySQL 8 database.
 * Run with CUSTOMER_SERVICE_MYSQL_IT_URL, CUSTOMER_SERVICE_MYSQL_IT_USER and
 * CUSTOMER_SERVICE_MYSQL_IT_PASSWORD, then: mvn -Dtest=CustomerServiceKnowledgeMySqlIT test.
 */
@EnabledIfEnvironmentVariable(named = "CUSTOMER_SERVICE_MYSQL_IT_URL", matches = ".+")
class CustomerServiceKnowledgeMySqlIT {
    private static final String URL = System.getenv("CUSTOMER_SERVICE_MYSQL_IT_URL");
    private static final String USER = System.getenv().getOrDefault("CUSTOMER_SERVICE_MYSQL_IT_USER", "root");
    private static final String PASSWORD = System.getenv().getOrDefault("CUSTOMER_SERVICE_MYSQL_IT_PASSWORD", "");

    @BeforeAll
    static void migrateLegacySchema() throws Exception {
        try (Connection connection = open(); var statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_mutation_lease");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_mutation_guard");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_etl_outbox");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_document");
            statement.execute("CREATE TABLE customer_service_knowledge_document (id BIGINT PRIMARY KEY, contentHash CHAR(64) NOT NULL, isDelete TINYINT NOT NULL DEFAULT 0) ENGINE=InnoDB");
            statement.execute("CREATE TABLE customer_service_knowledge_etl_outbox (id BIGINT PRIMARY KEY, documentId BIGINT NOT NULL, documentVersion BIGINT NOT NULL, etlVersion BIGINT NOT NULL, operation VARCHAR(32) NOT NULL, status VARCHAR(32) NOT NULL, retryCount INT NOT NULL DEFAULT 0, nextRetryTime DATETIME NOT NULL, processingOwner VARCHAR(128), processingDeadline DATETIME) ENGINE=InnoDB");
        }
        executeUpgrade();
        executeUpgrade();
    }

    @AfterAll
    static void cleanUp() throws Exception {
        try (Connection connection = open(); var statement = connection.createStatement()) {
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_mutation_lease");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_mutation_guard");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_etl_outbox");
            statement.execute("DROP TABLE IF EXISTS customer_service_knowledge_document");
        }
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
        return DriverManager.getConnection(URL, USER, PASSWORD);
    }
}
