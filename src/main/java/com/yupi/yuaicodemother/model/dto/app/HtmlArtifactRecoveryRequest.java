package com.yupi.yuaicodemother.model.dto.app;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/** 管理员人工恢复单文件 HTML 的请求参数。 */
@Data
public class HtmlArtifactRecoveryRequest implements Serializable {
    @Serial
    private static final long serialVersionUID = 1L;

    private Long appId;
    private String requestId;
    private String candidateHtml;
    private String sourceDescription;

    /** 未显式提交时始终执行只读验证，防止运维调用意外改变活动版本。 */
    private boolean dryRun = true;

    /** 只有明确传入 false 才进入提交模式；缺省值或 JSON null 均保持只读。 */
    public void setDryRun(Boolean dryRun) {
        this.dryRun = dryRun == null || dryRun;
    }
}
