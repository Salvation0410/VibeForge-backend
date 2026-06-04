package com.yupi.yuaicodemother.service.impl;

import cn.hutool.captcha.CaptchaUtil;
import cn.hutool.captcha.CircleCaptcha;
import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.lang.Validator;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.crypto.digest.DigestUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.constant.SysUserConstant;
import com.yupi.yuaicodemother.enums.SysUserRoleEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.manager.MailManager;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.mapper.SysUserMapper;
import com.yupi.yuaicodemother.model.dto.user.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserUpdateRequest;
import com.yupi.yuaicodemother.model.dto.user.UserEmailCodeSendRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.LoginCaptchaVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.util.Objects;

/**
 * 用户服务实现
 */
@Service
@RequiredArgsConstructor
public class SysUserServiceImpl extends ServiceImpl<SysUserMapper, SysUser> implements SysUserService {

    /**
     * 邮箱验证码有效期，单位：秒
     */
    private static final int EMAIL_CODE_EXPIRE_SECONDS = 300;

    /**
     * 邮箱验证码发送间隔，单位：秒
     */
    private static final int EMAIL_CODE_SEND_INTERVAL_SECONDS = 60;

    /**
     * 登录图形验证码有效期，单位：秒
     */
    private static final int LOGIN_CAPTCHA_EXPIRE_SECONDS = 300;

    private final OssManager ossManager;

    private final MailManager mailManager;

    @Override
    public SysUser register(SysUserRegisterRequest request, HttpServletRequest httpServletRequest) {
        return createUser(request, null, false, httpServletRequest);
    }

    @Override
    public SysUser register(SysUserRegisterRequest request, MultipartFile avatarFile, HttpServletRequest httpServletRequest) {
        return createUser(request, avatarFile, false, httpServletRequest);
    }

    @Override
    public SysUser adminCreate(SysUserRegisterRequest request) {
        return createUser(request, null, true, null);
    }

    @Override
    public SysUser adminCreate(SysUserRegisterRequest request, MultipartFile avatarFile) {
        return createUser(request, avatarFile, true, null);
    }

