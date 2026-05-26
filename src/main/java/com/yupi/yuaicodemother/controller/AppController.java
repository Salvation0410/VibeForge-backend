package com.yupi.yuaicodemother.controller;

import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.model.dto.AppAddRequest;
import com.yupi.yuaicodemother.model.dto.AppAdminUpdateRequest;
import com.yupi.yuaicodemother.model.dto.AppQueryRequest;
import com.yupi.yuaicodemother.model.dto.AppUserUpdateRequest;
import com.yupi.yuaicodemother.model.vo.AppVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 应用控制层
 */
@RestController
@RequestMapping("/apps")
public class AppController {

    @Autowired
    private AppService appService;

    @Autowired
    private SysUserService sysUserService;

    /**
     * 用户创建应用。
     *
     * @param request 创建参数
     * @param httpServletRequest 请求对象
     * @return 新应用 id
     */
    @AuthCheck
    @PostMapping
    public BaseResponse<Long> create(@RequestBody AppAddRequest request, HttpServletRequest httpServletRequest) {
        // 创建时自动绑定当前登录用户，前端无需额外传 userId。
        return ResultUtils.success(appService.createApp(request, getLoginUserId(httpServletRequest)));
    }

    /**
     * 用户修改自己的应用。
     *
     * @param id 应用 id
     * @param request 修改参数
     * @param httpServletRequest 请求对象
     * @return 是否修改成功
     */
    @AuthCheck
    @PutMapping("/{id}")
    public BaseResponse<Boolean> updateMyApp(@PathVariable Long id,
                                             @RequestBody AppUserUpdateRequest request,
                                             HttpServletRequest httpServletRequest) {
        // 当前需求下，普通用户侧只开放应用名称修改。
        return ResultUtils.success(appService.updateMyApp(id, request, getLoginUserId(httpServletRequest)));
    }

    /**
     * 用户删除自己的应用。
     *
     * @param id 应用 id
     * @param httpServletRequest 请求对象
     * @return 是否删除成功
     */
    @AuthCheck
    @DeleteMapping("/{id}")
    public BaseResponse<Boolean> remove(@PathVariable Long id, HttpServletRequest httpServletRequest) {
        return ResultUtils.success(appService.removeMyApp(id, getLoginUserId(httpServletRequest)));
    }

    /**
     * 用户查看自己的应用详情。
     *
     * @param id 应用 id
     * @param httpServletRequest 请求对象
     * @return 应用详情
     */
    @AuthCheck
    @GetMapping("/{id}")
    public BaseResponse<AppVO> getMyAppById(@PathVariable Long id, HttpServletRequest httpServletRequest) {
        return ResultUtils.success(appService.getMyAppById(id, getLoginUserId(httpServletRequest)));
    }

    /**
     * 用户分页查询自己的应用列表。
     *
     * @param request 查询参数
     * @param httpServletRequest 请求对象
     * @return 分页结果
     */
    @AuthCheck
    @GetMapping("/my/page")
    public BaseResponse<Page<AppVO>> pageMyApps(AppQueryRequest request, HttpServletRequest httpServletRequest) {
        return ResultUtils.success(appService.pageMyApps(request, getLoginUserId(httpServletRequest)));
    }

    /**
     * 用户分页查询精选应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    @AuthCheck
    @GetMapping("/featured/page")
    public BaseResponse<Page<AppVO>> pageFeaturedApps(AppQueryRequest request) {
        return ResultUtils.success(appService.pageFeaturedApps(request));
    }

    /**
     * 管理员删除任意应用。
     *
     * @param id 应用 id
     * @return 是否删除成功
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @DeleteMapping("/admin/{id}")
    public BaseResponse<Boolean> adminRemove(@PathVariable Long id) {
        return ResultUtils.success(appService.adminRemoveApp(id));
    }

    /**
     * 管理员更新任意应用。
     *
     * @param id 应用 id
     * @param request 更新参数
     * @return 是否更新成功
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PutMapping("/admin/{id}")
    public BaseResponse<Boolean> adminUpdate(@PathVariable Long id, @RequestBody AppAdminUpdateRequest request) {
        return ResultUtils.success(appService.adminUpdateApp(id, request));
    }

    /**
     * 管理员分页查询应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/admin/page")
    public BaseResponse<Page<AppVO>> adminPage(AppQueryRequest request) {
        return ResultUtils.success(appService.pageAdminApps(request));
    }

    /**
     * 管理员查看应用详情。
     *
     * @param id 应用 id
     * @return 应用详情
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/admin/{id}")
    public BaseResponse<AppVO> adminGetById(@PathVariable Long id) {
        return ResultUtils.success(appService.getAdminAppById(id));
    }

    /**
     * 从登录态中提取当前用户 id。
     *
     * @param httpServletRequest 请求对象
     * @return 当前登录用户 id
     */
    private Long getLoginUserId(HttpServletRequest httpServletRequest) {
        // 统一复用用户服务的 session 解析逻辑，避免控制器分散处理登录态。
        SysUserVO loginUserVO = sysUserService.getLoginUserVo(httpServletRequest);
        return loginUserVO.getId();
    }
}
