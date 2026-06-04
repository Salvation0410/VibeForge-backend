package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.user.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserUpdateRequest;
import com.yupi.yuaicodemother.model.dto.user.UserEmailCodeSendRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.LoginCaptchaVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.multipart.MultipartFile;

/**
 * 用户服务层
 */
public interface SysUserService extends IService<SysUser> {

    /**
     * 用户注册
     *
     * @param request 注册参数
     * @param httpServletRequest 请求对象
     * @return 注册后的用户信息
     */
    SysUser register(SysUserRegisterRequest request, HttpServletRequest httpServletRequest);

    /**
     * 用户注册（支持头像上传）
     *
     * @param request 注册参数
     * @param avatarFile 头像文件
     * @param httpServletRequest 请求对象
     * @return 注册后的用户信息
     */
    SysUser register(SysUserRegisterRequest request, MultipartFile avatarFile, HttpServletRequest httpServletRequest);

    /**
     * 管理员创建用户
     *
     * @param request 创建参数
     * @return 创建结果
     */
    SysUser adminCreate(SysUserRegisterRequest request);

    /**
     * 管理员创建用户（支持头像上传）
     *
     * @param request 创建参数
     * @param avatarFile 头像文件
     * @return 创建结果
     */
    SysUser adminCreate(SysUserRegisterRequest request, MultipartFile avatarFile);

    /**
     * 用户登录
     *
     * @param request 登录参数
     * @param httpServletRequest 请求对象
     * @return 脱敏后的登录用户信息
     */
    SysUserVO login(SysUserLoginRequest request, HttpServletRequest httpServletRequest);

    /**
     * 发送邮箱注册验证码
     *
     * @param request 发送验证码请求
     * @param httpServletRequest 请求对象
     * @return 是否发送成功
     */
    boolean sendRegisterEmailCode(UserEmailCodeSendRequest request, HttpServletRequest httpServletRequest);

    /**
     * 获取登录图形验证码
     *
     * @param httpServletRequest 请求对象
     * @return 登录验证码信息
     */
    LoginCaptchaVO getLoginCaptcha(HttpServletRequest httpServletRequest);

    /**
     * 获取当前已登录用户的完整信息
     *
     * @param httpServletRequest 请求对象
     * @return 当前登录用户完整信息
     */
    SysUser getLoginUser(HttpServletRequest httpServletRequest);

    /**
     * 获取当前已登录用户的脱敏信息
     *
     * @param httpServletRequest 请求对象
     * @return 当前登录用户信息
     */
    SysUserVO getLoginUserVo(HttpServletRequest httpServletRequest);

    /**
     * 用户实体转脱敏对象
     *
     * @param sysUser 用户实体
     * @return 脱敏后的用户信息
     */
    SysUserVO getSysUserVO(SysUser sysUser);

    /**
     * 管理员更新用户
     *
     * @param id 用户 id
     * @param request 更新参数
     * @return 是否成功
     */
    boolean updateUserByAdmin(Long id, SysUserUpdateRequest request);

    /**
     * 管理员更新用户（支持头像上传）
     *
     * @param id 用户 id
     * @param request 更新参数
     * @param avatarFile 头像文件
     * @return 是否成功
     */
    boolean updateUserByAdmin(Long id, SysUserUpdateRequest request, MultipartFile avatarFile);

    boolean updateCurrentUser(SysUserUpdateRequest request, MultipartFile avatarFile, HttpServletRequest httpServletRequest);
}
