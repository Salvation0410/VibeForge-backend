package com.yupi.yuaicodemother.config;

import com.yupi.yuaicodemother.manager.MailManager;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration;
import org.springframework.boot.autoconfigure.mail.MailSenderAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.cache.RedisCacheManager;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.mail.javamail.JavaMailSender;

import static org.assertj.core.api.Assertions.assertThat;

class LocalInfrastructureConfigurationTest {

    @Test
    void mailManagerRequiresSmtpConfiguration() {
        // 缺少本地邮件配置时应保留启动失败，不能用空发送器掩盖配置问题。
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(MailSenderAutoConfiguration.class))
                .withUserConfiguration(MailManager.class)
                .run(context -> assertThat(context).hasFailed());
    }

    @Test
    void configuredMailManagerStartsWithoutSendingMail() {
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(MailSenderAutoConfiguration.class))
                .withUserConfiguration(MailManager.class)
                .withPropertyValues("spring.mail.host=smtp.example.invalid",
                        "spring.mail.username=test@example.invalid")
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(JavaMailSender.class);
                    assertThat(context).hasSingleBean(MailManager.class);
                });
    }

    @Test
    void redisInfrastructureStartsWithoutJavaAiDependencies() {
        // 使用真实自动配置和缓存配置验证运行时客户端齐备，不建立 Redis 网络连接。
        new ApplicationContextRunner()
                .withConfiguration(AutoConfigurations.of(RedisAutoConfiguration.class))
                .withUserConfiguration(RedisCacheManagerConfig.class)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    assertThat(context).hasSingleBean(RedisConnectionFactory.class);
                    assertThat(context).hasSingleBean(StringRedisTemplate.class);
                    assertThat(context).hasSingleBean(RedisCacheManager.class);
                });
    }
}
