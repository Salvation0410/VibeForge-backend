package com.yupi.yuaicodemother.controller;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.PageRequest;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.model.dto.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.LoginUserVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.stream.Collectors;

/**
 * 用户控制层。
 */
@RestController
@RequestMapping("/users")
public class SysUserController {

    @Autowired
    private SysUserService sysUserService;

    /**
     * 管理员创建用户。
     *
     * @param request 注册参数
     * @return 创建结果
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin")
    public BaseResponse<SysUser> adminCreate(@RequestBody SysUserRegisterRequest request) {
        return ResultUtils.success(sysUserService.register(request));
    }

    /**
     * 用户注册。
     *
     * @param request 注册参数
     * @return 注册结果
     */
    @PostMapping
    public BaseResponse<SysUser> register(@RequestBody SysUserRegisterRequest request) {
        return ResultUtils.success(sysUserService.register(request));
    }

    /**
     * 用户登录。
     *
     * @param request 登录参数
     * @param httpServletRequest 请求对象
     * @return 脱敏后的登录用户信息
     */
    @PostMapping("/login")
    public BaseResponse<LoginUserVO> login(@RequestBody SysUserLoginRequest request,
                                           HttpServletRequest httpServletRequest) {
        // 登录成功后返回脱敏信息，前端可直接展示
        LoginUserVO loginUserVO = new LoginUserVO();
        loginUserVO.setUser(sysUserService.login(request, httpServletRequest));
        return ResultUtils.success(loginUserVO);
    }

    /**
     * 获取当前登录用户。
     *
     * @param httpServletRequest 请求对象
     * @return 当前登录用户信息
     */
    @GetMapping("/login")
    public BaseResponse<LoginUserVO> getLoginUser(HttpServletRequest httpServletRequest) {
        // 从 session 中读取当前登录用户并脱敏返回
        LoginUserVO loginUserVO = new LoginUserVO();
        loginUserVO.setUser(sysUserService.getLoginUserVo(httpServletRequest));
        return ResultUtils.success(loginUserVO);
    }

    /**
     * 用户退出登录。
     *
     * @param httpServletRequest 请求对象
     * @return 是否成功
     */
    @DeleteMapping("/login")
    public BaseResponse<Boolean> logout(HttpServletRequest httpServletRequest) {
        // 只读取当前已有的 session，避免无意中新建会话
        HttpSession session = httpServletRequest.getSession(false);
        if (session == null) {
            return ResultUtils.success(true);
        }

        // 从 session 中取出之前保存的登录态
        Object loginUser = session.getAttribute(UserConstant.USER_LOGIN_STATE);
        if (loginUser == null) {
            return ResultUtils.success(true);
        }

        // 销毁当前 session，完成注销
        session.invalidate();
        return ResultUtils.success(true);
    }

    /**
     * 管理员根据 ID 获取用户（未脱敏）。
     *
     * @param id 用户 ID
     * @return 用户信息
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/{id}")
    public BaseResponse<SysUser> getById(@PathVariable Long id) {
        return ResultUtils.success(sysUserService.getById(id));
    }

    /**
     * 根据 ID 获取用户（脱敏）。
     *
     * @param id 用户 ID
     * @return 脱敏后的用户信息
     */
    @GetMapping("/{id}/vo")
    public BaseResponse<SysUserVO> getByIdVo(@PathVariable Long id) {
        // 先查原始用户，再转换为脱敏对象返回
        SysUser sysUser = sysUserService.getById(id);
        return ResultUtils.success(sysUserService.getSysUserVO(sysUser));
    }

    /**
     * 管理员分页获取用户列表（脱敏）。
     *
     * @param pageRequest 分页参数
     * @return 脱敏后的用户列表
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/page")
    public BaseResponse<Page<SysUserVO>> page(PageRequest pageRequest) {
        // 构造分页对象
        Page<SysUser> page = Page.of(pageRequest.getPageNum(), pageRequest.getPageSize());
        QueryWrapper queryWrapper = QueryWrapper.create();
        if (pageRequest.getSortField() != null && !pageRequest.getSortField().isBlank()) {
            queryWrapper.orderBy(pageRequest.getSortField(), "descend".equalsIgnoreCase(pageRequest.getSortOrder()));
        }

        // 分页查询后做脱敏映射
        Page<SysUser> userPage = sysUserService.page(page, queryWrapper);
        Page<SysUserVO> voPage = Page.of(userPage.getPageNumber(), userPage.getPageSize());
        voPage.setTotalRow(userPage.getTotalRow());
        voPage.setTotalPage(userPage.getTotalPage());
        voPage.setRecords(userPage.getRecords().stream()
                .map(sysUserService::getSysUserVO)
                .collect(Collectors.toList()));
        return ResultUtils.success(voPage);
    }

    /**
     * 管理员更新用户。
     *
     * @param id 用户 ID
     * @param sysUser 用户信息
     * @return 是否成功
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PutMapping("/{id}")
    public BaseResponse<Boolean> update(@PathVariable Long id, @RequestBody SysUser sysUser) {
        // 路径参数优先，避免前端误传 id
        sysUser.setId(id);
        return ResultUtils.success(sysUserService.updateById(sysUser));
    }

    /**
     * 管理员根据 ID 删除用户。
     *
     * @param id 用户 ID
     * @return 是否成功
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @DeleteMapping("/{id}")
    public BaseResponse<Boolean> delete(@PathVariable Long id) {
        return ResultUtils.success(sysUserService.removeById(id));
    }
}
