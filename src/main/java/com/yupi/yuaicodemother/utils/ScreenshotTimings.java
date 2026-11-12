package com.yupi.yuaicodemother.utils;

import lombok.extern.slf4j.Slf4j;

import java.util.UUID;
import java.util.function.Supplier;

/** 截图诊断只记录固定阶段、结果和异常类型，禁止记录地址、路径或异常正文。 */
@Slf4j
public final class ScreenshotTimings {
    public enum Stage { DRIVER_INIT, NAVIGATE, READY_WAIT, CAPTURE, COMPRESS, DRIVER_CLOSE, UPLOAD, CLEANUP }
    public enum Result { SUCCESS, FAILED, DEGRADED }

    private ScreenshotTimings() { }

    public static String newId() {
        return UUID.randomUUID().toString();
    }

    public static <T> T measure(String id, Stage stage, Supplier<T> operation) {
        long started = System.nanoTime();
        try {
            T result = operation.get();
            record(id, stage, Result.SUCCESS, started, null);
            return result;
        } catch (RuntimeException e) {
            record(id, stage, Result.FAILED, started, e);
            throw e;
        }
    }

    static void record(String id, Stage stage, Result result, long started, Throwable failure) {
        log.info("screenshot id={} stage={} result={} elapsed_ms={} exception_type={}",
                id, stage.name().toLowerCase(java.util.Locale.ROOT), result.name().toLowerCase(java.util.Locale.ROOT),
                Math.max(0, (System.nanoTime() - started) / 1_000_000),
                failure == null ? "none" : failure.getClass().getSimpleName());
    }
}
