package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.crypto.digest.DigestUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.constant.SysUserConstant;
import com.yupi.yuaicodemother.enums.SysUserRoleEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.mapper.SysUserMapper;
import com.yupi.yuaicodemother.model.dto.user.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.dto.user.SysUserUpdateRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
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

    private final OssManager ossManager;

    @Override
    public SysUser register(SysUserRegisterRequest request) {
        return createUser(request, null, false);
    }

    @Override
    public SysUser register(SysUserRegisterRequest request, MultipartFile avatarFile) {
        return createUser(request, avatarFile, false);
    }

    @Override
    public SysUser adminCreate(SysUserRegisterRequest request) {
        return createUser(request, null, true);
    }

    @Override
    public SysUser adminCreate(SysUserRegisterRequest request, MultipartFile avatarFile) {
        return createUser(request, avatarFile, true);
    }

    @Override
    public SysUserVO login(SysUserLoginRequest request, HttpServletRequest httpServletRequest) {
        if (request == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "登录参数不能为空");
        }
        String account = request.getAccount();
        String email = request.getEmail();
        String password = request.getPassword();
        if (StrUtil.isBlank(password)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "密码不能为空");
        }
        if (StrUtil.isBlank(account) && StrUtil.isBlank(email)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号或邮箱至少填写一个");
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

        HttpSession session = httpServletRequest.getSession();
        session.setAttribute(SysUserConstant.USER_LOGIN_STATE, user);
        return getSysUserVO(user);
    }

    @Override
    public SysUser getLoginUser(HttpServletRequest httpServletRequest) {
        Object loginObj = httpServletRequest.getSession().getAttribute(SysUserConstant.USER_LOGIN_STATE);
        if (loginObj == null) {
            throw new BusinessException(ErrorCode.NOT_LOGIN_ERROR, "未登录");
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
        validateUniqueAccountAndEmail(request.getAccount(), request.getEmail(), id);

        SysUser updateUser = new SysUser();
        updateUser.setId(id);
        if (request.getAccount() != null) {
            updateUser.setAccount(request.getAccount());
        }
        if (request.getEmail() != null) {
            updateUser.setEmail(request.getEmail());
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

    private SysUser createUser(SysUserRegisterRequest request, MultipartFile avatarFile, boolean allowAssignRole) {
        if (request == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "注册参数不能为空");
        }

        String account = request.getAccount();
        String email = request.getEmail();
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
        user.setEmailVerified(0);
        user.setDeleted(0);
        boolean result = this.save(user);
        ThrowUtils.throwIf(!result, ErrorCode.OPERATION_ERROR, "用户创建失败");
        return user;
    }

    private void validateUpdateRequest(SysUserUpdateRequest request) {
        if (request.getAccount() != null && StrUtil.isBlank(request.getAccount())) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号不能为空字符串");
        }
        if (request.getEmail() != null && StrUtil.isBlank(request.getEmail())) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "邮箱不能为空字符串");
        }
        if (request.getUserRole() != null && SysUserRoleEnum.getEnumByValue(request.getUserRole()) == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "用户角色不合法");
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

    private String resolveUserRole(String userRole, boolean allowAssignRole) {
        if (!allowAssignRole || StrUtil.isBlank(userRole)) {
            return SysUserRoleEnum.USER.getValue();
        }
        if (SysUserRoleEnum.getEnumByValue(userRole) == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "用户角色不合法");
        }
        return userRole;
    }

    private String resolveAvatarUrl(String avatarUrl, MultipartFile avatarFile) {
        if (avatarFile != null && !avatarFile.isEmpty()) {
            return ossManager.uploadAvatar(avatarFile);
        }
        return avatarUrl;
    }
}
