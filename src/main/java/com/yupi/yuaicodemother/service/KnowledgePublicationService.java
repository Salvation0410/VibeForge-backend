package com.yupi.yuaicodemother.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.*;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.model.entity.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.BadSqlGrammarException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.*;
import java.util.*;

/** MySQL 是唯一发布权威。所有短事务遵守 epoch → task → lease → document 锁序。 */
@Service
public class KnowledgePublicationService implements ApplicationRunner {
    private final KnowledgePublicationMapper mapper;
    private final CustomerServiceKnowledgeDocumentMapper documents;
    private final CustomerServiceKnowledgeEtlOutboxMapper tasks;
    private final CustomerServiceKnowledgeMutationLeaseMapper leases;
    private final KnowledgeMutationCoordinator coordinator;
    private final CustomerServiceProperties properties;
    private final ObjectMapper json = new ObjectMapper();
    private final Clock clock;

    @Autowired
    public KnowledgePublicationService(KnowledgePublicationMapper mapper,
            CustomerServiceKnowledgeDocumentMapper documents, CustomerServiceKnowledgeEtlOutboxMapper tasks,
            CustomerServiceKnowledgeMutationLeaseMapper leases, KnowledgeMutationCoordinator coordinator,
            CustomerServiceProperties properties) {
        this(mapper, documents, tasks, leases, coordinator, properties, Clock.systemUTC());
    }
    public KnowledgePublicationService(KnowledgePublicationMapper mapper,
            CustomerServiceKnowledgeDocumentMapper documents, CustomerServiceKnowledgeEtlOutboxMapper tasks,
            CustomerServiceKnowledgeMutationLeaseMapper leases, KnowledgeMutationCoordinator coordinator,
            CustomerServiceProperties properties, Clock clock) {
        this.mapper=mapper; this.documents=documents; this.tasks=tasks; this.leases=leases;
        this.coordinator=coordinator; this.properties=properties; this.clock=clock;
    }
    @Override public void run(ApplicationArguments args) {
        // 迁移前旧库可继续旧协议；已持久启用后，即使配置关闭也必须拒绝启动。
        try {
            KnowledgePublicationState state=mapper.state();
            if (state==null || (state.isEnabled() && !properties.isPublicationEnabled()))
                throw failure("KNOWLEDGE_PUBLICATION_MODE_REQUIRED");
            if (properties.isPublicationEnabled() && !state.isEnabled()
                    && !properties.isPublicationBootstrapAllowed()) throw failure("KNOWLEDGE_PUBLICATION_NOT_BOOTSTRAPPED");
        } catch (BadSqlGrammarException error) {
            if (properties.isPublicationEnabled() || error.getSQLException()==null
                    || !"42S02".equals(error.getSQLException().getSQLState())) throw error;
        }
    }
    public void requireConfiguredMode() {
        try {
            KnowledgePublicationState state=mapper.state();
            if(state==null || (state.isEnabled() && !properties.isPublicationEnabled()))
                throw failure("KNOWLEDGE_PUBLICATION_MODE_REQUIRED");
            if(properties.isPublicationEnabled() && !state.isEnabled())
                throw failure("KNOWLEDGE_PUBLICATION_NOT_BOOTSTRAPPED");
        } catch(BadSqlGrammarException error) {
            if(properties.isPublicationEnabled() || error.getSQLException()==null
                    || !"42S02".equals(error.getSQLException().getSQLState())) throw error;
        }
    }
    /** 管理事务在任何文档锁之前调用；业务变更本身推进 epoch，立即撤销旧白名单。 */
    @Transactional
    public void beforeAdministrativeMutation() {
        // 即使当前实例仍配置旧模式，也持有 epoch 锁直到管理事务结束，阻止 bootstrap 穿插。
        try {
            KnowledgePublicationState state=mapper.lockState();
            if(state==null || (state.isEnabled() && !properties.isPublicationEnabled()))
                throw failure("KNOWLEDGE_PUBLICATION_MODE_REQUIRED");
            if(properties.isPublicationEnabled() && (!state.isEnabled() || state.getPublicationId()==null))
                throw failure("KNOWLEDGE_PUBLICATION_NOT_BOOTSTRAPPED");
            requireOne(mapper.advanceEpoch());
        } catch(BadSqlGrammarException error) {
            if(properties.isPublicationEnabled() || error.getSQLException()==null
                    || !"42S02".equals(error.getSQLException().getSQLState())) throw error;
        }
    }
    @Transactional
    public PublicationContext prepare(long taskId, String owner, KnowledgeMutationCoordinator.Lease lease) {
        KnowledgePublicationState state=activeState();
        CustomerServiceKnowledgeEtlOutbox task=lockExecution(taskId,owner,lease);
        return new PublicationContext("task_"+taskId,state.getEpoch(), source(state), expected(task));
    }
    @Transactional
    public PublicationFact commit(long taskId,String owner,KnowledgeMutationCoordinator.Lease lease,
            PublicationContext context,CandidateReceipt receipt) {
        KnowledgePublicationState state=activeState();
        validateReceipt(context,receipt,lease);
        if (!context.publicationId().equals("task_"+taskId)) throw failure("KNOWLEDGE_PUBLICATION_ID_CONFLICT");
        // 已提交事实先于过期检查：确认丢失后重试只能返回相同不可变收据。
        PublicationFact existing=factOrNull(context.publicationId());
        if(existing!=null) {
            if(!existing.receipt().equals(receipt)) throw failure("KNOWLEDGE_PUBLICATION_RECEIPT_CONFLICT");
            return existing;
        }
        CustomerServiceKnowledgeEtlOutbox task=lockExecution(taskId,owner,lease);
        if(state.getEpoch()!=context.baseEpoch() || !Objects.equals(source(state),context.sourceCollection())
                || !expected(task).equals(context.documents())) throw failure("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT");
        if("INDEX".equals(task.getOperation())) {
            ReceiptDocument document=receipt.documents().stream().filter(d -> d.documentId().equals(String.valueOf(task.getDocumentId())))
                    .findFirst().orElseThrow(() -> failure("KNOWLEDGE_PUBLICATION_DOCUMENTS_INVALID"));
            requireOne(documents.completeIndex(task.getDocumentId(),task.getDocumentVersion(),task.getEtlVersion(),document.chunkCount()));
        }
        long epoch=Math.addExact(state.getEpoch(),1);
        persist(receipt,epoch);
        requireOne(tasks.finish(taskId,owner,"SUCCEEDED",null));
        return new PublicationFact(receipt.publicationId(),epoch,receipt);
    }
    @Transactional
    public ReadSnapshot snapshot() {
        KnowledgePublicationState state;
        try { state=mapper.lockState(); }
        catch(BadSqlGrammarException error) {
            // 仅明确缺表且关闭新协议才报告旧模式；连接失败或权限错误不能伪装成未启用。
            if(!properties.isPublicationEnabled() && error.getSQLException()!=null
                    && "42S02".equals(error.getSQLException().getSQLState()))
                return new ReadSnapshot(false,0,null,null,null,null,0,List.of());
            throw error;
        }
        if(state==null) throw failure("KNOWLEDGE_PUBLICATION_NOT_READY");
        if(state.isEnabled() && !properties.isPublicationEnabled()) throw failure("KNOWLEDGE_PUBLICATION_MODE_REQUIRED");
        // 未 bootstrap 是可观察的维护状态；问答方必须对 enabled=false 失败关闭。
        if(!state.isEnabled()) return new ReadSnapshot(false,state.getEpoch(),null,null,null,null,0,List.of());
        if(state.getPublicationId()==null) throw failure("KNOWLEDGE_PUBLICATION_NOT_BOOTSTRAPPED");
        CandidateReceipt receipt=requiredFact(state.getPublicationId()).receipt();
        List<DocumentIdentity> serving=servingDocuments();
        // 白名单既要满足当前业务状态，也必须来自实际发布快照，不能凭待索引 metadata 扩张。
        Set<DocumentIdentity> published=new HashSet<>(receipt.documents().stream().map(ReceiptDocument::identity).toList());
        if(!published.containsAll(serving)) throw failure("KNOWLEDGE_PUBLICATION_SERVING_MISMATCH");
        return new ReadSnapshot(true,state.getEpoch(),state.getPublicationId(),receipt.candidateCollection(),
                receipt.snapshotHash(),receipt.embeddingModelVersion(),receipt.embeddingDimension(),serving);
    }
    public PublicationFact findTaskFact(long taskId) {
        if(taskId<1) throw failure("KNOWLEDGE_PUBLICATION_ID_INVALID");
        return factOrNull("task_"+taskId);
    }
    public PublicationFact fact(String id) {
        if(id==null || !id.matches("(?:task_[0-9]+|bootstrap_[a-f0-9]{32})")) throw failure("KNOWLEDGE_PUBLICATION_ID_INVALID");
        return requiredFact(id);
    }
    @Transactional
    public PublicationContext prepareBootstrap(String id,String source,KnowledgeMutationCoordinator.Lease lease) {
        KnowledgePublicationState state=bootstrapState();
        validateBootstrapLease(lease);
        validateSource(source);
        if(id==null || !id.matches("bootstrap_[a-f0-9]{32}")) throw failure("KNOWLEDGE_PUBLICATION_ID_INVALID");
        return new PublicationContext(id,state.getEpoch(),source,servingDocuments());
    }
    @Transactional
    public PublicationFact commitBootstrap(PublicationContext context,CandidateReceipt receipt,KnowledgeMutationCoordinator.Lease lease) {
        mapper.lockState();
        validateReceipt(context,receipt,lease);
        if(context.publicationId()==null || !context.publicationId().matches("bootstrap_[a-f0-9]{32}"))
            throw failure("KNOWLEDGE_PUBLICATION_ID_INVALID");
        PublicationFact existing=factOrNull(context.publicationId());
        if(existing!=null) {
            if(!existing.receipt().equals(receipt)) throw failure("KNOWLEDGE_PUBLICATION_RECEIPT_CONFLICT");
            return existing;
        }
        KnowledgePublicationState state=bootstrapState();
        validateBootstrapLease(lease);
        if(state.getEpoch()!=context.baseEpoch() || !servingDocuments().equals(context.documents()))
            throw failure("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT");
        long epoch=Math.addExact(state.getEpoch(),1);
        persist(receipt,epoch);
        return new PublicationFact(receipt.publicationId(),epoch,receipt);
    }
    @Transactional
    public void acknowledgeMirror(ReadSnapshot snapshot) {
        KnowledgePublicationState state=activeState();
        mapper.supersedeAliases(state.getPublicationId());
        if(state.getEpoch()==snapshot.epoch() && Objects.equals(state.getPublicationId(),snapshot.publicationId()))
            mapper.acknowledgeAlias(snapshot.publicationId(),snapshot.epoch());
    }
    public void lockTaskForFinalization(long id) {
        if(properties.isPublicationEnabled()) {
            activeState();
            tasks.lockTask(id);
        }
    }
    public List<CustomerServiceKnowledgeDocument> servingRegistry() { return registry(); }
    private KnowledgePublicationState activeState() {
        if(!properties.isPublicationEnabled()) throw failure("KNOWLEDGE_PUBLICATION_MODE_REQUIRED");
        KnowledgePublicationState state=mapper.lockState();
        if(state==null || !state.isEnabled() || state.getPublicationId()==null)
            throw failure("KNOWLEDGE_PUBLICATION_NOT_BOOTSTRAPPED");
        return state;
    }
    private KnowledgePublicationState bootstrapState() {
        if(!properties.isPublicationEnabled() || !properties.isPublicationBootstrapAllowed())
            throw failure("KNOWLEDGE_PUBLICATION_BOOTSTRAP_FORBIDDEN");
        KnowledgePublicationState state=mapper.lockState();
        if(state==null || state.isEnabled() || state.getPublicationId()!=null || mapper.unfinishedTasks()!=0)
            throw failure("KNOWLEDGE_PUBLICATION_BOOTSTRAP_NOT_EMPTY");
        return state;
    }
    private void validateBootstrapLease(KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeMutationLease row=leases.lockLease(lease.operationId());
        if(!"REBUILD".equals(lease.operation()) || !validLockedLease(row,lease))
            throw failure("KNOWLEDGE_PUBLICATION_LEASE_INVALID");
    }
    private CustomerServiceKnowledgeEtlOutbox lockExecution(long id,String owner,KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeEtlOutbox task=tasks.lockTask(id);
        LocalDateTime now=LocalDateTime.ofInstant(clock.instant(),ZoneOffset.UTC);
        if(task==null || !"PROCESSING".equals(task.getStatus()) || !Objects.equals(owner,task.getProcessingOwner())
                || task.getProcessingDeadline()==null || !task.getProcessingDeadline().isAfter(now))
            throw failure("KNOWLEDGE_PUBLICATION_CLAIM_LOST");
        CustomerServiceKnowledgeMutationLease row=leases.lockLease(lease.operationId());
        String scope="REBUILD".equals(task.getOperation()) ? "collection:"+properties.getCollectionAlias() : "document:"+task.getDocumentId();
        if(row==null || !Objects.equals(row.getOwner(),owner) || !Objects.equals(task.getOperation(),lease.operation())
                || !scope.equals(lease.scope()) || !validLockedLease(row,lease))
            throw failure("KNOWLEDGE_PUBLICATION_LEASE_INVALID");
        return task;
    }
    private boolean validLockedLease(CustomerServiceKnowledgeMutationLease row,KnowledgeMutationCoordinator.Lease lease) {
        // RR 事务中前面的事实查询可能已建立读视图；授权必须使用锁定行的当前值，不能复用快照中的 current。
        return row!=null && row.getRevokedAt()==null && row.getExpiresAt()!=null
                && row.getExpiresAt().toEpochSecond(ZoneOffset.UTC)==lease.expiresAt()
                && lease.expiresAt()>clock.instant().getEpochSecond()
                && Objects.equals(row.getFence(),lease.fence()) && Objects.equals(row.getScope(),lease.scope())
                && Objects.equals(row.getOperation(),lease.operation()) && coordinator.validate(lease).verified();
    }
    private List<DocumentIdentity> expected(CustomerServiceKnowledgeEtlOutbox task) {
        List<DocumentIdentity> result=new ArrayList<>(servingDocuments());
        if("REBUILD".equals(task.getOperation())) return List.copyOf(result);
        CustomerServiceKnowledgeDocument document=documents.findIncludingDeleted(task.getDocumentId());
        if(document==null || !Objects.equals(document.getEtlVersion(),task.getEtlVersion()))
            throw failure("KNOWLEDGE_TASK_STALE");
        result.removeIf(d -> d.documentId().equals(String.valueOf(task.getDocumentId())));
        if("INDEX".equals(task.getOperation())) {
            if(!Objects.equals(document.getDocumentVersion(),task.getDocumentVersion())
                    || Integer.valueOf(1).equals(document.getIsDelete()) || "DISABLED".equals(document.getStatus())
                    || "DELETING".equals(document.getStatus())) throw failure("KNOWLEDGE_TASK_STALE");
            result.add(identity(document,document.getDocumentVersion(),task.getEtlVersion(),document.getActivationRevision()));
        } else if("DELETE".equals(task.getOperation())) {
            long target=document.getIndexedVersion()>0 ? document.getIndexedVersion():document.getDocumentVersion();
            if((!"DISABLED".equals(document.getStatus()) && !"DELETING".equals(document.getStatus()))
                    || task.getDocumentVersion()!=target) throw failure("KNOWLEDGE_TASK_STALE");
        } else throw failure("KNOWLEDGE_TASK_OPERATION_INVALID");
        result.sort(Comparator.comparing(DocumentIdentity::documentId));
        return List.copyOf(result);
    }
    private List<CustomerServiceKnowledgeDocument> registry() {
        List<CustomerServiceKnowledgeDocument> result=documents.listRebuildableLimited(properties.getRebuildMaxDocuments()+1);
        if(result.size()>properties.getRebuildMaxDocuments()) throw failure("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS");
        return result;
    }
    private List<DocumentIdentity> servingDocuments() {
        return registry().stream().map(d -> identity(d,d.getIndexedVersion(),d.getIndexedEtlVersion(),d.getIndexedActivationRevision()))
                .sorted(Comparator.comparing(DocumentIdentity::documentId)).toList();
    }
    private DocumentIdentity identity(CustomerServiceKnowledgeDocument d,Long version,Long index,Long activation) {
        if(version==null || version<1 || index==null || index<1 || activation==null || activation<1
                || d.getContentHash()==null || !d.getContentHash().matches("[a-f0-9]{64}") || d.getName()==null || d.getFileType()==null)
            throw failure("KNOWLEDGE_REBUILD_CONTENT_REVISION_MISSING");
        return new DocumentIdentity(String.valueOf(d.getId()),version,index,activation,d.getContentHash(),d.getName(),d.getFileType().toUpperCase(Locale.ROOT));
    }
    private void validateReceipt(PublicationContext context,CandidateReceipt receipt,KnowledgeMutationCoordinator.Lease lease) {
        if(receipt==null || !Objects.equals(context.publicationId(),receipt.publicationId()) || context.baseEpoch()!=receipt.baseEpoch()
                || !Objects.equals(receipt.operationId(),lease.operationId()) || receipt.fence()!=lease.fence()
                || receipt.candidateCollection()==null || !receipt.candidateCollection().matches(candidatePattern())
                || receipt.candidateCollection().equals(context.sourceCollection())
                || receipt.snapshotHash()==null || !receipt.snapshotHash().matches("[a-f0-9]{64}")
                || receipt.embeddingModelVersion()==null || receipt.embeddingModelVersion().isBlank() || receipt.embeddingModelVersion().length()>255
                || receipt.embeddingDimension()<1 || receipt.embeddingDimension()>65536 || receipt.documents()==null
                || receipt.documents().stream().anyMatch(d -> d==null || d.chunkCount()<0 || d.chunkCount()>1_000_000)
                || !context.documents().equals(receipt.documents().stream().map(ReceiptDocument::identity).toList()))
            throw failure("KNOWLEDGE_PUBLICATION_RECEIPT_INVALID");
    }
    private String candidatePattern() {
        // Milvus 集合名最多 255 字符；长 alias 统一使用前 210 字符作为命名前缀。
        String alias=properties.getCollectionAlias();
        return java.util.regex.Pattern.quote(alias.substring(0,Math.min(alias.length(),210)))+"_pub_[a-f0-9]{32}";
    }
    private void validateSource(String source) {
        if(source==null || !source.matches("[A-Za-z_][A-Za-z0-9_]{0,254}") || source.equals(properties.getCollectionAlias())
                || source.contains("_pub_")) throw failure("KNOWLEDGE_PUBLICATION_SOURCE_INVALID");
    }
    private String source(KnowledgePublicationState state) { return requiredFact(state.getPublicationId()).receipt().candidateCollection(); }
    private void persist(CandidateReceipt receipt,long epoch) {
        try {
            requireOne(mapper.insertFact(receipt.publicationId(),epoch,receipt.candidateCollection(),json.writeValueAsString(receipt)));
            requireOne(mapper.activate(receipt.publicationId(),epoch));
            requireOne(mapper.enqueueAlias(receipt.publicationId(),epoch));
        } catch(JsonProcessingException error) { throw failure("KNOWLEDGE_PUBLICATION_RECEIPT_INVALID"); }
    }
    private PublicationFact requiredFact(String id) {
        PublicationFact result=factOrNull(id);
        if(result==null) throw failure("KNOWLEDGE_PUBLICATION_NOT_FOUND");
        return result;
    }
    private PublicationFact factOrNull(String id) {
        KnowledgePublicationRecord row=mapper.fact(id);
        if(row==null) return null;
        try { return new PublicationFact(row.getPublicationId(),row.getEpoch(),json.readValue(row.getReceiptJson(),CandidateReceipt.class)); }
        catch(JsonProcessingException error) { throw failure("KNOWLEDGE_PUBLICATION_FACT_INVALID"); }
    }
    private static void requireOne(int result) { if(result!=1) throw failure("KNOWLEDGE_PUBLICATION_WRITE_CONFLICT"); }
    private static IllegalStateException failure(String code) { return new IllegalStateException(code); }
}
