package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.mapper.GenerationTaskMapper;
import com.yupi.yuaicodemother.mapper.GenerationFenceMapper;
import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.mapping.Environment;
import org.apache.ibatis.session.Configuration;
import org.apache.ibatis.session.SqlSessionFactoryBuilder;
import org.mybatis.spring.SqlSessionTemplate;
import org.mybatis.spring.transaction.SpringManagedTransactionFactory;
import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.StandardEnvironment;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.util.Set;

/** 显式 opt-in 测试专用资源；不启动 Spring 全应用、不输出连接凭据、不自动迁移表。 */
final class RealGenerationTaskResources implements AutoCloseable {
    private final DriverManagerDataSource source;
    final GenerationTaskMapper mapper;
    final GenerationFenceMapper fenceMapper;
    final RedissonClient first;
    final RedissonClient second;

    RealGenerationTaskResources() throws Exception {
        var environment = new StandardEnvironment();
        var loader = new YamlPropertySourceLoader();
        // 标准进程环境变量优先，其次本地配置，最后公共配置；仅取数据库和 Redis 字段。
        for (String name : new String[]{"application-local.yml", "application.yml"}) {
            Path path = Path.of("src/main/resources", name);
            if (Files.isRegularFile(path)) {
                for (var property : loader.load("task-it-" + name, new FileSystemResource(path))) {
                    environment.getPropertySources().addLast(property);
                }
            }
        }
        String url = environment.getRequiredProperty("spring.datasource.url");
        URI target = URI.create(url.substring("jdbc:".length()));
        Set<String> local = Set.of("localhost", "127.0.0.1", "::1");
        if (!local.contains(target.getHost()) || !"/yu_ai_code_mother".equals(target.getPath())) {
            throw new IllegalStateException("集成测试仅允许配置中的本地业务库");
        }
        source = new DriverManagerDataSource(url,
                environment.getRequiredProperty("spring.datasource.username"),
                environment.getRequiredProperty("spring.datasource.password"));
        try (var connection = source.getConnection(); var statement = connection.createStatement()) {
            // 预检已迁移的表；失败应保留为真实依赖错误，不能自动跳过或建表掩盖。
            statement.executeQuery("SELECT request_id, execution_state, query_until FROM generation_task LIMIT 0").close();
            statement.executeQuery("SELECT app_id, current_token FROM generation_fence LIMIT 0").close();
            statement.executeQuery("SELECT request_id, fence_token FROM generation_publication LIMIT 0").close();
            statement.executeQuery("SELECT app_id, code_gen_type, request_id FROM generation_active_artifact LIMIT 0").close();
        }
        mapper = newMapper();
        fenceMapper = newFenceMapper();
        String host = environment.getRequiredProperty("spring.data.redis.host");
        int port = environment.getProperty("spring.data.redis.port", Integer.class, 6379);
        int database = environment.getProperty("spring.data.redis.database", Integer.class, 1);
        if (!local.contains(host) || database != 1) {
            throw new IllegalStateException("集成测试仅允许本地 Spring Redis database 1");
        }
        String password = environment.getProperty("spring.data.redis.password");
        first = Redisson.create(redisConfig(host, port, database, password));
        try {
            second = Redisson.create(redisConfig(host, port, database, password));
        } catch (RuntimeException error) {
            first.shutdown();
            throw error;
        }
    }

    GenerationTaskMapper newMapper() throws Exception {
        var configuration = new Configuration(new Environment("task-it",
                new SpringManagedTransactionFactory(), source));
        String resource = "mapper/GenerationTaskMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(resource)) {
            new XMLMapperBuilder(input, configuration, resource, configuration.getSqlFragments()).parse();
        }
        var factory = new SqlSessionFactoryBuilder().build(configuration);
        // 使用 Spring 的真实异常翻译，验证唯一键冲突能够被服务识别为 DuplicateKeyException。
        return new SqlSessionTemplate(factory).getMapper(GenerationTaskMapper.class);
    }

    GenerationFenceMapper newFenceMapper() throws Exception {
        var configuration = new Configuration(new Environment("task-fence-it",
                new SpringManagedTransactionFactory(), source));
        String taskResource = "mapper/GenerationTaskMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(taskResource)) {
            new XMLMapperBuilder(input, configuration, taskResource, configuration.getSqlFragments()).parse();
        }
        String fenceResource = "mapper/GenerationFenceMapper.xml";
        try (var input = getClass().getClassLoader().getResourceAsStream(fenceResource)) {
            new XMLMapperBuilder(input, configuration, fenceResource, configuration.getSqlFragments()).parse();
        }
        return new SqlSessionTemplate(new SqlSessionFactoryBuilder().build(configuration))
                .getMapper(GenerationFenceMapper.class);
    }

    Connection connection() throws Exception {
        return source.getConnection();
    }

    javax.sql.DataSource dataSource() {
        return source;
    }

    private Config redisConfig(String host, int port, int database, String password) {
        var config = new Config().setThreads(2).setNettyThreads(2);
        var server = config.useSingleServer().setAddress("redis://" + host + ":" + port)
                .setDatabase(database).setConnectionMinimumIdleSize(1).setConnectionPoolSize(2)
                .setSubscriptionConnectionMinimumIdleSize(1).setSubscriptionConnectionPoolSize(2)
                .setConnectTimeout(2_000).setTimeout(3_000).setRetryAttempts(0);
        if (password != null && !password.isBlank()) server.setPassword(password);
        return config;
    }

    @Override
    public void close() {
        try { first.shutdown(); }
        finally { second.shutdown(); }
    }
}
