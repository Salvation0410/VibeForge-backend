package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import org.junit.jupiter.api.Test;

import java.util.Set;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.*;

class MultiFileArtifactValidatorTest {
    private final MultiFileArtifactValidator validator = new MultiFileArtifactValidator();

    @Test
    void validArtifactPasses() {
        assertTrue(validator.validate(artifact(validHtml(), "main { color: blue; }", "document.querySelector('main');")).valid());
    }

    @Test
    void invalidContentReturnsStableFileErrors() {
        var result = validator.validate(artifact("<main>x</main><style>x{}</style><script>alert(1)</script>",
                "style.css", "function broken( {"));
        Set<String> codes = result.errors().stream().map(ArtifactValidationError::code).collect(Collectors.toSet());
        assertFalse(result.valid());
        assertTrue(codes.contains("HTML_STRUCTURE_INVALID"));
        assertTrue(codes.contains("HTML_STYLESHEET_MISSING"));
        assertTrue(codes.contains("HTML_SCRIPT_MISSING"));
        assertTrue(codes.contains("HTML_INLINE_STYLE_FORBIDDEN"));
        assertTrue(codes.contains("HTML_INLINE_SCRIPT_FORBIDDEN"));
        assertTrue(codes.contains("CSS_STRUCTURE_INVALID"));
        assertTrue(codes.contains("CSS_CONTENT_INVALID"));
        assertTrue(codes.contains("JS_STRUCTURE_INVALID"));
    }

    @Test
    void scannersIgnoreQuotedDelimitersAndComments() {
        var result = validator.validate(artifact(validHtml(), "a::before { content: \"}\"; } /* { */",
                "const text = '}'; // {\nconsole.log(text);"));
        assertTrue(result.valid(), () -> result.errors().toString());
    }

    private MultiFileCodeResult artifact(String html, String css, String js) {
        var result = new MultiFileCodeResult();
        result.setHtmlCode(html); result.setCssCode(css); result.setJsCode(js);
        return result;
    }

    private String validHtml() {
        return "<!doctype html><html><head><link rel=\"stylesheet\" href=\"style.css\"></head>"
                + "<body><main>x</main><script src=\"script.js\"></script></body></html>";
    }
}
