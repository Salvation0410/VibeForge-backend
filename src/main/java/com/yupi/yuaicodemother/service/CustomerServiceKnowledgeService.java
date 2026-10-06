package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeDocumentVO;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeTaskVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

public interface CustomerServiceKnowledgeService {
    CustomerServiceKnowledgeDocumentVO upload(MultipartFile file, Long replacementId, long userId);
    Page<CustomerServiceKnowledgeDocumentVO> page(int pageNum, int pageSize);
    CustomerServiceKnowledgeDocumentVO detail(long id);
    CustomerServiceKnowledgeDocumentVO reindex(long id, long userId);
    CustomerServiceKnowledgeDocumentVO disable(long id, long userId);
    CustomerServiceKnowledgeDocumentVO enable(long id, long userId);
    boolean delete(long id, long userId);
    List<CustomerServiceKnowledgeTaskVO> taskHistory(long id, int limit);
    int rebuild(long userId);
}
