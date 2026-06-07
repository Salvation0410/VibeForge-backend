package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingService;
import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingServiceFactory;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorServiceFactory;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.core.AiCodeGeneratorFacade;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
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
    private final AiCodeGeneratorFacade aiCodeGeneratorFacade;
    private final ChatHistoryService chatHistoryService;

    @Resource
    private StreamHandlerExecutor streamHandlerExecutor;

    @Resource
    private VueProjectBuilder vueProjectBuilder;

    @Resource
    private ScreenshotService screenshotService;

    @Resource
    private ChatHistoryOriginalService chatHistoryOriginalService;

    //智能路由创建工厂
    @Resource
    private AiCodeGenTypeRoutingServiceFactory aiCodeGenTypeRoutingServiceFactory;

    /**
     * 对话生成代码
     * @param appId 应用id
     * @param message 用户消息
     * @param loginUser 登录用户
     * @return
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

        chatHistoryService.addChatMessage(
                appId,
                message,
                ChatHistoryMessageTypeEnum.USER.getValue(),
                loginUser.getId()
        );
        chatHistoryOriginalService.addOriginalChatMessage(
                appId,
                message,
                ChatHistoryMessageTypeEnum.USER.getValue(),
                loginUser.getId()
        );

        Flux<String> contentStream = aiCodeGeneratorFacade.generateAndSaveCodeStream(message, codeGenTypeEnum, appId);
        return streamHandlerExecutor.doExecute(contentStream, chatHistoryService, chatHistoryOriginalService, appId, loginUser, codeGenTypeEnum);
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
        AiCodeGenTypeRoutingService routingService = aiCodeGenTypeRoutingServiceFactory.createAiCodeGenTypeRoutingService();
        CodeGenTypeEnum selectedCodeGenType = routingService.routeCodeGenType(initPrompt);
        app.setCodeGenType(selectedCodeGenType.getValue());
        boolean result = this.save(app);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR);
        log.info("App created successfully, id: {}, type: {}", app.getId(), selectedCodeGenType.getValue());
        return app.getId();
    }

    private File resolveSourceRootDir(Long appId, String codeGenType) {
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
