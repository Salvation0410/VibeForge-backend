package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import org.junit.jupiter.api.Test;

import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HtmlArtifactValidatorTest {
    private final HtmlArtifactValidator validator = new HtmlArtifactValidator();

    @Test
    void completeHtmlWithoutInlineCodePasses() {
        assertTrue(validator.validate(artifact("<!doctype html><html><head></head><body><main>OK</main></body></html>")).valid());
    }

    @Test
    void documentBoundaryScannerIgnoresTagsInsideScriptRawText() {
        String script = "<script>const s = '<body></body></html>';</script>";
        assertTrue(validator.validate(artifact("<html><head></head><body>" + script + "</body></html>")).valid());
        assertHasError("<html><head></head>" + script + "</html>", "HTML_DOCUMENT_INCOMPLETE");
    }

    @Test
    void embeddedBlockScannerIgnoresCrossTagTextInsideRawText() {
        assertTrue(validator.validate(artifact(completeBody("<script>const x = '<style>';</script>"))).valid());
        assertTrue(validator.validate(artifact(completeBody("<style>content: '<script>';</style>"))).valid());
    }

    @Test
    void embeddedBlockScannerIgnoresTagsInsideHtmlComments() {
        String comments = "<!-- <script>const broken = {</script> -->"
                + "<!-- <style>broken {</style> -->";
        assertTrue(validator.validate(artifact(completeBody(comments))).valid());
    }

    @Test
    void incompleteDocumentReturnsStableError() {
        for (String html : new String[]{
                "<!doctype html><html><head></head><body></body>",
                "<!doctype html><html><head><title>x</title><body></body></html>",
                "<!doctype html><html><head></head><body><main>x</main></html>"}) {
            assertHasError(html, "HTML_DOCUMENT_INCOMPLETE");
        }
    }

    @Test
    void markdownFenceResidueReturnsStableError() {
        assertHasError("```html\n" + completeBody("") + "\n```", "HTML_MARKDOWN_RESIDUE");
    }

    @Test
    void incompleteStyleReturnsStableError() {
        for (String style : new String[]{
                "<style>main { color: red; </style>",
                "<style>main { content: \"oops; }</style>",
                "<style>main { color: red; /* unfinished }</style>",
                "<style>main { color: red; }"}) {
            assertHasError(completeBody(style), "HTML_STYLE_INCOMPLETE");
        }
    }

    @Test
    void completeStyleIgnoresDelimitersInStringsAndComments() {
        String style = "<style>main::before { content: \"}\"; } /* { ignored } */</style>";
        assertTrue(validator.validate(artifact(completeBody(style))).valid());
    }

    @Test
    void incompleteScriptReturnsStableError() {
        for (String script : new String[]{
                "<script>function run() { return 1;</script>",
                "<script>const value = `hello ${name;</script>",
                "<script>const value = 'unfinished;</script>",
                "<script>/* unfinished</script>",
                "<script>const values = [1, 2;</script>",
                "<script>const value = 1;"}) {
            assertHasError(completeBody(script), "HTML_SCRIPT_INCOMPLETE");
        }
    }

    @Test
    void completeScriptSupportsTemplateInterpolationAndRegexLiteral() {
        String script = """
                <script>
                const suffix = /[})\\]]+/g;
                const render = (name) => `hello ${name.replace(suffix, '')}: ${`${name}!`}`;
                let counter = 0;
                counter++;
                const ratio = counter / 2;
                document.body.dataset.value = render('Ada');
                </script>
                """;
        assertTrue(validator.validate(artifact(completeBody(script))).valid());
    }

    @Test
    void orphanClosingStyleOrScriptTagIsIncomplete() {
        assertHasError(completeBody("</style>"), "HTML_STYLE_INCOMPLETE");
        assertHasError(completeBody("</script>"), "HTML_SCRIPT_INCOMPLETE");
    }

    @Test
    void trailingJavascriptFragmentReturnsStableError() {
        for (String script : new String[]{
                "<script>const value = 1 +</script>",
                "<script>const value = condition ? ready :</script>",
                "<script>list.innerHTML = displayList.map(msg => `${escapeText</script>"}) {
            assertHasError(completeBody(script), "HTML_SCRIPT_TRAILING_FRAGMENT");
        }
    }

    @Test
    void validateReturnsAllErrorsAndValidateOrThrowUsesWrapperCode() {
        var result = validator.validate(artifact("```<html><head><style>main {</style></head><body><script>const x = 1 +</script>"));
        Set<String> codes = result.errors().stream().map(ArtifactValidationError::code).collect(Collectors.toSet());
        assertFalse(result.valid());
        assertTrue(codes.contains("HTML_DOCUMENT_INCOMPLETE"));
        assertTrue(codes.contains("HTML_MARKDOWN_RESIDUE"));
        assertTrue(codes.contains("HTML_STYLE_INCOMPLETE"));
        assertTrue(codes.contains("HTML_SCRIPT_TRAILING_FRAGMENT"));

        ArtifactValidationException exception = assertThrows(ArtifactValidationException.class,
                () -> validator.validateOrThrow(artifact("<html><head></head><body></body>")));
        assertEquals("HTML_VALIDATION_FAILED", exception.getErrorCode());
        assertEquals("index.html", exception.getFile());
        assertTrue(exception.getMessage().contains("HTML_DOCUMENT_INCOMPLETE"));
    }

    private void assertHasError(String html, String code) {
        var result = validator.validate(artifact(html));
        assertFalse(result.valid(), () -> "expected " + code + " for " + html);
        assertTrue(result.errors().stream().anyMatch(error -> code.equals(error.code())),
                () -> "missing " + code + " in " + result.errors());
    }

    private HtmlCodeResult artifact(String html) {
        HtmlCodeResult result = new HtmlCodeResult();
        result.setHtmlCode(html);
        return result;
    }

    private String completeBody(String content) {
        return "<!doctype html><html><head></head><body>" + content + "</body></html>";
    }
}
