package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeDocumentVO;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeTaskVO;
import com.yupi.yuaicodemother.service.CustomerServiceKnowledgeService;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

@Service
@RequiredArgsConstructor
public class CustomerServiceKnowledgeServiceImpl implements CustomerServiceKnowledgeService {
    private final CustomerServiceKnowledgeDocumentMapper documentMapper;
    private final CustomerServiceKnowledgeEtlOutboxMapper outboxMapper;
    private final OssManager ossManager;

    @Override
    @Transactional
    public CustomerServiceKnowledgeDocumentVO upload(MultipartFile file, Long replacementId, long userId) {
        OssManager.KnowledgeObject uploaded = ossManager.uploadKnowledgeDocument(file);
        AtomicBoolean compensated = new AtomicBoolean(false);
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCompletion(int status) {
                    if (status != STATUS_COMMITTED) compensateUpload(uploaded.objectKey(), compensated);
                }
            });
        }
        try {
            if (documentMapper.findDuplicate(uploaded.sha256(), replacementId) != null)
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "知识文档内容已存在");
            CustomerServiceKnowledgeDocument document;
            if (replacementId == null) {
                document = new CustomerServiceKnowledgeDocument();
                document.setDocumentVersion(1L);
                document.setIndexedVersion(0L);
                document.setEtlVersion(1L);
                document.setChunkCount(0);
                document.setCreatedBy(userId);
                document.setIsDelete(0);
            } else {
                document = requireDocumentForUpdate(replacementId);
                ensureNotDeleted(document);
                document.setDocumentVersion(document.getDocumentVersion() + 1);
                document.setEtlVersion(document.getEtlVersion() + 1);
            }
            document.setName(uploaded.displayName());
            document.setFileType(uploaded.fileType().toUpperCase());
            document.setObjectKey(uploaded.objectKey());
            document.setFileSize(uploaded.size());
            document.setContentHash(uploaded.sha256());
            document.setStatus("UPLOADED");
            document.setLastErrorCode(null);
            document.setUpdatedBy(userId);
            int changed = replacementId == null ? documentMapper.insert(document) : documentMapper.update(document);
            if (changed != 1) throw new IllegalStateException("KNOWLEDGE_DOCUMENT_WRITE_FAILED");
            enqueue(document, "INDEX", document.getDocumentVersion());
            return toVO(document);
        } catch (RuntimeException error) {
            try {
                compensateUpload(uploaded.objectKey(), compensated);
            } catch (RuntimeException compensationError) {
                error.addSuppressed(compensationError);
            }
            if (error instanceof DuplicateKeyException)
                throw new BusinessException(ErrorCode.OPERATION_ERROR, "知识文档内容已存在");
            throw error;
        }
    }

    private void compensateUpload(String objectKey, AtomicBoolean compensated) {
        if (compensated.compareAndSet(false, true)) ossManager.deleteKnowledgeObject(objectKey);
    }

    @Override
    public Page<CustomerServiceKnowledgeDocumentVO> page(int pageNum, int pageSize) {
        checkPage(pageNum, pageSize);
        long total = documentMapper.countDocuments();
        List<CustomerServiceKnowledgeDocumentVO> records = documentMapper
                .pageDocuments((long) (pageNum - 1) * pageSize, pageSize).stream().map(this::toVO).toList();
        Page<CustomerServiceKnowledgeDocumentVO> page = new Page<>(pageNum, pageSize, total);
        page.setRecords(records);
        return page;
    }

    @Override
    public CustomerServiceKnowledgeDocumentVO detail(long id) {
        CustomerServiceKnowledgeDocument document = requireDocument(id);
        ensureNotDeleted(document);
        return toVO(document);
    }

    @Override
    @Transactional
    public CustomerServiceKnowledgeDocumentVO reindex(long id, long userId) {
        CustomerServiceKnowledgeDocument document = requireDocumentForUpdate(id);
        ensureNotDeleted(document);
        document.setEtlVersion(document.getEtlVersion() + 1);
        document.setStatus("UPLOADED");
        document.setUpdatedBy(userId);
        documentMapper.update(document);
        enqueue(document, "INDEX", document.getDocumentVersion());
        return toVO(document);
    }

    @Override
    @Transactional
    public CustomerServiceKnowledgeDocumentVO disable(long id, long userId) {
        CustomerServiceKnowledgeDocument document = requireDocumentForUpdate(id);
        ensureNotDeleted(document);
        document.setEtlVersion(document.getEtlVersion() + 1);
        document.setStatus("DISABLED");
        document.setUpdatedBy(userId);
        documentMapper.update(document);
        enqueue(document, "DELETE", activeVersion(document));
        return toVO(document);
    }

    @Override
    @Transactional
    public CustomerServiceKnowledgeDocumentVO enable(long id, long userId) {
        CustomerServiceKnowledgeDocument document = requireDocumentForUpdate(id);
        ensureNotDeleted(document);
        document.setEtlVersion(document.getEtlVersion() + 1);
        document.setStatus("UPLOADED");
        document.setUpdatedBy(userId);
        documentMapper.update(document);
        enqueue(document, "INDEX", document.getDocumentVersion());
        return toVO(document);
    }

    @Override
    @Transactional
    public boolean delete(long id, long userId) {
        CustomerServiceKnowledgeDocument document = requireDocumentForUpdate(id);
        ensureNotDeleted(document);
        document.setEtlVersion(document.getEtlVersion() + 1);
        document.setStatus("DELETING");
        document.setUpdatedBy(userId);
        document.setIsDelete(1);
        documentMapper.update(document);
        enqueue(document, "DELETE", activeVersion(document));
        return true;
    }

    @Override
    public List<CustomerServiceKnowledgeTaskVO> taskHistory(long id, int limit) {
        if (id <= 0 || limit < 1 || limit > 100) throw new BusinessException(ErrorCode.PARAMS_ERROR);
        return outboxMapper.history(id, limit).stream().map(this::toTaskVO).toList();
    }

    @Override
    @Transactional
    public int rebuild(long userId) {
        CustomerServiceKnowledgeEtlOutbox task = new CustomerServiceKnowledgeEtlOutbox();
        task.setDocumentId(0L);
        task.setDocumentVersion(0L);
        task.setEtlVersion(System.currentTimeMillis());
        task.setOperation("REBUILD");
        task.setStatus("PENDING");
        task.setRetryCount(0);
        task.setNextRetryTime(LocalDateTime.now());
        if (outboxMapper.insert(task) != 1) throw new IllegalStateException("KNOWLEDGE_REBUILD_OUTBOX_WRITE_FAILED");
        return 1;
    }

    private void enqueue(CustomerServiceKnowledgeDocument document, String operation, long targetVersion) {
        CustomerServiceKnowledgeEtlOutbox task = new CustomerServiceKnowledgeEtlOutbox();
        task.setDocumentId(document.getId());
        task.setDocumentVersion(targetVersion);
        task.setEtlVersion(document.getEtlVersion());
        task.setOperation(operation);
        task.setStatus("PENDING");
        task.setRetryCount(0);
        task.setNextRetryTime(LocalDateTime.now());
        if (outboxMapper.insert(task) != 1) throw new IllegalStateException("KNOWLEDGE_OUTBOX_WRITE_FAILED");
    }

    private CustomerServiceKnowledgeDocument requireDocument(long id) {
        if (id <= 0) throw new BusinessException(ErrorCode.PARAMS_ERROR);
        CustomerServiceKnowledgeDocument document = documentMapper.findIncludingDeleted(id);
        if (document == null) throw new BusinessException(ErrorCode.NOT_FOUND_ERROR);
        return document;
    }

    private CustomerServiceKnowledgeDocument requireDocumentForUpdate(long id) {
        if (id <= 0) throw new BusinessException(ErrorCode.PARAMS_ERROR);
        CustomerServiceKnowledgeDocument document = documentMapper.findIncludingDeletedForUpdate(id);
        if (document == null) throw new BusinessException(ErrorCode.NOT_FOUND_ERROR);
        return document;
    }

    private static void ensureNotDeleted(CustomerServiceKnowledgeDocument document) {
        if (Integer.valueOf(1).equals(document.getIsDelete())) throw new BusinessException(ErrorCode.NOT_FOUND_ERROR);
    }

    private static long activeVersion(CustomerServiceKnowledgeDocument document) {
        return document.getIndexedVersion() != null && document.getIndexedVersion() > 0
                ? document.getIndexedVersion() : document.getDocumentVersion();
    }

    private static void checkPage(int pageNum, int pageSize) {
        if (pageNum < 1 || pageSize < 1 || pageSize > 100) throw new BusinessException(ErrorCode.PARAMS_ERROR);
    }

    private CustomerServiceKnowledgeDocumentVO toVO(CustomerServiceKnowledgeDocument document) {
        CustomerServiceKnowledgeDocumentVO vo = new CustomerServiceKnowledgeDocumentVO();
        BeanUtil.copyProperties(document, vo);
        return vo;
    }

    private CustomerServiceKnowledgeTaskVO toTaskVO(CustomerServiceKnowledgeEtlOutbox task) {
        CustomerServiceKnowledgeTaskVO vo = new CustomerServiceKnowledgeTaskVO();
        BeanUtil.copyProperties(task, vo);
        return vo;
    }
}
