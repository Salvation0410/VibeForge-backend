package com.yupi.yuaicodemother.model.dto.app;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class AppGenerationCancelRequest implements Serializable {
    @Serial
    private static final long serialVersionUID = 1L;

    private Long appId;
}
