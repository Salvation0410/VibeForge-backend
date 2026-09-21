package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.ai.gateway.AiGenerationGateway;
import com.yupi.yuaicodemother.ai.gateway.GenerationLeaseService;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.HtmlOutputBudgetGuard;
import com.yupi.yuaicodemother.core.handler.StreamHandlerExecutor;
import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.mapper.AppMapper;
import com.yupi.yuaicodemother.model.dto.app.AppAddRequest;
import com.yupi.yuaicodemother.model.dto.app.AppQueryRequest;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.AppVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.ChatHistoryOriginalService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import com.yupi.yuaicodemother.service.ScreenshotService;
import com.yupi.yuaicodemother.service.SysUserService;
import com.yupi.yuaicodemother.utils.SpringContextUtil;
import jakarta.annotation.Resource;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import reactor.core.publisher.Flux;

import java.io.File;
import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class AppServiceImpl extends ServiceImpl<AppMapper, App> implements AppService {

    private final SysUserService userService;
    private final AiGenerationGateway aiGenerationGateway;
    private final ChatHistoryService chatHistoryService;
    private final ArtifactPathResolver artifactPathResolver;
    private final GenerationLeaseService generationLeaseService;
    private final HtmlOutputBudgetGuard htmlOutputBudgetGuard;

    @Resource
    private StreamHandlerExecutor streamHandlerExecutor;

    @Resource
    private VueProjectBuilder vueProjectBuilder;

    @Resource
    private ScreenshotService screenshotService;

    @Resource
    private ChatHistoryOriginalService chatHistoryOriginalService;

    /**
     * 校验应用访问权限、记录用户消息，并通过统一 AI 网关执行流式代码生成。
     * <p>
     * 网关会根据引擎配置选择 Legacy 或 LangGraph 实现，返回的数据继续交给既有流处理器，
     * 由其完成消息格式转换和聊天记录持久化。
     *
     * @param appId 要生成或修改代码的应用 ID
     * @param message 用户本次输入的生成需求
     * @param loginUser 当前登录用户
     * @return 面向控制器 SSE 响应的字符串数据流
     * @throws BusinessException 应用不存在、用户无权访问或生成类型不受支持时抛出
     */
    @Override
    public Flux<String> chatToGenCode(Long appId, String message, SysUser loginUser) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "appId is invalid");
        ThrowUtils.throwIf(StrUtil.isBlank(message), ErrorCode.PARAMS_ERROR, "message must not be blank");

        App app = this.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "App not found");
        if (!app.getUserId().equals(loginUser.getId())) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "No permission to access app");
        }

        CodeGenTypeEnum codeGenTypeEnum = CodeGenTypeEnum.getEnumByValue(app.getCodeGenType());
        if (codeGenTypeEnum == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Unsupported code generation type");
        }

        String requestId = java.util.UUID.randomUUID().toString();
        // 租约覆盖消息入库、模型调用和产物发布，防止同一应用的上下文与版本交叉。
        return Flux.defer(() -> {
            var lease = generationLeaseService.acquire(appId, requestId);
            try {
                // 在写入历史和调用模型前拒绝超预算的 HTML 全量重写，失败时保留当前活动版本。
                htmlOutputBudgetGuard.checkRewriteAllowed(codeGenTypeEnum, appId);
                chatHistoryService.addChatMessage(appId, message, ChatHistoryMessageTypeEnum.USER.getValue(), loginUser.getId());
                chatHistoryOriginalService.addOriginalChatMessage(appId, message,
                        ChatHistoryMessageTypeEnum.USER.getValue(), loginUser.getId());
                Flux<String> contentStream = aiGenerationGateway.generate(message, codeGenTypeEnum, appId,
                        loginUser.getId(), requestId);
                Flux<String> execution = streamHandlerExecutor.doExecute(contentStream, chatHistoryService,
                                chatHistoryOriginalService, appId, loginUser, codeGenTypeEnum)
                        .doFinally(signal -> generationLeaseService.release(lease));
                // 零历史缓存让下游取消后生成仍能走到真实终态并释放租约，同时不在内存保留代码块。
                return execution.cache(0)
                        .doOnCancel(() -> {
                            // 取消只有在提交尚未开始时才能胜出；提交胜出后保持成功终态。
                            if (generationLeaseService.cancel(lease)) {
                                aiGenerationGateway.cancel(appId, loginUser.getId(), requestId);
                            }
                        });
            } catch (Throwable error) {
                generationLeaseService.release(lease);
                return Flux.error(error);
            }
        }).onErrorMap(error -> wrapGenerationError(requestId, error));
    }

    /**
     * 将底层模型、校验或发布异常转换为可通过 SSE 返回的稳定业务错误。
     */
    private GenerationStreamException wrapGenerationError(String requestId, Throwable error) {
        if (error instanceof GenerationStreamException streamError) return streamError;
        String stableCode = error instanceof ArtifactValidationException validation
                ? validation.getErrorCode() : "GENERATION_FAILED";
        int code = error instanceof BusinessException business ? business.getCode() : ErrorCode.OPERATION_ERROR.getCode();
        String message = error.getMessage() == null ? "生成失败，已保留上一版本" : error.getMessage();
        return new GenerationStreamException(code, stableCode, requestId, message, error);
    }

    /**
     * 部署应用
     * @param appId 应用id
     * @param loginUser 登录用户
     * @return
     */
    @Override
    public String deployApp(Long appId, SysUser loginUser) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "appId is invalid");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR, "User not logged in");

        App app = this.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "App not found");
        if (!app.getUserId().equals(loginUser.getId())) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "No permission to deploy app");
        }

        String deployKey = app.getDeployKey();
        if (StrUtil.isBlank(deployKey)) {
            deployKey = RandomUtil.randomString(6);
        }

        File sourceRootDir = resolveSourceRootDir(appId, app.getCodeGenType());
        ThrowUtils.throwIf(!sourceRootDir.isDirectory(), ErrorCode.SYSTEM_ERROR, "Source code directory not found");

        File sourceDir = resolveDeploySourceDir(sourceRootDir);
        File deployDir = new File(AppConstant.CODE_DEPLOY_ROOT_DIR, deployKey);
        try {
            if (deployDir.exists()) {
                FileUtil.del(deployDir);
            }
            FileUtil.mkdir(deployDir);
            FileUtil.copyContent(sourceDir, deployDir, true);
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Deploy failed: " + e.getMessage());
        }

        App updateApp = new App();
        updateApp.setId(appId);
        updateApp.setDeployKey(deployKey);
        updateApp.setDeployedTime(LocalDateTime.now());
        boolean updateResult = this.updateById(updateApp);
        ThrowUtils.throwIf(!updateResult, ErrorCode.OPERATION_ERROR, "Failed to update deploy info");

        String appDeployUrl = String.format("%s/%s/", AppConstant.CODE_DEPLOY_HOST, deployKey);
        generateAppScreenshotAsync(appId, appDeployUrl);
        return appDeployUrl;
    }

    @Override
    public void generateAppScreenshotAsync(Long appId, String appUrl) {
        Thread.startVirtualThread(() -> {
            String screenshotUrl = screenshotService.generateAndUploadScreenshot(appUrl);
            App updateApp = new App();
            updateApp.setId(appId);
            updateApp.setCover(screenshotUrl);
            boolean updated = this.updateById(updateApp);
            ThrowUtils.throwIf(!updated, ErrorCode.OPERATION_ERROR, "Failed to update cover");
        });
    }

    /**
     * 创建应用并通过统一 AI 网关判断初始代码生成类型。
     * <p>
     * 方法根据初始提示词设置应用名称和所属用户，完成类型路由后持久化应用，
     * 返回数据库生成的应用 ID。
     *
     * @param appAddRequest 包含初始提示词的应用创建请求
     * @param loginUser 当前登录用户
     * @return 新建应用 ID
     * @throws BusinessException 初始提示词为空、类型路由失败或应用保存失败时抛出
     */
    @Override
    public Long createApp(AppAddRequest appAddRequest, SysUser loginUser) {
        //参数校验
        String initPrompt = appAddRequest.getInitPrompt();
        ThrowUtils.throwIf(StrUtil.isBlank(initPrompt), ErrorCode.PARAMS_ERROR, "initPrompt must not be blank");

        App app = new App();
        BeanUtil.copyProperties(appAddRequest, app);
        app.setUserId(loginUser.getId());
        app.setAppName(initPrompt.substring(0, Math.min(initPrompt.length(), 12)));
        //使用Ai 智能选择代码生成类型
        String requestId = java.util.UUID.randomUUID().toString();
        // TODO 路由单独选择一个智能体进行路由
        CodeGenTypeEnum selectedCodeGenType = aiGenerationGateway.route(initPrompt, null,
                loginUser.getId(), requestId);
        app.setCodeGenType(selectedCodeGenType.getValue());
        boolean result = this.save(app);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR);
        log.info("App created successfully, id: {}, type: {}", app.getId(), selectedCodeGenType.getValue());
        return app.getId();
    }

    /**
     * 解析部署使用的源码根目录；多文件应用优先返回当前已提交版本，失败候选不可见。
     */
    private File resolveSourceRootDir(Long appId, String codeGenType) {
        CodeGenTypeEnum type = CodeGenTypeEnum.getEnumByValue(codeGenType);
        if (type != null) {
            // 部署读取当前已提交版本，生成失败时继续使用上一成功版本。
            File active = artifactPathResolver.resolveActiveRoot(type, appId).toFile();
            if (active.isDirectory()) return active;
        }
        if (StrUtil.isNotBlank(codeGenType)) {
            File sourceDir = new File(AppConstant.CODE_OUTPUT_ROOT_DIR, codeGenType + "_" + appId);
            if (sourceDir.isDirectory()) {
                return sourceDir;
            }
        }

        File vueProjectDir = new File(
                AppConstant.CODE_OUTPUT_ROOT_DIR,
                CodeGenTypeEnum.VUE_PROJECT.getValue() + "_" + appId
        );
        if (vueProjectDir.isDirectory()) {
            log.info("Use vue project directory as source root: {}", vueProjectDir.getAbsolutePath());
            return vueProjectDir;
        }

        return new File(AppConstant.CODE_OUTPUT_ROOT_DIR, codeGenType + "_" + appId);
    }

    private File resolveDeploySourceDir(File sourceRootDir) {
        if (!vueProjectBuilder.isVueProject(sourceRootDir)) {
            return sourceRootDir;
        }

        boolean buildSuccess = vueProjectBuilder.buildProject(sourceRootDir.getAbsolutePath());
        ThrowUtils.throwIf(!buildSuccess, ErrorCode.SYSTEM_ERROR, "Vue build failed");
        ThrowUtils.throwIf(!vueProjectBuilder.hasReadyDist(sourceRootDir), ErrorCode.SYSTEM_ERROR, "Vue dist is missing");

        File distDir = vueProjectBuilder.getDistDirectory(sourceRootDir);
        log.info("Vue build finished, deploy dist directory: {}", distDir.getAbsolutePath());
        return distDir;
    }

    @Override
    @Transactional
    public boolean removeById(Serializable id) {
        if (id == null) {
            return false;
        }
        Long appId = Long.valueOf(id.toString());
        if (appId <= 0) {
            return false;
        }
        try {
            chatHistoryService.deleteByAppId(appId);
            chatHistoryOriginalService.deleteByAppId(appId);
        } catch (Exception e) {
            log.error("Failed to delete app related chat history: {}", e.getMessage(), e);
        }
        return super.removeById(id);
    }

    @Override
    public AppVO getAppVO(App app) {
        if (app == null) {
            return null;
        }
        AppVO appVO = new AppVO();
        BeanUtil.copyProperties(app, appVO);
        Long userId = app.getUserId();
        if (userId != null) {
            SysUser user = userService.getById(userId);
            SysUserVO userVO = userService.getSysUserVO(user);
            appVO.setUser(userVO);
        }
        return appVO;
    }

    @Override
    public QueryWrapper getQueryWrapper(AppQueryRequest appQueryRequest) {
        if (appQueryRequest == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "Request must not be null");
        }
        Long id = appQueryRequest.getId();
        String appName = appQueryRequest.getAppName();
        String cover = appQueryRequest.getCover();
        String initPrompt = appQueryRequest.getInitPrompt();
        String codeGenType = appQueryRequest.getCodeGenType();
        String deployKey = appQueryRequest.getDeployKey();
        Integer priority = appQueryRequest.getPriority();
        Long userId = appQueryRequest.getUserId();
        String sortField = appQueryRequest.getSortField();
        String sortOrder = appQueryRequest.getSortOrder();
        return QueryWrapper.create()
                .eq("id", id)
                .like("appName", appName)
                .like("cover", cover)
                .like("initPrompt", initPrompt)
                .eq("codeGenType", codeGenType)
                .eq("deployKey", deployKey)
                .eq("priority", priority)
                .eq("userId", userId)
                .orderBy(sortField, "ascend".equals(sortOrder));
    }

    @Override
    public Page<AppVO> listPublicAppVOByUser(AppQueryRequest appQueryRequest, Long userId) {
        ThrowUtils.throwIf(userId == null || userId <= 0, ErrorCode.PARAMS_ERROR, "Invalid userId");
        ThrowUtils.throwIf(appQueryRequest == null, ErrorCode.PARAMS_ERROR, "Request must not be null");
        long pageNum = appQueryRequest.getPageNum() <= 0 ? 1 : appQueryRequest.getPageNum();
        long pageSize = appQueryRequest.getPageSize() <= 0 ? 10 : appQueryRequest.getPageSize();
        ThrowUtils.throwIf(pageSize > 20, ErrorCode.PARAMS_ERROR, "pageSize must be <= 20");

        AppQueryRequest publicQuery = new AppQueryRequest();
        BeanUtil.copyProperties(appQueryRequest, publicQuery);
        publicQuery.setUserId(userId);
        if (StrUtil.isBlank(publicQuery.getSortField())) {
            publicQuery.setSortField("createTime");
            publicQuery.setSortOrder("descend");
        }
        QueryWrapper queryWrapper = getQueryWrapper(publicQuery)
                .isNotNull("deployKey")
                .ne("deployKey", "");
        Page<App> appPage = this.page(Page.of(pageNum, pageSize), queryWrapper);
        Page<AppVO> appVOPage = new Page<>(pageNum, pageSize, appPage.getTotalRow());
        appVOPage.setRecords(getPublicAppVOList(appPage.getRecords()));
        return appVOPage;
    }

    @Override
    public List<AppVO> getAppVOList(List<App> appList) {
        if (CollUtil.isEmpty(appList)) {
            return new ArrayList<>();
        }
        Set<Long> userIds = appList.stream()
                .map(App::getUserId)
                .collect(Collectors.toSet());
        Map<Long, SysUserVO> userVOMap = userService.listByIds(userIds).stream()
                .collect(Collectors.toMap(SysUser::getId, userService::getSysUserVO));
        return appList.stream().map(app -> {
            AppVO appVO = new AppVO();
            BeanUtil.copyProperties(app, appVO);
            SysUserVO userVO = userVOMap.get(app.getUserId());
            appVO.setUser(userVO);
            return appVO;
        }).collect(Collectors.toList());
    }

    private List<AppVO> getPublicAppVOList(List<App> appList) {
        List<AppVO> appVOList = getAppVOList(appList);
        appVOList.forEach(appVO -> {
            appVO.setInitPrompt(null);
            appVO.setDeployKey(null);
            appVO.setDeployedTime(null);
            appVO.setPriority(null);
        });
        return appVOList;
    }
}
