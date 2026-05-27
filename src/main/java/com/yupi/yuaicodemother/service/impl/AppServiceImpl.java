package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.mapper.AppMapper;
import com.yupi.yuaicodemother.model.dto.app.AppAddRequest;
import com.yupi.yuaicodemother.model.dto.app.AppAdminUpdateRequest;
import com.yupi.yuaicodemother.model.dto.app.AppQueryRequest;
import com.yupi.yuaicodemother.model.dto.app.AppUserUpdateRequest;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.vo.AppVO;
import com.yupi.yuaicodemother.service.AppService;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * 应用服务实现
 */
@Service
public class AppServiceImpl extends ServiceImpl<AppMapper, App> implements AppService {

    /**
     * 用户侧分页最大条数。
     */
    private static final int USER_PAGE_MAX_SIZE = 20;

    /**
     * 约定 priority > 0 表示精选应用。
     */
    private static final int FEATURED_PRIORITY_THRESHOLD = 0;

    /**
     * 排序字段白名单，避免非法字段直接透传到 SQL。
     */
    private static final Set<String> SORT_FIELD_WHITELIST = Set.of(
            "id", "appName", "cover", "initPrompt", "codeGenType", "deployKey",
            "deployedTime", "priority", "userId", "editTime", "createTime", "updateTime"
    );

