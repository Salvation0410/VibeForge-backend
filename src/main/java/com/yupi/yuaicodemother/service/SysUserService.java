package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import jakarta.servlet.http.HttpServletRequest;

/**
 * 用户服务层。
 */
public interface SysUserService extends IService<SysUser> {

    /**
     * 用户注册。
     *
     * @param request 注册参数
     * @return 注册后的用户信息
     */
    SysUser register(SysUserRegisterRequest request);

    /**
     * 用户登录。
     *
     * @param request 登录参数
     * @param httpServletRequest 请求对象，用于写入 session
     * @return 脱敏后的登录用户信息
     */
    SysUserVO login(SysUserLoginRequest request, HttpServletRequest httpServletRequest);

    /**
     * 获取当前已登录用户的脱敏信息。
     *
     * @param httpServletRequest 请求对象，用于读取 session
     * @return 已登录用户信息
     */
    SysUserVO getLoginUserVo(HttpServletRequest httpServletRequest);

    /**
     * 将用户实体转换为脱敏对象。
     *
     * @param sysUser 用户实体
     * @return 脱敏后的用户信息
     */
    SysUserVO getSysUserVO(SysUser sysUser);
}
