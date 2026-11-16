package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.*;
import com.yupi.yuaicodemother.controller.InternalKnowledgePublicationController;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import com.yupi.yuaicodemother.service.*;
import com.yupi.yuaicodemother.worker.KnowledgePublicationAliasWorker;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgePublicationEdgesTest {
    final KnowledgePublicationService publications=mock(KnowledgePublicationService.class);
    final CustomerServiceAiClient client=mock(CustomerServiceAiClient.class);
    final CustomerServiceProperties properties=new CustomerServiceProperties();
    @Test void internalEndpointsAuthenticateBeforeDatabaseAccess() {
        var ai=new AiEngineProperties();ai.setToken("test-token");
        var controller=new InternalKnowledgePublicationController(publications,ai);
        assertThrows(BusinessException.class,()->controller.snapshot(null));
        assertThrows(BusinessException.class,()->controller.fact("Bearer wrong","task_1"));
        verifyNoInteractions(publications);
        controller.snapshot("Bearer test-token");verify(publications).snapshot();
    }
    @Test void aliasMirrorIsPeriodicallyRecheckedEvenAfterAcknowledgement() {
        properties.setPublicationEnabled(true);
        var snapshot=new ReadSnapshot(true,7,"task_1","specific_collection","a".repeat(64),"model",3,List.of());
        when(publications.snapshot()).thenReturn(snapshot);
        var worker=new KnowledgePublicationAliasWorker(publications,client,properties);
        worker.reconcile();worker.reconcile();
        verify(client,times(2)).reconcileAlias(new ReconcileAliasRequest(properties.getCollectionAlias(),"specific_collection","task_1",7));
        verify(publications,times(2)).acknowledgeMirror(snapshot);
    }
    @Test void failedAliasMirrorNeverAcknowledgesOrRollsBackPublication() {
        properties.setPublicationEnabled(true);
        when(publications.snapshot()).thenReturn(new ReadSnapshot(true,7,"task_1","specific_collection","a".repeat(64),"model",3,List.of()));
        doThrow(new CustomerServiceAiClient.CallException("KNOWLEDGE_ALIAS_SUPERSEDED",true)).when(client).reconcileAlias(any());
        new KnowledgePublicationAliasWorker(publications,client,properties).reconcile();
        verify(publications,never()).acknowledgeMirror(any());
    }
    @Test void bootstrapRequiresMaintenanceSwitchBeforeAcquiringLease() {
        var coordinator=mock(KnowledgeMutationCoordinator.class);
        var service=new KnowledgePublicationBootstrapService(publications,coordinator,client,properties,mock(OssManager.class));
        assertThrows(IllegalStateException.class,()->service.bootstrap("legacy_specific"));verifyNoInteractions(coordinator,client,publications);
    }
    @Test void bootstrapUsesRebuildMutationAndNeverIndex() {
        properties.setPublicationEnabled(true);properties.setPublicationBootstrapAllowed(true);
        var coordinator=mock(KnowledgeMutationCoordinator.class);
        var lease=new KnowledgeMutationCoordinator.Lease("collection:"+properties.getCollectionAlias(),"operation","REBUILD",1,2000000000,"unused");
        when(coordinator.acquire(anyString(),anyString(),eq("REBUILD"),anyString())).thenReturn(lease);
        when(publications.prepareBootstrap(anyString(),eq("legacy_specific"),eq(lease))).thenAnswer(invocation ->
                new PublicationContext(invocation.getArgument(0),0,"legacy_specific",List.of()));
        when(publications.servingRegistry()).thenReturn(List.of());
        when(client.preparePublication(any())).thenAnswer(invocation -> {
            CandidateRequest request=invocation.getArgument(0);assertInstanceOf(CustomerServiceAiClient.RebuildRequest.class,request.mutation());
            assertEquals("legacy_specific",request.context().sourceCollection());
            return new CandidateReceipt(request.context().publicationId(),0,"operation",1,"customer_service_knowledge_pub_"+"a".repeat(32),"a".repeat(64),"model",3,List.of());
        });
        new KnowledgePublicationBootstrapService(publications,coordinator,client,properties,mock(OssManager.class)).bootstrap("legacy_specific");
        verify(publications).commitBootstrap(any(),any(),eq(lease));verify(coordinator).revoke(lease);verify(client,never()).index(any());
    }
}
