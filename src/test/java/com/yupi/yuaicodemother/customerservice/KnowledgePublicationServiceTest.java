package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.*;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.model.entity.*;
import com.yupi.yuaicodemother.service.*;
import org.junit.jupiter.api.*;
import java.time.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgePublicationServiceTest {
    static final String HASH="a".repeat(64);
    static final String OLD="customer_service_knowledge_pub_"+"1".repeat(32);
    static final String NEW="customer_service_knowledge_pub_"+"2".repeat(32);
    static final Instant NOW=Instant.parse("2030-01-01T00:00:00Z");
    final KnowledgePublicationMapper mapper=mock(KnowledgePublicationMapper.class);
    final CustomerServiceKnowledgeDocumentMapper documents=mock(CustomerServiceKnowledgeDocumentMapper.class);
    final CustomerServiceKnowledgeEtlOutboxMapper tasks=mock(CustomerServiceKnowledgeEtlOutboxMapper.class);
    final CustomerServiceKnowledgeMutationLeaseMapper leases=mock(CustomerServiceKnowledgeMutationLeaseMapper.class);
    final KnowledgeMutationCoordinator coordinator=mock(KnowledgeMutationCoordinator.class);
    final CustomerServiceProperties properties=new CustomerServiceProperties();
    final KnowledgeMutationCoordinator.Lease lease=new KnowledgeMutationCoordinator.Lease("document:2","attempt", "INDEX",3,2000000000L,"unused");
    KnowledgePublicationService service;
    KnowledgePublicationState state;
    CustomerServiceKnowledgeEtlOutbox task;
    @BeforeEach void setup() throws Exception {
        properties.setPublicationEnabled(true);
        service=new KnowledgePublicationService(mapper,documents,tasks,leases,coordinator,properties,Clock.fixed(NOW,ZoneOffset.UTC));
        state=new KnowledgePublicationState(); state.setEpoch(7); state.setEnabled(true);state.setPublicationId("task_1");
        when(mapper.lockState()).thenReturn(state); when(mapper.state()).thenReturn(state);
        var old=new CandidateReceipt("task_1",6,"old-attempt",1,OLD,HASH,"model-v1",3,List.of());
        when(mapper.fact("task_1")).thenReturn(record(old,7));
        when(documents.listRebuildableLimited(anyInt())).thenReturn(List.of());
        var document=new CustomerServiceKnowledgeDocument(); document.setId(2L);document.setDocumentVersion(1L);
        document.setEtlVersion(2L);document.setActivationRevision(1L);document.setIndexedVersion(0L);
        document.setContentHash(HASH);document.setName("原文");document.setFileType("TXT");document.setStatus("INDEXING");document.setIsDelete(0);
        when(documents.findIncludingDeleted(2)).thenReturn(document);
        task=new CustomerServiceKnowledgeEtlOutbox();task.setId(2L);task.setDocumentId(2L);task.setDocumentVersion(1L);task.setEtlVersion(2L);
        task.setStatus("PROCESSING");task.setOperation("INDEX");task.setProcessingOwner("owner");
        task.setProcessingDeadline(LocalDateTime.ofInstant(NOW.plusSeconds(30),ZoneOffset.UTC));
        when(tasks.lockTask(2)).thenReturn(task);
        var leaseRow=new CustomerServiceKnowledgeMutationLease();leaseRow.setOwner("owner");
        leaseRow.setOperationId(lease.operationId());leaseRow.setFence(lease.fence());leaseRow.setScope(lease.scope());
        leaseRow.setOperation(lease.operation());leaseRow.setExpiresAt(LocalDateTime.ofEpochSecond(lease.expiresAt(),0,ZoneOffset.UTC));
        when(leases.lockLease("attempt")).thenReturn(leaseRow);
        when(coordinator.validate(lease)).thenReturn(new KnowledgeMutationCoordinator.Validation(true,true,"document:2","attempt","INDEX",3,2000000000L));
        when(documents.completeIndex(2,1,2,4)).thenReturn(1);when(tasks.finish(2,"owner","SUCCEEDED",null)).thenReturn(1);
        when(mapper.insertFact(anyString(),anyLong(),anyString(),anyString())).thenReturn(1);
        when(mapper.activate(anyString(),anyLong())).thenReturn(1);when(mapper.enqueueAlias(anyString(),anyLong())).thenReturn(1);
    }
    @Test void rejectsLostClaimBeforeLockingLease() {
        task.setProcessingOwner("new-owner");
        assertEquals("KNOWLEDGE_PUBLICATION_CLAIM_LOST",assertThrows(IllegalStateException.class,()->service.prepare(2,"owner",lease)).getMessage());
        verifyNoInteractions(leases);
    }
    @Test void inventoryBoundsAndExtraRowDoNotExposeReceipts() throws Exception {
        assertThrows(IllegalStateException.class,()->service.inventory(-1,10));
        assertThrows(IllegalStateException.class,()->service.inventory(0,101));
        var first=new InventoryEntry("task_1",1,OLD,"2030-01-01 00:00:00");
        var second=new InventoryEntry("task_2",2,NEW,"2030-01-01 00:00:01");
        when(mapper.inventory(0,2)).thenReturn(List.of(first,second));
        var page=service.inventory(0,1);
        assertTrue(page.hasMore());assertEquals(1,page.nextEpoch());assertEquals(List.of(first),page.entries());
        String encoded=new ObjectMapper().writeValueAsString(page);
        assertFalse(encoded.contains("receipt"));assertFalse(encoded.contains("sha256"));
        var root=new ObjectMapper().readTree(encoded);assertTrue(root.get("epoch").isIntegralNumber());
        when(mapper.inventory(2,2)).thenReturn(List.of());
        assertEquals(2,service.inventory(2,1).nextEpoch());
    }
    @Test void rejectsExpiredClaimAndInvalidLease() {
        task.setProcessingDeadline(LocalDateTime.ofInstant(NOW,ZoneOffset.UTC));
        assertThrows(IllegalStateException.class,()->service.prepare(2,"owner",lease));
        task.setProcessingDeadline(LocalDateTime.ofInstant(NOW.plusSeconds(1),ZoneOffset.UTC));
        when(coordinator.validate(lease)).thenReturn(new KnowledgeMutationCoordinator.Validation(false,false,"document:2","attempt","INDEX",3,2000000000L));
        assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",assertThrows(IllegalStateException.class,()->service.prepare(2,"owner",lease)).getMessage());
    }
    @Test void revokedLockedRowCannotBeAuthorizedByOlderConsistentReadValidation() {
        var row=leases.lockLease("attempt");row.setRevokedAt(LocalDateTime.ofInstant(NOW.minusSeconds(1),ZoneOffset.UTC));
        assertEquals("KNOWLEDGE_PUBLICATION_LEASE_INVALID",assertThrows(IllegalStateException.class,()->service.prepare(2,"owner",lease)).getMessage());
    }
    @Test void epochConflictCannotWriteAnyFact() {
        var context=service.prepare(2,"owner",lease);state.setEpoch(8);
        assertEquals("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT",assertThrows(IllegalStateException.class,()->service.commit(2,"owner",lease,context,receipt(context))).getMessage());
        verify(mapper,never()).insertFact(anyString(),anyLong(),anyString(),anyString());
    }
    @Test void sameReceiptReturnsPersistedFactEvenAfterClaimExpiry() throws Exception {
        var context=service.prepare(2,"owner",lease);var receipt=receipt(context);
        when(mapper.fact("task_2")).thenReturn(record(receipt,8));
        task.setProcessingDeadline(LocalDateTime.ofInstant(NOW.minusSeconds(10),ZoneOffset.UTC));
        clearInvocations(tasks,leases,coordinator);
        assertEquals(receipt,service.commit(2,"owner",lease,context,receipt).receipt());
        verifyNoInteractions(tasks,leases,coordinator);
        var conflict=new CandidateReceipt(receipt.publicationId(),receipt.baseEpoch(),receipt.operationId(),receipt.fence(),NEW,"b".repeat(64),"model-v1",3,receipt.documents());
        assertEquals("KNOWLEDGE_PUBLICATION_RECEIPT_CONFLICT",assertThrows(IllegalStateException.class,()->service.commit(2,"owner",lease,context,conflict)).getMessage());
    }
    @Test void writesPublicationHeadServingSuccessAndAliasInOrder() {
        var context=service.prepare(2,"owner",lease);
        assertEquals(8,service.commit(2,"owner",lease,context,receipt(context)).epoch());
        var ordered=inOrder(documents,mapper,tasks);
        ordered.verify(documents).completeIndex(2,1,2,4);
        ordered.verify(mapper).insertFact(eq("task_2"),eq(8L),eq(NEW),anyString());
        ordered.verify(mapper).activate("task_2",8);ordered.verify(mapper).enqueueAlias("task_2",8);
        ordered.verify(tasks).finish(2,"owner","SUCCEEDED",null);
    }
    @Test void receiptRejectsWrongDocumentListAndSharedSourceCollection() {
        var context=service.prepare(2,"owner",lease);var valid=receipt(context);
        var empty=new CandidateReceipt("task_2",7,"attempt",3,NEW,HASH,"model-v1",3,List.of());
        assertThrows(IllegalStateException.class,()->service.commit(2,"owner",lease,context,empty));
        var shared=new CandidateReceipt("task_2",7,"attempt",3,OLD,HASH,"model-v1",3,valid.documents());
        assertThrows(IllegalStateException.class,()->service.commit(2,"owner",lease,context,shared));
    }
    @Test void snapshotIncludesHashAndUsesOneLockedState() {
        var snapshot=service.snapshot();assertEquals(HASH,snapshot.snapshotHash());assertEquals(OLD,snapshot.collectionName());
        verify(mapper).lockState();verify(documents).listRebuildableLimited(anyInt());
    }
    @Test void durableModeBlocksDisabledLiveInstanceAndEnabledWithoutHead() {
        properties.setPublicationEnabled(false);
        assertEquals("KNOWLEDGE_PUBLICATION_MODE_REQUIRED",assertThrows(IllegalStateException.class,service::requireConfiguredMode).getMessage());
        properties.setPublicationEnabled(true);state.setEnabled(false);
        assertThrows(IllegalStateException.class,service::requireConfiguredMode);
    }
    @Test void administrativeChangeLocksEpochAndAdvancesIt() {
        when(mapper.advanceEpoch()).thenReturn(1);service.beforeAdministrativeMutation();
        var ordered=inOrder(mapper);ordered.verify(mapper).lockState();ordered.verify(mapper).advanceEpoch();
    }
    @Test void bootstrapRepeatedReceiptReturnsFactWithoutActiveLeaseOrMaintenanceSwitch() throws Exception {
        String id="bootstrap_"+"3".repeat(32);
        var context=new PublicationContext(id,0,"legacy_canonical",List.of());
        var receipt=new CandidateReceipt(id,0,"attempt",3,NEW,HASH,"model-v1",3,List.of());
        when(mapper.fact(id)).thenReturn(record(receipt,1));
        properties.setPublicationBootstrapAllowed(false);
        assertEquals(receipt,service.commitBootstrap(context,receipt,lease).receipt());
        verifyNoInteractions(leases,coordinator);
    }
    @Test void disabledOldSchemaIsExplicitButDatabaseFailuresNeverLookLikeDisabledMode() {
        properties.setPublicationEnabled(false);
        when(mapper.lockState()).thenThrow(new org.springframework.jdbc.BadSqlGrammarException("snapshot","controlled",new java.sql.SQLException("missing","42S02")));
        assertFalse(service.snapshot().enabled());
        properties.setPublicationEnabled(true);assertThrows(org.springframework.jdbc.BadSqlGrammarException.class,service::snapshot);
        properties.setPublicationEnabled(false);
        doThrow(new org.springframework.dao.DataAccessResourceFailureException("offline")).when(mapper).lockState();
        assertThrows(org.springframework.dao.DataAccessResourceFailureException.class,service::snapshot);
    }
    @Test void preBootstrapSnapshotExposesDisabledMaintenanceState() {
        state.setEnabled(false);state.setPublicationId(null);
        var snapshot=service.snapshot();assertFalse(snapshot.enabled());assertNull(snapshot.collectionName());
        assertTrue(snapshot.documents().isEmpty());
    }
    @Test void internalNumbersSurviveFrontendLongStringSerializer() throws Exception {
        var module=new SimpleModule();module.addSerializer(Long.class,ToStringSerializer.instance);module.addSerializer(Long.TYPE,ToStringSerializer.instance);
        var json=new ObjectMapper().registerModule(module);
        var context=service.prepare(2,"owner",lease);
        var tree=json.readTree(json.writeValueAsBytes(receipt(context)));
        assertTrue(tree.get("baseEpoch").isNumber());assertTrue(tree.get("fence").isNumber());
        assertTrue(tree.get("documents").get(0).get("documentVersion").isNumber());
        assertTrue(json.valueToTree(service.snapshot()).get("epoch").isNumber());
    }
    static KnowledgePublicationRecord record(CandidateReceipt receipt,long epoch) throws Exception {
        var record=new KnowledgePublicationRecord();record.setPublicationId(receipt.publicationId());record.setEpoch(epoch);
        record.setReceiptJson(new ObjectMapper().writeValueAsString(receipt));return record;
    }
    static CandidateReceipt receipt(PublicationContext context) {
        return new CandidateReceipt(context.publicationId(),context.baseEpoch(),"attempt",3,NEW,HASH,"model-v1",3,
                context.documents().stream().map(d->new ReceiptDocument(d.documentId(),d.documentVersion(),d.indexRevision(),d.activationRevision(),d.sha256(),d.fileName(),d.fileType(),4)).toList());
    }
}
