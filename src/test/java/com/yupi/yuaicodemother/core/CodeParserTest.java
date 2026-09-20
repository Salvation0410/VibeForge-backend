package com.yupi.yuaicodemother.core;

import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class CodeParserTest {
    private final MultiFileCodeParser parser = new MultiFileCodeParser();

    private static final String COMPLETE = """
            index.html
            ```html
            <!doctype html><html><head><link rel="stylesheet" href="style.css"></head>
            <body><main>Hello</main><script src="script.js"></script></body></html>
            ```

            style.css
            ```css
            main { color: blue; }
            ```

            script.js
            ```javascript
            document.querySelector('main').textContent = 'Ready';
            ```
            """;

    @Test
    void completeResponseParsesAllFilesDespiteHtmlReferences() {
        var result = parser.parseCode(COMPLETE);
        assertTrue(result.getHtmlCode().contains("style.css"));
        assertEquals("main { color: blue; }", result.getCssCode());
        assertTrue(result.getJsCode().startsWith("document.querySelector"));
    }

    @Test
    void truncatedCssNeverBecomesFilenameContent() {
        String response = COMPLETE.substring(0, COMPLETE.indexOf("main { color: blue; }")) + "main { border: 2px";
        assertThrows(ArtifactValidationException.class, () -> parser.parseCode(response));
    }

    @Test
    void truncatedJavascriptIsRejected() {
        String response = COMPLETE.substring(0, COMPLETE.lastIndexOf("```"));
        assertThrows(ArtifactValidationException.class, () -> parser.parseCode(response));
    }

    @Test
    void duplicateOrExplanatorySectionsAreRejected() {
        assertThrows(ArtifactValidationException.class, () -> parser.parseCode(COMPLETE + "\nstyle.css\n```css\na{}\n```"));
        assertThrows(ArtifactValidationException.class, () -> parser.parseCode("Here is the site\n" + COMPLETE));
    }

    @Test
    void blankSectionIsRejected() {
        assertThrows(ArtifactValidationException.class, () -> parser.parseCode(COMPLETE.replace("main { color: blue; }", "")));
    }
}
