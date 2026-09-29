package com.yupi.yuaicodemother.model.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
@AllArgsConstructor
public class AppGenerationStatusVO implements Serializable {
    @Serial
    private static final long serialVersionUID = 1L;

    private Long appId;
    private String requestId;
    private String state;
}
