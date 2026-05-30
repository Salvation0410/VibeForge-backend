package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
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
import com.yupi.yuaicodemother.model.dto.app.AppQueryRequest;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.AppVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import com.yupi.yuaicodemother.service.SysUserService;
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

    @Override
    public Flux<String> chatToGenCode(Long appId, String message, SysUser loginUser) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 不能为空");
        ThrowUtils.throwIf(StrUtil.isBlank(message), ErrorCode.PARAMS_ERROR, "用户消息不能为空");

        App app = this.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "应用不存在");
        if (!app.getUserId().equals(loginUser.getId())) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限访问该应用");
        }

        String codeGenTypeStr = app.getCodeGenType();
        CodeGenTypeEnum codeGenTypeEnum = CodeGenTypeEnum.getEnumByValue(codeGenTypeStr);
        if (codeGenTypeEnum == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "不支持的代码生成类型");
        }

        chatHistoryService.addChatMessage(
                appId,
                message,
                ChatHistoryMessageTypeEnum.USER.getValue(),
                loginUser.getId()
        );

        Flux<String> contentStream = aiCodeGeneratorFacade.generateAndSaveCodeStream(message, codeGenTypeEnum, appId);
        return streamHandlerExecutor.doExecute(contentStream, chatHistoryService, appId, loginUser, codeGenTypeEnum);
    }

    @Override
    public String deployApp(Long appId, SysUser loginUser) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 不能为空");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR, "用户未登录");

        App app = this.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "应用不存在");
        if (!app.getUserId().equals(loginUser.getId())) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限部署该应用");
        }

        String deployKey = app.getDeployKey();
        if (StrUtil.isBlank(deployKey)) {
            deployKey = RandomUtil.randomString(6);
        }

        File sourceRootDir = resolveSourceRootDir(appId, app.getCodeGenType());
        ThrowUtils.throwIf(!sourceRootDir.isDirectory(), ErrorCode.SYSTEM_ERROR, "应用代码不存在，请先生成代码");

        File sourceDir = resolveDeploySourceDir(sourceRootDir);
        File deployDir = new File(AppConstant.CODE_DEPLOY_ROOT_DIR, deployKey);
        try {
            if (deployDir.exists()) {
                FileUtil.del(deployDir);
            }
            FileUtil.mkdir(deployDir);
            FileUtil.copyContent(sourceDir, deployDir, true);
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "部署失败：" + e.getMessage());
        }

        App updateApp = new App();
        updateApp.setId(appId);
        updateApp.setDeployKey(deployKey);
        updateApp.setDeployedTime(LocalDateTime.now());
        boolean updateResult = this.updateById(updateApp);
        ThrowUtils.throwIf(!updateResult, ErrorCode.OPERATION_ERROR, "更新应用部署信息失败");

        return String.format("%s/%s/", AppConstant.CODE_DEPLOY_HOST, deployKey);
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
            log.info("应用 {} 使用 Vue 项目目录作为代码源: {}", appId, vueProjectDir.getAbsolutePath());
            return vueProjectDir;
        }

        return new File(AppConstant.CODE_OUTPUT_ROOT_DIR, codeGenType + "_" + appId);
    }

    private File resolveDeploySourceDir(File sourceRootDir) {
        if (!vueProjectBuilder.isVueProject(sourceRootDir)) {
            return sourceRootDir;
        }

        boolean buildSuccess = vueProjectBuilder.buildProject(sourceRootDir.getAbsolutePath());
        ThrowUtils.throwIf(!buildSuccess, ErrorCode.SYSTEM_ERROR, "Vue 项目构建失败，请检查代码和依赖");
        ThrowUtils.throwIf(
                !vueProjectBuilder.hasReadyDist(sourceRootDir),
                ErrorCode.SYSTEM_ERROR,
                "Vue 项目尚未生成可部署的 dist/index.html"
        );

        File distDir = vueProjectBuilder.getDistDirectory(sourceRootDir);
        log.info("Vue 项目构建成功，将部署 dist 目录: {}", distDir.getAbsolutePath());
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
        } catch (Exception e) {
            log.error("删除应用关联对话历史失败: {}", e.getMessage(), e);
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
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "请求参数为空");
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
            AppVO appVO = getAppVO(app);
            SysUserVO userVO = userVOMap.get(app.getUserId());
            appVO.setUser(userVO);
            return appVO;
        }).collect(Collectors.toList());
    }
}
