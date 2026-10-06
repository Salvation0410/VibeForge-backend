package com.yupi.yuaicodemother.model.dto.customerservice;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = false)
public class CustomerServiceAskRequest {
    @NotBlank
    @Size(max = 4000)
    private String question;
}
