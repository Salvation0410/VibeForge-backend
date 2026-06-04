package com.yupi.yuaicodemother.controller;

import com.mybatisflex.core.query.QueryWrapper;
import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.enums.CommunityPostStatusEnum;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.ChatHistory;
import com.yupi.yuaicodemother.model.entity.CommunityComment;
import com.yupi.yuaicodemother.model.entity.CommunityPost;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.AdminDashboardVO;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import com.yupi.yuaicodemother.service.CommunityCommentService;
import com.yupi.yuaicodemother.service.CommunityPostService;
import com.yupi.yuaicodemother.service.SysUserService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/admin/dashboard")
@RequiredArgsConstructor
public class AdminDashboardController {

    private static final int TREND_DAYS = 7;
    private static final DateTimeFormatter DAY_LABEL_FORMATTER = DateTimeFormatter.ofPattern("MM-dd");

    private final SysUserService sysUserService;
    private final AppService appService;
    private final CommunityPostService communityPostService;
    private final CommunityCommentService communityCommentService;
    private final ChatHistoryService chatHistoryService;

    /**
     * 提供后台首页可视化数据，前端可直接交给 ECharts 渲染。
     */
    @GetMapping("/overview")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<AdminDashboardVO> getOverview() {
        AdminDashboardVO dashboard = new AdminDashboardVO();
        dashboard.setMetrics(buildMetrics());
        dashboard.setTrend(buildTrendData());
        dashboard.setPostStatusDistribution(buildPostStatusDistribution());
        dashboard.setAppTypeDistribution(buildAppTypeDistribution());
        return ResultUtils.success(dashboard);
    }

    private List<AdminDashboardVO.MetricItem> buildMetrics() {
        long pendingPosts = communityPostService.count(QueryWrapper.create()
                .eq("status", CommunityPostStatusEnum.PENDING.getValue()));
        return List.of(
                new AdminDashboardVO.MetricItem("用户数", sysUserService.count(), "平台注册账号"),
                new AdminDashboardVO.MetricItem("应用数", appService.count(), "用户创建的应用"),
                new AdminDashboardVO.MetricItem("待审帖子", pendingPosts, "需要管理员处理"),
                new AdminDashboardVO.MetricItem("评论数", communityCommentService.count(), "社区互动总量"),
                new AdminDashboardVO.MetricItem("对话数", chatHistoryService.count(), "应用生成对话")
        );
    }

    private AdminDashboardVO.TrendData buildTrendData() {
        List<LocalDate> days = buildRecentDays();
        LocalDateTime startTime = days.get(0).atStartOfDay();
        return new AdminDashboardVO.TrendData(
                days.stream().map(DAY_LABEL_FORMATTER::format).toList(),
                // sys_user 表使用下划线字段名，不能和社区/应用表一样写 createTime。
                countByDay(sysUserService.list(QueryWrapper.create().ge("create_time", startTime)), SysUser::getCreateTime, days),
                countByDay(appService.list(QueryWrapper.create().ge("createTime", startTime)), App::getCreateTime, days),
                countByDay(communityPostService.list(QueryWrapper.create().ge("createTime", startTime)), CommunityPost::getCreateTime, days),
                countByDay(communityCommentService.list(QueryWrapper.create().ge("createTime", startTime)), CommunityComment::getCreateTime, days),
                countByDay(chatHistoryService.list(QueryWrapper.create().ge("createTime", startTime)), ChatHistory::getCreateTime, days)
        );
    }

    private List<AdminDashboardVO.ChartItem> buildPostStatusDistribution() {
        List<AdminDashboardVO.ChartItem> items = new ArrayList<>();
        for (CommunityPostStatusEnum status : CommunityPostStatusEnum.values()) {
            items.add(new AdminDashboardVO.ChartItem(status.getValue(), communityPostService.count(
                    QueryWrapper.create().eq("status", status.getValue()))));
        }
        return items;
    }

    private List<AdminDashboardVO.ChartItem> buildAppTypeDistribution() {
        Map<String, Long> appTypeCount = appService.list().stream()
                .collect(Collectors.groupingBy(app -> normalizeType(app.getCodeGenType()), LinkedHashMap::new, Collectors.counting()));
        return appTypeCount.entrySet().stream()
                .map(entry -> new AdminDashboardVO.ChartItem(entry.getKey(), entry.getValue()))
                .toList();
    }

    private List<LocalDate> buildRecentDays() {
        LocalDate today = LocalDate.now();
        List<LocalDate> days = new ArrayList<>();
        for (int i = TREND_DAYS - 1; i >= 0; i--) {
            days.add(today.minusDays(i));
        }
        return days;
    }

    private <T> List<Long> countByDay(List<T> records, Function<T, LocalDateTime> timeGetter, List<LocalDate> days) {
        Map<LocalDate, Long> countMap = records.stream()
                .map(timeGetter)
                .filter(time -> time != null)
                .collect(Collectors.groupingBy(LocalDateTime::toLocalDate, Collectors.counting()));
        return days.stream().map(day -> Optional.ofNullable(countMap.get(day)).orElse(0L)).toList();
    }

    private String normalizeType(String codeGenType) {
        if (codeGenType == null || codeGenType.isBlank()) {
            return "UNKNOWN";
        }
        return codeGenType.trim().toUpperCase(Locale.ROOT);
    }
}
