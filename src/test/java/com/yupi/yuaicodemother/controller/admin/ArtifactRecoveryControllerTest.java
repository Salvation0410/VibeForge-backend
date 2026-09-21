package com.yupi.yuaicodemother.controller.admin;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublishResult;
import com.yupi.yuaicodemother.core.artifact.HtmlSmokeTestResult;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.model.dto.app.HtmlArtifactRecoveryRequest;
import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;

import java.lang.reflect.Method;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ArtifactRecoveryControllerTest {
    private static final String HTML = "<!doctype html><html><head></head><body>recovered</body></html>";

    @Test
    void exposesAdminOnlyRecoveryEndpoint() throws Exception {
        RequestMapping root = ArtifactRecoveryController.class.getAnnotation(RequestMapping.class);
        Method method = ArtifactRecoveryController.class.getMethod("recover", HtmlArtifactRecoveryRequest.class);
        PostMapping post = method.getAnnotation(PostMapping.class);
        AuthCheck auth = method.getAnnotation(AuthCheck.class);

        assertEquals("/apps/admin/artifacts/html", root.value()[0]);
        assertEquals("/recover", post.value()[0]);
        assertEquals(UserConstant.ADMIN_ROLE, auth.mustRole());
    }

    @Test
    void defaultsToDryRunAndNeverPublishes() {
        ArtifactPublicationService publisher = mock(ArtifactPublicationService.class);
        when(publisher.inspectHtml(HTML)).thenReturn(new ArtifactPublicationService.HtmlInspectionResult(
                true, List.of(), HtmlSmokeTestResult.success()));
        ArtifactRecoveryController controller = new ArtifactRecoveryController(publisher);
        HtmlArtifactRecoveryRequest request = request();

        var response = controller.recover(request).getData();

        assertTrue(request.isDryRun());
        assertTrue(response.dryRun());
        assertTrue(response.valid());
        assertFalse(response.published());
        verify(publisher).inspectHtml(HTML);
        verify(publisher, never()).publishHtml(42L, "recovery-1", HTML, "manual-recovery", "MANUAL_RECOVERY");
    }

    @Test
    void nullDryRunValueRemainsReadOnly() throws Exception {
        HtmlArtifactRecoveryRequest request = new ObjectMapper().readValue("""
                {"appId":42,"requestId":"recovery-1","candidateHtml":"candidate",
                 "sourceDescription":"reviewed","dryRun":null}
                """, HtmlArtifactRecoveryRequest.class);

        assertTrue(request.isDryRun());
    }

    @Test
    void publishesOnlyWhenCommitIsExplicit() {
        ArtifactPublicationService publisher = mock(ArtifactPublicationService.class);
        when(publisher.publishHtml(42L, "recovery-1", HTML, "manual-recovery", "MANUAL_RECOVERY"))
                .thenReturn(new ArtifactPublishResult(true, "recovery-1", Map.of("index.html", "hash")));
        ArtifactRecoveryController controller = new ArtifactRecoveryController(publisher);
        HtmlArtifactRecoveryRequest request = request();
        request.setDryRun(false);

        var response = controller.recover(request).getData();

        assertFalse(response.dryRun());
        assertTrue(response.valid());
        assertTrue(response.published());
        assertEquals("recovery-1", response.versionId());
        assertEquals("chat-history message 18", response.sourceDescription());
        verify(publisher, never()).inspectHtml(HTML);
        verify(publisher).publishHtml(42L, "recovery-1", HTML, "manual-recovery", "MANUAL_RECOVERY");
    }

    @Test
    void rejectsMissingReviewedCandidateOrSource() {
        ArtifactRecoveryController controller = new ArtifactRecoveryController(mock(ArtifactPublicationService.class));
        HtmlArtifactRecoveryRequest missingCandidate = request();
        missingCandidate.setCandidateHtml(" ");
        HtmlArtifactRecoveryRequest missingSource = request();
        missingSource.setSourceDescription(null);

        assertThrows(BusinessException.class, () -> controller.recover(missingCandidate));
        assertThrows(BusinessException.class, () -> controller.recover(missingSource));
    }

    private HtmlArtifactRecoveryRequest request() {
        HtmlArtifactRecoveryRequest request = new HtmlArtifactRecoveryRequest();
        request.setAppId(42L);
        request.setRequestId("recovery-1");
        request.setCandidateHtml(HTML);
        request.setSourceDescription("chat-history message 18");
        return request;
    }
}
