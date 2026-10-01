package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.vo.CustomerServiceAnswerVO;
import com.yupi.yuaicodemother.service.CustomerServiceAnswerService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
@RequiredArgsConstructor
@Slf4j
public class CustomerServiceAnswerServiceImpl implements CustomerServiceAnswerService {
    private static final int MAX_QUESTION_BYTES = 16_000;
    private static final int MAX_ANSWER_CHARS = 4_000;
    private static final Pattern CHUNK_ID = Pattern.compile("^([^:]{1,128}):(\\d+):(\\d+)$");
    private final CustomerServiceAiClient aiClient;
    private final CustomerServiceKnowledgeDocumentMapper documentMapper;
    private final CustomerServiceProperties properties;

    @Override
    public CustomerServiceAnswerVO answer(CustomerServiceAskRequest request, long userId) {
        if (request == null || StrUtil.isBlank(request.getQuestion())
                || request.getQuestion().length() > 4_000
                || request.getQuestion().getBytes(StandardCharsets.UTF_8).length > MAX_QUESTION_BYTES)
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "问题不能为空且不能超过限制");
        if (!properties.isEnabled()) throw new BusinessException(ErrorCode.SYSTEM_ERROR, "客服服务暂不可用");
        String requestId = UUID.randomUUID().toString();
        CustomerServiceAiClient.AnswerResponse response;
        try {
            response = aiClient.answer(new CustomerServiceAiClient.AnswerRequest(requestId, request.getQuestion().trim()));
        } catch (CustomerServiceAiClient.CallException error) {
            log.warn("customer service requestId={} code={}", requestId, error.code());
            throw mapped(error, requestId);
        }
        if (!response.requestId().equals(requestId)) reject(requestId);
        if (response.answer() == null || response.answer().length() > MAX_ANSWER_CHARS) reject(requestId);
        List<CustomerServiceAnswerVO.Source> sources = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (CustomerServiceAiClient.AnswerSource source : response.sources()) {
            CustomerServiceKnowledgeDocument document = findReady(source.documentId());
            if (document == null || document.getDocumentVersion() == null
                    || document.getDocumentVersion() != source.documentVersion()
                    || document.getIndexedVersion() == null || document.getIndexedVersion() != source.documentVersion()
                    || document.getName() == null || !document.getName().equals(source.documentName())
                    || !validChunk(source, document)) reject(requestId);
            if (source.excerpt() == null || source.excerpt().length() > 400
                    || source.documentName().length() > 255 || source.locator().length() > 500) reject(requestId);
            if (!seen.add(source.chunkId())) reject(requestId);
            if (sources.size() == 3) continue;
            sources.add(new CustomerServiceAnswerVO.Source(source.documentId(), source.documentName(),
                    source.documentVersion(), source.chunkId(), source.locator(), source.excerpt()));
        }
        if (!response.answered()) return new CustomerServiceAnswerVO(false, "暂未找到相关知识。", List.of());
        return new CustomerServiceAnswerVO(true, response.answer(), List.copyOf(sources));
    }

    private CustomerServiceKnowledgeDocument findReady(String id) {
        try { return documentMapper.findIncludingDeleted(Long.parseLong(id)); }
        catch (RuntimeException ignored) { return null; }
    }

    private boolean validChunk(CustomerServiceAiClient.AnswerSource source, CustomerServiceKnowledgeDocument document) {
        Matcher matcher = CHUNK_ID.matcher(source.chunkId());
        if (!matcher.matches() || !matcher.group(1).equals(source.documentId())) return false;
        try {
            if (Long.parseLong(matcher.group(2)) != source.documentVersion()) return false;
            long index = Long.parseLong(matcher.group(3));
            return index >= 0 && document.getChunkCount() != null && index < document.getChunkCount()
                    && document.getIsDelete() != null && document.getIsDelete() == 0
                    && ("READY".equalsIgnoreCase(document.getStatus()) || "ACTIVE".equalsIgnoreCase(document.getStatus()));
        } catch (NumberFormatException ignored) {
            return false;
        }
    }

    private CustomerServiceKnowledgeDocument findReady(String id, boolean ignored) { return findReady(id); }
    private static void reject(String requestId) { throw new BusinessException(ErrorCode.SYSTEM_ERROR, "客服服务暂不可用"); }
    private BusinessException mapped(CustomerServiceAiClient.CallException error, String requestId) {
        if ("CUSTOMER_SERVICE_NO_ANSWER".equals(error.code())) return new BusinessException(ErrorCode.SYSTEM_ERROR, "暂未找到相关知识");
        return new BusinessException(ErrorCode.SYSTEM_ERROR, "客服服务暂不可用");
    }
}
