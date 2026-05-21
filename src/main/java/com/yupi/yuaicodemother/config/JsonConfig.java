package com.yupi.yuaicodemother.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;

/**
 * Spring MVC JSON 配置类。
 * 用于统一处理返回给前端的 JSON 序列化规则。
 *
 * @author huang
 */
@Configuration
public class JsonConfig {

    /**
     * 自定义 Jackson 的 ObjectMapper。
     * 将 Long / long 类型统一序列化为字符串，避免前端解析雪花算法 ID 时丢失精度。
     *
     * @param builder Jackson 构造器
     * @return 自定义后的 ObjectMapper
     */
    @Bean
    public ObjectMapper jacksonObjectMapper(Jackson2ObjectMapperBuilder builder) {
        ObjectMapper objectMapper = builder.createXmlMapper(false).build();
        SimpleModule simpleModule = new SimpleModule();
        // 包装类型 Long 序列化为字符串
        simpleModule.addSerializer(Long.class, ToStringSerializer.instance);
        // 基本类型 long 序列化为字符串
        simpleModule.addSerializer(Long.TYPE, ToStringSerializer.instance);
        objectMapper.registerModule(simpleModule);
        return objectMapper;
    }
}
