package com.yupi.yuaicodemother.model.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class CustomerServiceAnswerVO {
    private boolean answered;
    private String answer;
    private List<Source> sources;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class Source {
        private String documentId;
        private String documentName;
        private long documentVersion;
        private String chunkId;
        private String locator;
        private String excerpt;
    }
}