    /**
     * 用户登录，支持账号或邮箱 + 密码登录，并校验图形验证码
     *
     * @param request 登录参数
     * @param httpServletRequest 请求对象
     * @return 登录用户信息
     */
    @Override
    public SysUserVO login(SysUserLoginRequest request, HttpServletRequest httpServletRequest) {
        if (request == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "登录参数不能为空");
        }
        HttpSession session = httpServletRequest.getSession();
        if (session.getAttribute(SysUserConstant.USER_LOGIN_STATE) != null) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "请勿重复登录");
        }

        validateLoginCaptcha(request.getCaptchaCode(), session);

        String account = StrUtil.trim(request.getAccount());
        String email = normalizeEmail(request.getEmail());
        String password = request.getPassword();
        if (StrUtil.isBlank(password)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "密码不能为空");
        }
        if (StrUtil.isBlank(account) && StrUtil.isBlank(email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号或邮箱至少填写一项");
        }

        QueryWrapper queryWrapper = QueryWrapper.create();
        if (StrUtil.isNotBlank(account)) {
            queryWrapper.where("account = ?", account);
        } else {
            queryWrapper.where("email = ?", email);
        }
        SysUser user = this.getOne(queryWrapper);
        if (user == null) {
            throw new BusinessException(ErrorCode.NOT_FOUND_ERROR, "用户不存在");
        }

        String storedPassword = user.getPasswordHash();
        boolean passwordMatch = DigestUtil.bcryptCheck(password, storedPassword);
        if (!passwordMatch && Objects.equals(password, storedPassword)) {
            passwordMatch = true;
            user.setPasswordHash(DigestUtil.bcrypt(password));
            this.updateById(user);
        }
        if (!passwordMatch) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号、邮箱或密码错误");
        }

        user.setLastLoginTime(LocalDateTime.now());
        user.setLastLoginIp(resolveClientIp(httpServletRequest));
        this.updateById(user);
        session.setAttribute(SysUserConstant.USER_LOGIN_STATE, user);
        clearLoginCaptcha(session);
        return getSysUserVO(user);
    }

    /**
     * 发送邮箱注册验证码
     *
     * @param request 发送请求
     * @param httpServletRequest 请求对象
     * @return 是否发送成功
     */
    @Override
    public boolean sendRegisterEmailCode(UserEmailCodeSendRequest request, HttpServletRequest httpServletRequest) {
        if (request == null || StrUtil.isBlank(request.getEmail())) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱不能为空");
        }
        String email = normalizeEmail(request.getEmail());
        if (!Validator.isEmail(email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱格式错误");
        }

        validateUniqueAccountAndEmail(null, email, null);
        HttpSession session = httpServletRequest.getSession();
        LocalDateTime lastSendTime = (LocalDateTime) session.getAttribute(SysUserConstant.EMAIL_REGISTER_CODE_SEND_TIME);
        if (lastSendTime != null && lastSendTime.plusSeconds(EMAIL_CODE_SEND_INTERVAL_SECONDS).isAfter(LocalDateTime.now())) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "验证码发送过于频繁，请稍后再试");
        }

        String emailCode = RandomUtil.randomNumbers(6);
        mailManager.sendRegisterCode(email, emailCode);
        session.setAttribute(SysUserConstant.EMAIL_REGISTER_CODE, emailCode);
        session.setAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EMAIL, email);
        session.setAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EXPIRE_TIME, LocalDateTime.now().plusSeconds(EMAIL_CODE_EXPIRE_SECONDS));
        session.setAttribute(SysUserConstant.EMAIL_REGISTER_CODE_SEND_TIME, LocalDateTime.now());
        return true;
    }

    /**
     * 获取登录图形验证码
     *
     * @param httpServletRequest 请求对象
     * @return 图形验证码信息
     */
    @Override
    public LoginCaptchaVO getLoginCaptcha(HttpServletRequest httpServletRequest) {
        CircleCaptcha captcha = CaptchaUtil.createCircleCaptcha(160, 60, 4, 20);
        captcha.createCode();
        HttpSession session = httpServletRequest.getSession();
        session.setAttribute(SysUserConstant.LOGIN_CAPTCHA_CODE, captcha.getCode());
        session.setAttribute(SysUserConstant.LOGIN_CAPTCHA_EXPIRE_TIME, LocalDateTime.now().plusSeconds(LOGIN_CAPTCHA_EXPIRE_SECONDS));

        LoginCaptchaVO loginCaptchaVO = new LoginCaptchaVO();
        loginCaptchaVO.setCaptchaImage(captcha.getImageBase64Data());
        loginCaptchaVO.setExpireSeconds(LOGIN_CAPTCHA_EXPIRE_SECONDS);
        return loginCaptchaVO;
    }

    @Override
    public SysUser getLoginUser(HttpServletRequest httpServletRequest) {
        Object loginObj = httpServletRequest.getSession().getAttribute(SysUserConstant.USER_LOGIN_STATE);
        if (loginObj == null) {
            throw new BusinessException(ErrorCode.NOT_LOGIN_ERROR, "用户未登录");
        }
        if (!(loginObj instanceof SysUser loginUser)) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "登录态异常");
        }
        SysUser currentUser = this.getById(loginUser.getId());
        if (currentUser == null) {
            throw new BusinessException(ErrorCode.NOT_FOUND_ERROR, "当前用户不存在");
        }
        return currentUser;
    }

    @Override
    public SysUserVO getLoginUserVo(HttpServletRequest httpServletRequest) {
        return getSysUserVO(getLoginUser(httpServletRequest));
    }

    @Override
    public SysUserVO getSysUserVO(SysUser sysUser) {
        SysUserVO vo = new SysUserVO();
        BeanUtil.copyProperties(sysUser, vo);
        return vo;
    }

    @Override
    public boolean updateUserByAdmin(Long id, SysUserUpdateRequest request) {
        return updateUserByAdmin(id, request, null);
    }

    @Override
    public boolean updateUserByAdmin(Long id, SysUserUpdateRequest request, MultipartFile avatarFile) {
        ThrowUtils.throwIf(id == null || id <= 0, ErrorCode.PARAMS_ERROR, "用户 id 不合法");
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "更新参数不能为空");

        SysUser oldUser = this.getById(id);
        ThrowUtils.throwIf(oldUser == null, ErrorCode.NOT_FOUND_ERROR, "用户不存在");

        validateUpdateRequest(request);
        validateUniqueAccountAndEmail(request.getAccount(), normalizeEmail(request.getEmail()), id);

        SysUser updateUser = new SysUser();
        updateUser.setId(id);
        if (request.getAccount() != null) {
            updateUser.setAccount(request.getAccount());
        }
        if (request.getEmail() != null) {
            updateUser.setEmail(normalizeEmail(request.getEmail()));
        }
        if (request.getNickname() != null) {
            updateUser.setNickname(request.getNickname());
        }
        if (request.getUserProfile() != null) {
            updateUser.setUserProfile(request.getUserProfile());
        }
        if (request.getStatus() != null) {
            updateUser.setStatus(request.getStatus());
        }
        if (request.getUserRole() != null) {
            updateUser.setUserRole(resolveUserRole(request.getUserRole(), true));
        }

        String avatarUrl = request.getAvatarUrl();
        if (avatarFile != null && !avatarFile.isEmpty()) {
            avatarUrl = ossManager.uploadAvatar(avatarFile);
        }
        if (avatarUrl != null) {
            updateUser.setAvatarUrl(avatarUrl);
        }

        updateUser.setUpdateTime(LocalDateTime.now());
        boolean result = this.updateById(updateUser);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR, "用户更新失败");
        return true;
    }

    /**
     * 创建用户
     *
     * @param request 注册参数
     * @param avatarFile 头像文件
     * @param allowAssignRole 是否允许指定角色
     * @param httpServletRequest 请求对象
     * @return 用户实体
     */
    @Override
    public boolean updateCurrentUser(SysUserUpdateRequest request, MultipartFile avatarFile, HttpServletRequest httpServletRequest) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "更新参数不能为空");

        SysUser currentUser = getLoginUser(httpServletRequest);
        validateSelfUpdateRequest(request);
        validateUniqueAccountAndEmail(null, normalizeEmail(request.getEmail()), currentUser.getId());

        SysUser updateUser = new SysUser();
        updateUser.setId(currentUser.getId());
        if (request.getEmail() != null) {
            updateUser.setEmail(normalizeEmail(request.getEmail()));
        }
        if (request.getNickname() != null) {
            updateUser.setNickname(request.getNickname());
        }
        if (request.getUserProfile() != null) {
            updateUser.setUserProfile(request.getUserProfile());
        }

        String avatarUrl = request.getAvatarUrl();
        if (avatarFile != null && !avatarFile.isEmpty()) {
            avatarUrl = ossManager.uploadAvatar(avatarFile);
        }
        if (avatarUrl != null) {
            updateUser.setAvatarUrl(avatarUrl);
        }

        updateUser.setUpdateTime(LocalDateTime.now());
        boolean result = this.updateById(updateUser);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR, "用户更新失败");
        return true;
    }

    private SysUser createUser(SysUserRegisterRequest request, MultipartFile avatarFile, boolean allowAssignRole,
                               HttpServletRequest httpServletRequest) {
        if (request == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "注册参数不能为空");
        }

        String account = StrUtil.trim(request.getAccount());
        String email = normalizeEmail(request.getEmail());
        String password = request.getPassword();
        String confirmPassword = request.getConfirmPassword();

        if (StrUtil.isBlank(password) || StrUtil.isBlank(confirmPassword)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "密码不能为空");
        }
        if (!Objects.equals(password, confirmPassword)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "两次输入的密码不一致");
        }
        if (StrUtil.isBlank(account) && StrUtil.isBlank(email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号和邮箱至少填写一项");
        }
        if (StrUtil.isNotBlank(email) && !Validator.isEmail(email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱格式错误");
        }

        if (!allowAssignRole && StrUtil.isNotBlank(email)) {
            verifyRegisterEmailCode(email, request.getEmailCode(), httpServletRequest.getSession());
        }

        validateUniqueAccountAndEmail(account, email, null);

        SysUser user = new SysUser();
        user.setAccount(account);
        user.setEmail(email);
        user.setPasswordHash(DigestUtil.bcrypt(password));
        user.setNickname(request.getNickname());
        user.setAvatarUrl(resolveAvatarUrl(request.getAvatarUrl(), avatarFile));
        user.setUserProfile(request.getUserProfile());
        user.setUserRole(resolveUserRole(request.getUserRole(), allowAssignRole));
        user.setRegisterType(StrUtil.isNotBlank(email) ? 2 : 1);
        user.setStatus(0);
        user.setEmailVerified(StrUtil.isNotBlank(email) ? 1 : 0);
        user.setDeleted(0);
        boolean result = this.save(user);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR, "用户创建失败");

        if (!allowAssignRole && StrUtil.isNotBlank(email)) {
            clearRegisterEmailCode(httpServletRequest.getSession());
        }
        return user;
    }

    /**
     * 校验登录验证码
     *
     * @param captchaCode 用户输入的验证码
     * @param session Session
     */
    private void validateLoginCaptcha(String captchaCode, HttpSession session) {
        if (StrUtil.isBlank(captchaCode)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "请输入登录验证码");
        }
        String sessionCode = (String) session.getAttribute(SysUserConstant.LOGIN_CAPTCHA_CODE);
        LocalDateTime expireTime = (LocalDateTime) session.getAttribute(SysUserConstant.LOGIN_CAPTCHA_EXPIRE_TIME);
        if (StrUtil.isBlank(sessionCode) || expireTime == null || expireTime.isBefore(LocalDateTime.now())) {
            clearLoginCaptcha(session);
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "登录验证码已过期，请重新获取");
        }
        if (!StrUtil.equalsIgnoreCase(sessionCode, captchaCode)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "登录验证码错误");
        }
    }

    /**
     * 校验邮箱注册验证码
     *
     * @param email 注册邮箱
     * @param emailCode 用户输入验证码
     * @param session Session
     */
    private void verifyRegisterEmailCode(String email, String emailCode, HttpSession session) {
        if (StrUtil.isBlank(emailCode)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱验证码不能为空");
        }
        String sessionCode = (String) session.getAttribute(SysUserConstant.EMAIL_REGISTER_CODE);
        String sessionEmail = (String) session.getAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EMAIL);
        LocalDateTime expireTime = (LocalDateTime) session.getAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EXPIRE_TIME);
        if (StrUtil.isBlank(sessionCode) || StrUtil.isBlank(sessionEmail) || expireTime == null) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "请先获取邮箱验证码");
        }
        if (expireTime.isBefore(LocalDateTime.now())) {
            clearRegisterEmailCode(session);
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "邮箱验证码已过期，请重新获取");
        }
        if (!StrUtil.equalsIgnoreCase(sessionEmail, email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱验证码与当前邮箱不匹配");
        }
        if (!StrUtil.equalsIgnoreCase(sessionCode, emailCode)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱验证码错误");
        }
    }

    /**
     * 校验管理员更新参数
     *
     * @param request 更新参数
     */
    private void validateUpdateRequest(SysUserUpdateRequest request) {
        if (request.getAccount() != null && StrUtil.isBlank(request.getAccount())) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号不能为空字符串");
        }
        if (request.getEmail() != null) {
            if (StrUtil.isBlank(request.getEmail())) {
                throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱不能为空字符串");
            }
            if (!Validator.isEmail(request.getEmail())) {
                throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱格式错误");
            }
        }
        if (request.getUserRole() != null && SysUserRoleEnum.getEnumByValue(request.getUserRole()) == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "用户角色不合法");
        }
    }

    /**
     * 校验账号和邮箱唯一性
     *
     * @param account 账号
     * @param email 邮箱
     * @param excludeId 需要排除的用户 id
     */
    private void validateSelfUpdateRequest(SysUserUpdateRequest request) {
        if (request.getEmail() != null) {
            if (StrUtil.isBlank(request.getEmail())) {
                throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱不能为空字符串");
            }
            if (!Validator.isEmail(request.getEmail())) {
                throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱格式错误");
            }
        }
    }

    private void validateUniqueAccountAndEmail(String account, String email, Long excludeId) {
        if (StrUtil.isNotBlank(account)) {
            QueryWrapper accountQuery = QueryWrapper.create().where("account = ?", account);
            if (excludeId != null) {
                accountQuery.and("id <> ?", excludeId);
            }
            Long accountCount = this.count(accountQuery);
            if (accountCount != null && accountCount > 0) {
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "账号已存在");
            }
        }
        if (StrUtil.isNotBlank(email)) {
            QueryWrapper emailQuery = QueryWrapper.create().where("email = ?", email);
            if (excludeId != null) {
                emailQuery.and("id <> ?", excludeId);
            }
            Long emailCount = this.count(emailQuery);
            if (emailCount != null && emailCount > 0) {
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "邮箱已存在");
            }
        }
    }

    /**
     * 解析用户角色
     *
     * @param userRole 用户角色
     * @param allowAssignRole 是否允许指定角色
     * @return 角色值
     */
    private String resolveUserRole(String userRole, boolean allowAssignRole) {
        if (!allowAssignRole || StrUtil.isBlank(userRole)) {
            return SysUserRoleEnum.USER.getValue();
        }
        if (SysUserRoleEnum.getEnumByValue(userRole) == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "用户角色不合法");
        }
        return userRole;
    }

    /**
     * 解析头像地址
     *
     * @param avatarUrl 头像地址
     * @param avatarFile 头像文件
     * @return 最终头像地址
     */
    private String resolveAvatarUrl(String avatarUrl, MultipartFile avatarFile) {
        if (avatarFile != null && !avatarFile.isEmpty()) {
            return ossManager.uploadAvatar(avatarFile);
        }
        return avatarUrl;
    }

    /**
     * 归一化邮箱，统一转小写去空格
     *
     * @param email 原始邮箱
     * @return 归一化后的邮箱
     */
    private String normalizeEmail(String email) {
        if (StrUtil.isBlank(email)) {
            return email;
        }
        return StrUtil.trim(email).toLowerCase();
    }

    /**
     * 清理登录验证码
     *
     * @param session Session
     */
    private void clearLoginCaptcha(HttpSession session) {
        session.removeAttribute(SysUserConstant.LOGIN_CAPTCHA_CODE);
        session.removeAttribute(SysUserConstant.LOGIN_CAPTCHA_EXPIRE_TIME);
    }

    /**
     * 清理邮箱注册验证码
     *
     * @param session Session
     */
    private void clearRegisterEmailCode(HttpSession session) {
        session.removeAttribute(SysUserConstant.EMAIL_REGISTER_CODE);
        session.removeAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EMAIL);
        session.removeAttribute(SysUserConstant.EMAIL_REGISTER_CODE_EXPIRE_TIME);
        session.removeAttribute(SysUserConstant.EMAIL_REGISTER_CODE_SEND_TIME);
    }

    /**
     * 获取客户端 IP
     *
     * @param httpServletRequest 请求对象
     * @return 客户端 IP
     */
    private String resolveClientIp(HttpServletRequest httpServletRequest) {
        String forwardedFor = httpServletRequest.getHeader("X-Forwarded-For");
        if (StrUtil.isNotBlank(forwardedFor)) {
            return StrUtil.split(forwardedFor, ',').get(0).trim();
        }
        String realIp = httpServletRequest.getHeader("X-Real-IP");
        if (StrUtil.isNotBlank(realIp)) {
            return realIp;
        }
        return httpServletRequest.getRemoteAddr();
    }
}
