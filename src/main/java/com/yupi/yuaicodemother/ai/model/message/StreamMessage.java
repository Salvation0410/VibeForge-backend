package com.yupi.yuaicodemother.ai.model.message;

/**
 * @author huang
 * @version 1.0
 * @description 流式相应基类
 * @date 2026/5/30
 */

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;


@Data
@AllArgsConstructor
@NoArgsConstructor
public class StreamMessage {
    private String type;
}
