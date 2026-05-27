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
import com.yupi.yuaicodemother.mapper.SysUserMapper;
import com.yupi.yuaicodemother.model.dto.SysUserLoginRequest;
import com.yupi.yuaicodemother.model.dto.SysUserRegisterRequest;
import com.yupi.yuaicodemother.model.dto.app.AppQueryRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.stereotype.Service;

import java.util.Objects;

/**
 * 用户服务实现。
 */
@Service
public class SysUserServiceImpl extends ServiceImpl<SysUserMapper, SysUser> implements SysUserService {

    @Override
    public SysUser register(SysUserRegisterRequest request) {
        // 参数基础校验
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

        // 查重
        if (StrUtil.isNotBlank(account)) {
            Long accountCount = this.count(QueryWrapper.create().where("account = ?", account));
            if (accountCount != null && accountCount > 0) {
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "账号已存在");
            }
        }
        if (StrUtil.isNotBlank(email)) {
            Long emailCount = this.count(QueryWrapper.create().where("email = ?", email));
            if (emailCount != null && emailCount > 0) {
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "邮箱已存在");
            }
        }

        // 保存用户
        SysUser user = new SysUser();
        user.setAccount(account);
        user.setEmail(email);
        // 密码入库前进行 BCrypt 加密，避免明文存储
        user.setPasswordHash(DigestUtil.bcrypt(password));
        user.setNickname(request.getNickname());
        user.setUserProfile(request.getUserProfile());
        user.setUserRole(StrUtil.blankToDefault(request.getUserRole(), SysUserRoleEnum.USER.getValue()));
        user.setRegisterType(StrUtil.isNotBlank(email) ? 2 : 1);
        user.setStatus(0);
        user.setEmailVerified(0);
        user.setDeleted(0);
        this.save(user);
        return user;
    }

    @Override
    public SysUserVO login(SysUserLoginRequest request, HttpServletRequest httpServletRequest) {
        // 参数校验
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

        // 查询用户
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

        // 密码校验：优先按 BCrypt 校验，兼容历史明文数据并自动升级
        String storedPassword = user.getPasswordHash();
        boolean passwordMatch = DigestUtil.bcryptCheck(password, storedPassword);
        if (!passwordMatch && Objects.equals(password, storedPassword)) {
            passwordMatch = true;
            // 历史明文密码命中后，立即升级为 BCrypt 密码
            user.setPasswordHash(DigestUtil.bcrypt(password));
            this.updateById(user);
        }
        if (!passwordMatch) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "账号、邮箱或密码错误");
        }

        // 写入 session
        HttpSession session = httpServletRequest.getSession();
        session.setAttribute(SysUserConstant.USER_LOGIN_STATE, user);

        // 返回脱敏对象
        return getSysUserVO(user);
    }

    @Override
    public SysUser getLoginUser(HttpServletRequest httpServletRequest) {
        // 从 session 中获取登录态
        Object loginObj = httpServletRequest.getSession().getAttribute(SysUserConstant.USER_LOGIN_STATE);
        if (loginObj == null) {
            throw new BusinessException(ErrorCode.NOT_LOGIN_ERROR, "未登录");
        }
        if (!(loginObj instanceof SysUser loginUser)) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "登录态异常");
        }

        Long userId = loginUser.getId();
        SysUser currentUser = this.getById(userId);
        if (currentUser == null) {
            throw new BusinessException(ErrorCode.NOT_FOUND_ERROR, "当前用户不存在");
        }
        return currentUser;
    }

    @Override
    public SysUserVO getLoginUserVo(HttpServletRequest httpServletRequest) {
        return getSysUserVO(getLoginUser(httpServletRequest));
    }

    /**
     * 用户对象脱敏。
     *
     * @param sysUser 原始用户对象
     * @return 脱敏后的 VO
     */
    @Override
    public SysUserVO getSysUserVO(SysUser sysUser) {
        // 仅复制前端展示需要的字段，避免泄露密码等敏感信息
        SysUserVO vo = new SysUserVO();
        BeanUtil.copyProperties(sysUser, vo);
        return vo;
    }


}
