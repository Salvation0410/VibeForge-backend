package com.yupi.yuaicodemother.aop;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.enums.SysUserRoleEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * 权限校验切面。
 */
@Aspect
@Component
public class AuthInterceptor {

    @Resource
    private SysUserService sysUserService;

    /**
     * 执行权限拦截。
     *
     * @param joinPoint 切入点
     * @param authCheck 权限注解
     * @return 方法执行结果
     * @throws Throwable 异常
     */
    @Around("@annotation(authCheck)")
    public Object doInterceptor(ProceedingJoinPoint joinPoint, AuthCheck authCheck) throws Throwable {
        // 读取注解配置的最低权限
        String mustRole = authCheck.mustRole();

        // 获取当前请求对象
        RequestAttributes requestAttributes = RequestContextHolder.currentRequestAttributes();
        HttpServletRequest request = ((ServletRequestAttributes) requestAttributes).getRequest();

        // 当前登录用户
        SysUserVO loginUser = sysUserService.getLoginUserVo(request);

        // 不需要权限，直接放行
        if (mustRole == null || mustRole.isBlank()) {
            return joinPoint.proceed();
        }

        // 当前用户角色
        SysUserRoleEnum mustRoleEnum = SysUserRoleEnum.getEnumByValue(mustRole);
        SysUserRoleEnum userRoleEnum = SysUserRoleEnum.getEnumByValue(loginUser.getUserRole());

        // 角色非法或未登录，直接拒绝
        if (mustRoleEnum == null || userRoleEnum == null) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限");
        }

        // 仅管理员权限校验
        if (SysUserRoleEnum.ADMIN.equals(mustRoleEnum) && !SysUserRoleEnum.ADMIN.equals(userRoleEnum)) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "无权限");
        }

        // 校验通过，放行
        return joinPoint.proceed();
    }
}
