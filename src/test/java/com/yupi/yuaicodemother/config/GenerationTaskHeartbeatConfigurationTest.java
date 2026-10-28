package com.yupi.yuaicodemother.config;

import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertTrue;

/** 验证 Spring 的实际默认调度选择；避免唯一自定义 scheduler 被既有任务意外共用。 */
class GenerationTaskHeartbeatConfigurationTest {
    @EnableScheduling
    static class Scheduling { }

    static class Probes {
        final CountDownLatch defaultStarted = new CountDownLatch(1);
        final CountDownLatch releaseDefault = new CountDownLatch(1);
        final CountDownLatch heartbeat = new CountDownLatch(1);

        @Scheduled(fixedDelay = 60_000)
        public void blockingDefaultTask() throws InterruptedException {
            defaultStarted.countDown();
            releaseDefault.await(5, TimeUnit.SECONDS);
        }

        @Scheduled(fixedDelay = 10, scheduler = "generationTaskHeartbeatScheduler")
        public void heartbeatTask() {
            if (defaultStarted.getCount() == 0) heartbeat.countDown();
        }
    }

    @Test
    void longDefaultTaskCannotBlockHeartbeatScheduler() throws Exception {
        var probes = new Probes();
        try (var context = new AnnotationConfigApplicationContext()) {
            context.register(GenerationTaskHeartbeatConfiguration.class, Scheduling.class);
            context.registerBean(Probes.class, () -> probes);
            context.refresh();
            try {
                assertTrue(probes.defaultStarted.await(2, TimeUnit.SECONDS));
                assertTrue(probes.heartbeat.await(2, TimeUnit.SECONDS));
            } finally {
                probes.releaseDefault.countDown();
            }
        }
    }
}
