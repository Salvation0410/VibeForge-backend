package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CustomerServiceAnswerVO;
import com.yupi.yuaicodemother.ratelimit.annotation.RateLimit;
import com.yupi.yuaicodemother.service.CustomerServiceAnswerService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/customer-service")
@RequiredArgsConstructor
public class CustomerServiceController {
    private final CustomerServiceAnswerService answerService;
    private final SysUserService sysUserService;

    @PostMapping("/ask")
    @RateLimit(key = "customer-service-ask", rate = 10, rateInterval = 60)
    public BaseResponse<CustomerServiceAnswerVO> ask(@Valid @RequestBody CustomerServiceAskRequest request,
                                                     HttpServletRequest httpRequest) {
        SysUser user = sysUserService.getLoginUser(httpRequest);
        return ResultUtils.success(answerService.answer(request, user.getId()));
    }
}
