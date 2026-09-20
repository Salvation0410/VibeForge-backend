package com.yupi.yuaicodemother.core;

import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactParser;
import com.yupi.yuaicodemother.core.paser.CodeParserExecutor;
import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.function.Executable;

import static org.junit.jupiter.api.Assertions.*;

class CodeParserTest {
    private final MultiFileCodeParser parser = new MultiFileCodeParser();
    private final HtmlArtifactParser htmlParser = new HtmlArtifactParser();

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

    @Test
    void singleClosedHtmlFenceIsAccepted() {
        assertEquals("<!doctype html><html><head></head><body></body></html>",
                htmlParser.parse("```html\n<!doctype html><html><head></head><body></body></html>\n```")
                        .getHtmlCode());
    }

    @Test
    void pureHtmlWithSupportedOpeningTokenIsAccepted() {
        assertEquals("<!doctype html><html><body></body></html>",
                htmlParser.parse(" \n<!doctype html><html><body></body></html>\t").getHtmlCode());
        assertEquals("<html lang=\"zh-CN\"><body></body></html>",
                htmlParser.parse("\n<html lang=\"zh-CN\"><body></body></html>\n").getHtmlCode());
    }

    @Test
    void malformedOrAmbiguousHtmlResponsesAreRejectedWithStableCode() {
        assertHtmlFormatInvalid("以下是优化后的代码：\n```html\n<html><body><script>${escapeText");
        assertHtmlFormatInvalid("```html\n<html><body></body></html>\n```\n额外说明");
        assertHtmlFormatInvalid("```html\n<html></html>\n```\n```html\n<html></html>\n```");
        assertHtmlFormatInvalid((String) null);
        assertHtmlFormatInvalid("");
        assertHtmlFormatInvalid(" \n\t");
        assertHtmlFormatInvalid("```html\n<html><body></body></html>");
        assertHtmlFormatInvalid("这是无法解析为 HTML 的普通说明文本");
    }

    @Test
    void htmlExecutorUsesStrictParser() {
        var result = CodeParserExecutor.executeParser(
                "```html\n<html><body></body></html>\n```", CodeGenTypeEnum.HTML);
        assertEquals("<html><body></body></html>",
                ((com.yupi.yuaicodemother.ai.model.HtmlCodeResult) result).getHtmlCode());
        assertHtmlFormatInvalid(() -> CodeParserExecutor.executeParser(
                "说明文字\n<html><body></body></html>", CodeGenTypeEnum.HTML));
    }

    private void assertHtmlFormatInvalid(String rawArtifact) {
        assertHtmlFormatInvalid(() -> htmlParser.parse(rawArtifact));
    }

    private void assertHtmlFormatInvalid(Executable executable) {
        var error = assertThrows(ArtifactValidationException.class, executable);
        assertEquals("HTML_FORMAT_INVALID", error.getErrorCode());
        assertEquals("index.html", error.getFile());
    }
}
