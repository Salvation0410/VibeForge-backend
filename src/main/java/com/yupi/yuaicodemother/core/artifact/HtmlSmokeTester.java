package com.yupi.yuaicodemother.core.artifact;

import java.nio.file.Path;

/** 在隔离的 staging 文件上验证 HTML 可执行性，不得修改候选文件。 */
@FunctionalInterface
public interface HtmlSmokeTester {
    HtmlSmokeTestResult verify(Path indexHtml);
}
