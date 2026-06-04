package com.yupi.yuaicodemother.model.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serial;
import java.io.Serializable;
import java.util.List;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class AdminDashboardVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private List<MetricItem> metrics;

    private TrendData trend;

    private List<ChartItem> postStatusDistribution;

    private List<ChartItem> appTypeDistribution;

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class MetricItem implements Serializable {

        @Serial
        private static final long serialVersionUID = 1L;

        private String label;

        private Long value;

        private String hint;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class TrendData implements Serializable {

        @Serial
        private static final long serialVersionUID = 1L;

        private List<String> dates;

        private List<Long> users;

        private List<Long> apps;

        private List<Long> posts;

        private List<Long> comments;

        private List<Long> chats;
    }

    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ChartItem implements Serializable {

        @Serial
        private static final long serialVersionUID = 1L;

        private String name;

        private Long value;
    }
}
