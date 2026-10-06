package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.model.vo.CustomerServiceAnswerVO;

public interface CustomerServiceAnswerService {
    CustomerServiceAnswerVO answer(CustomerServiceAskRequest request, long userId);
}
