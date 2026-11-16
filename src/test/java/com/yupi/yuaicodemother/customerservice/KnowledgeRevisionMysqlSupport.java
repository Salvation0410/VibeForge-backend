package com.yupi.yuaicodemother.customerservice;

import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.Set;

/** 本地显式验收使用已有 Spring 配置，不复制或打印认证材料。 */
final class KnowledgeRevisionMysqlSupport {
    static Connection connect() throws Exception {
        var environment = new StandardEnvironment();
        var loader = new YamlPropertySourceLoader();
        for (String name : new String[]{"application-local.yml", "application.yml"}) {
            Path path = Path.of("src/main/resources", name);
            if (Files.isRegularFile(path)) {
                for (var source : loader.load("knowledge-revision-it-" + name, new FileSystemResource(path))) {
                    environment.getPropertySources().addLast(source);
                }
            }
        }
        String url = environment.getRequiredProperty("spring.datasource.url");
        URI target = URI.create(url.substring("jdbc:".length()));
        if (!Set.of("localhost", "127.0.0.1", "::1").contains(target.getHost())
                || !"/yu_ai_code_mother".equals(target.getPath())) {
            throw new IllegalStateException("只允许本地业务库配置");
        }
        return new DriverManagerDataSource(url,
                environment.getRequiredProperty("spring.datasource.username"),
                environment.getRequiredProperty("spring.datasource.password")).getConnection();
    }

    /** 仅读取仓库受控脚本；DELIMITER 在客户端解析，不发送给 MySQL。 */
    static void script(Connection connection, String script) throws SQLException {
        String delimiter = ";";
        var pending = new StringBuilder();
        for (String line : script.split("\\R")) {
            String trimmed = line.trim();
            if (trimmed.startsWith("--") || trimmed.isEmpty()) continue;
            if (trimmed.toUpperCase(java.util.Locale.ROOT).startsWith("DELIMITER ")) {
                if (!pending.toString().isBlank()) throw new IllegalArgumentException("分隔符前语句未结束");
                delimiter = trimmed.substring("DELIMITER ".length()).trim();
                continue;
            }
            pending.append(line).append('\n');
            if (trimmed.endsWith(delimiter)) {
                String sql = pending.toString().stripTrailing();
                sql = sql.substring(0, sql.length() - delimiter.length());
                try (var statement = connection.createStatement()) { statement.execute(sql); }
                pending.setLength(0);
            }
        }
        if (!pending.toString().isBlank()) throw new IllegalArgumentException("脚本尾部语句未结束");
    }
}