    /**
     * 用户创建应用。
     *
     * @param request 创建参数
     * @param loginUserId 当前登录用户 id
     * @return 新应用 id
     */
    @Override
    public Long createApp(AppAddRequest request, Long loginUserId) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "创建应用参数不能为空");
        ThrowUtils.throwIf(loginUserId == null || loginUserId <= 0, ErrorCode.NOT_LOGIN_ERROR, "用户未登录");
        ThrowUtils.throwIf(StrUtil.isBlank(request.getInitPrompt()), ErrorCode.PARAMS_ERROR, "initPrompt 不能为空");

        // 只接收当前业务允许用户填写的字段，归属关系和系统字段由后端补齐。
        App app = new App();
        app.setAppName(StrUtil.trim(request.getAppName()));
        app.setCover(StrUtil.trim(request.getCover()));
        app.setInitPrompt(StrUtil.trim(request.getInitPrompt()));
        app.setCodeGenType(StrUtil.trim(request.getCodeGenType()));
        // 新建应用默认不是精选。
        app.setPriority(0);
        app.setUserId(loginUserId);
        // 创建时顺带初始化编辑时间，便于后续统一做最近编辑排序。
        app.setEditTime(LocalDateTime.now());
        app.setIsDelete(0);
        boolean saved = this.save(app);
        ThrowUtils.throwIf(!saved, ErrorCode.OPERATION_ERROR, "创建应用失败");
        return app.getId();
    }

    /**
     * 用户修改自己的应用。
     *
     * @param appId 应用 id
     * @param request 修改参数
     * @param loginUserId 当前登录用户 id
     * @return 是否修改成功
     */
    @Override
    public boolean updateMyApp(Long appId, AppUserUpdateRequest request, Long loginUserId) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "修改应用参数不能为空");
        ThrowUtils.throwIf(StrUtil.isBlank(request.getAppName()), ErrorCode.PARAMS_ERROR, "应用名称不能为空");

        // 先校验应用归属，确保普通用户只能修改自己的应用。
        App existApp = getMyAppEntity(appId, loginUserId);
        App app = new App();
        app.setId(existApp.getId());
        app.setAppName(StrUtil.trim(request.getAppName()));
        app.setEditTime(LocalDateTime.now());
        return this.updateById(app);
    }

    /**
     * 用户删除自己的应用。
     *
     * @param appId 应用 id
     * @param loginUserId 当前登录用户 id
     * @return 是否删除成功
     */
    @Override
    public boolean removeMyApp(Long appId, Long loginUserId) {
        // 删除前先校验应用存在且归属于当前用户。
        App existApp = getMyAppEntity(appId, loginUserId);
        return this.removeById(existApp.getId());
    }

    /**
     * 用户查看自己的应用详情。
     *
     * @param appId 应用 id
     * @param loginUserId 当前登录用户 id
     * @return 应用详情
     */
    @Override
    public AppVO getMyAppById(Long appId, Long loginUserId) {
        return getAppVO(getMyAppEntity(appId, loginUserId));
    }

    /**
     * 用户分页查询自己的应用列表。
     *
     * @param request 查询参数
     * @param loginUserId 当前登录用户 id
     * @return 分页结果
     */
    @Override
    public Page<AppVO> pageMyApps(AppQueryRequest request, Long loginUserId) {
        // 用户侧分页大小受限，防止一次拉取过多数据。
        validateUserPageRequest(request);
        QueryWrapper queryWrapper = buildMyAppQueryWrapper(request, loginUserId);
        Page<App> appPage = this.page(Page.of(request.getPageNum(), request.getPageSize()), queryWrapper);
        return buildAppVOPage(appPage);
    }

    /**
     * 用户分页查询精选应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    @Override
    public Page<AppVO> pageFeaturedApps(AppQueryRequest request) {
        // 精选列表与用户侧列表保持一致的分页约束。
        validateUserPageRequest(request);
        QueryWrapper queryWrapper = buildFeaturedQueryWrapper(request);
        Page<App> appPage = this.page(Page.of(request.getPageNum(), request.getPageSize()), queryWrapper);
        return buildAppVOPage(appPage);
    }

    /**
     * 管理员删除任意应用。
     *
     * @param appId 应用 id
     * @return 是否删除成功
     */
    @Override
    public boolean adminRemoveApp(Long appId) {
        App existApp = getAppEntityById(appId);
        return this.removeById(existApp.getId());
    }

    /**
     * 管理员更新任意应用。
     *
     * @param appId 应用 id
     * @param request 更新参数
     * @return 是否更新成功
     */
    @Override
    public boolean adminUpdateApp(Long appId, AppAdminUpdateRequest request) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "更新应用参数不能为空");

        // 管理员可以改任意应用，但仍需要先确认目标应用存在。
        App existApp = getAppEntityById(appId);
        App app = new App();
        app.setId(existApp.getId());
        if (request.getAppName() != null) {
            ThrowUtils.throwIf(StrUtil.isBlank(request.getAppName()), ErrorCode.PARAMS_ERROR, "应用名称不能为空");
            app.setAppName(StrUtil.trim(request.getAppName()));
        }
        if (request.getCover() != null) {
            app.setCover(StrUtil.trim(request.getCover()));
        }
        if (request.getPriority() != null) {
            // priority 同时承担排序和“是否精选”的语义。
            app.setPriority(request.getPriority());
        }
        app.setEditTime(LocalDateTime.now());
        return this.updateById(app);
    }

    /**
     * 管理员分页查询应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    @Override
    public Page<AppVO> pageAdminApps(AppQueryRequest request) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "查询参数不能为空");
        ThrowUtils.throwIf(request.getPageNum() <= 0, ErrorCode.PARAMS_ERROR, "页码必须大于 0");
        ThrowUtils.throwIf(request.getPageSize() <= 0, ErrorCode.PARAMS_ERROR, "每页数量必须大于 0");

        // 管理员分页查询不限制 pageSize，按传入字段动态拼接查询条件。
        QueryWrapper queryWrapper = buildAdminQueryWrapper(request);
        Page<App> appPage = this.page(Page.of(request.getPageNum(), request.getPageSize()), queryWrapper);
        return buildAppVOPage(appPage);
    }

    /**
     * 管理员查看应用详情。
     *
     * @param appId 应用 id
     * @return 应用详情
     */
    @Override
    public AppVO getAdminAppById(Long appId) {
        return getAppVO(getAppEntityById(appId));
    }

    /**
     * 将应用实体转换为视图对象。
     *
     * @param app 应用实体
     * @return 应用视图对象
     */
    @Override
    public AppVO getAppVO(App app) {
        if (app == null) {
            return null;
        }
        // 对外返回统一走 VO，方便后续扩展展示字段或裁剪敏感信息。
        AppVO appVO = new AppVO();
        BeanUtil.copyProperties(app, appVO);
        return appVO;
    }

    /**
     * 校验用户侧分页参数。
     *
     * @param request 查询参数
     */
    private void validateUserPageRequest(AppQueryRequest request) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "查询参数不能为空");
        ThrowUtils.throwIf(request.getPageNum() <= 0, ErrorCode.PARAMS_ERROR, "页码必须大于 0");
        ThrowUtils.throwIf(request.getPageSize() <= 0, ErrorCode.PARAMS_ERROR, "每页数量必须大于 0");
        ThrowUtils.throwIf(request.getPageSize() > USER_PAGE_MAX_SIZE, ErrorCode.PARAMS_ERROR, "每页最多查询 20 条");
    }

    /**
     * 按 id 获取应用实体。
     *
     * @param appId 应用 id
     * @return 应用实体
     */
    private App getAppEntityById(Long appId) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 id 非法");
        App app = this.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "应用不存在");
        return app;
    }

    /**
     * 获取当前用户自己的应用实体。
     *
     * @param appId 应用 id
     * @param loginUserId 当前登录用户 id
     * @return 应用实体
     */
    private App getMyAppEntity(Long appId, Long loginUserId) {
        ThrowUtils.throwIf(loginUserId == null || loginUserId <= 0, ErrorCode.NOT_LOGIN_ERROR, "用户未登录");
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 id 非法");
        // 直接带 userId 查询，避免先查出应用再单独比对归属。
        QueryWrapper queryWrapper = QueryWrapper.create()
                .where("id = ? and userId = ?", appId, loginUserId);
        App app = this.getOne(queryWrapper);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "应用不存在");
        return app;
    }

    /**
     * 构建“我的应用”查询条件。
     *
     * @param request 查询参数
     * @param loginUserId 当前登录用户 id
     * @return 查询条件
     */
    private QueryWrapper buildMyAppQueryWrapper(AppQueryRequest request, Long loginUserId) {
        List<String> conditions = new ArrayList<>();
        List<Object> params = new ArrayList<>();
        // “我的应用”固定按当前用户过滤。
        conditions.add("userId = ?");
        params.add(loginUserId);
        if (StrUtil.isNotBlank(request.getAppName())) {
            conditions.add("appName like ?");
            params.add(buildLikeValue(request.getAppName()));
        }
        QueryWrapper queryWrapper = buildWhereQueryWrapper(conditions, params);
        applyOrderBy(queryWrapper, request);
        return queryWrapper;
    }

    /**
     * 构建精选应用查询条件。
     *
     * @param request 查询参数
     * @return 查询条件
     */
    private QueryWrapper buildFeaturedQueryWrapper(AppQueryRequest request) {
        List<String> conditions = new ArrayList<>();
        List<Object> params = new ArrayList<>();
        // 当前通过 priority 大于 0 来识别精选应用。
        conditions.add("priority > ?");
        params.add(FEATURED_PRIORITY_THRESHOLD);
        if (StrUtil.isNotBlank(request.getAppName())) {
            conditions.add("appName like ?");
            params.add(buildLikeValue(request.getAppName()));
        }
        QueryWrapper queryWrapper = buildWhereQueryWrapper(conditions, params);
        applyOrderBy(queryWrapper, request);
        return queryWrapper;
    }

    /**
     * 构建管理员查询条件。
     *
     * @param request 查询参数
     * @return 查询条件
     */
    private QueryWrapper buildAdminQueryWrapper(AppQueryRequest request) {
        List<String> conditions = new ArrayList<>();
        List<Object> params = new ArrayList<>();
        // 管理员支持按除时间外的大部分字段进行筛选。
        if (request.getId() != null) {
            conditions.add("id = ?");
            params.add(request.getId());
        }
        if (StrUtil.isNotBlank(request.getAppName())) {
            conditions.add("appName like ?");
            params.add(buildLikeValue(request.getAppName()));
        }
        if (StrUtil.isNotBlank(request.getCover())) {
            conditions.add("cover like ?");
            params.add(buildLikeValue(request.getCover()));
        }
        if (StrUtil.isNotBlank(request.getInitPrompt())) {
            conditions.add("initPrompt like ?");
            params.add(buildLikeValue(request.getInitPrompt()));
        }
        if (StrUtil.isNotBlank(request.getCodeGenType())) {
            conditions.add("codeGenType like ?");
            params.add(buildLikeValue(request.getCodeGenType()));
        }
        if (StrUtil.isNotBlank(request.getDeployKey())) {
            conditions.add("deployKey like ?");
            params.add(buildLikeValue(request.getDeployKey()));
        }
        if (request.getPriority() != null) {
            conditions.add("priority = ?");
            params.add(request.getPriority());
        }
        if (request.getUserId() != null) {
            conditions.add("userId = ?");
            params.add(request.getUserId());
        }
        QueryWrapper queryWrapper = buildWhereQueryWrapper(conditions, params);
        applyOrderBy(queryWrapper, request);
        return queryWrapper;
    }

    /**
     * 统一拼装 where 条件。
     *
     * @param conditions 条件片段
     * @param params 占位参数
     * @return 查询条件
     */
    private QueryWrapper buildWhereQueryWrapper(List<String> conditions, List<Object> params) {
        QueryWrapper queryWrapper = QueryWrapper.create();
        if (!conditions.isEmpty()) {
            // 使用占位符传参，避免直接字符串拼接带来的风险。
            queryWrapper.where(String.join(" and ", conditions), params.toArray());
        }
        return queryWrapper;
    }

    /**
     * 统一处理排序逻辑。
     *
     * @param queryWrapper 查询条件
     * @param request 查询参数
     */
    private void applyOrderBy(QueryWrapper queryWrapper, AppQueryRequest request) {
        String sortField = request.getSortField();
        if (StrUtil.isNotBlank(sortField)) {
            // 仅允许白名单字段参与排序，防止非法字段透传。
            ThrowUtils.throwIf(!SORT_FIELD_WHITELIST.contains(sortField), ErrorCode.PARAMS_ERROR, "排序字段非法");
            queryWrapper.orderBy(sortField, "descend".equalsIgnoreCase(request.getSortOrder()));
            return;
        }
        // 默认按创建时间倒序，优先展示较新的应用。
        queryWrapper.orderBy("createTime", true);
    }

    /**
     * 将应用分页结果转换为视图对象分页结果。
     *
     * @param appPage 应用分页结果
     * @return 视图对象分页结果
     */
    private Page<AppVO> buildAppVOPage(Page<App> appPage) {
        Page<AppVO> appVOPage = Page.of(appPage.getPageNumber(), appPage.getPageSize());
        appVOPage.setTotalRow(appPage.getTotalRow());
        appVOPage.setTotalPage(appPage.getTotalPage());
        // 分页元信息保持不变，仅转换 records 的元素类型。
        appVOPage.setRecords(appPage.getRecords().stream().map(this::getAppVO).toList());
        return appVOPage;
    }

    /**
     * 构造 like 查询值。
     *
     * @param value 原始值
     * @return like 查询值
     */
    private String buildLikeValue(String value) {
        return "%" + StrUtil.trim(value) + "%";
    }
}
