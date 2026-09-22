package com.yupi.yuaicodemother.ai.gateway;

import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * Spring 内部 AI 工具的标准名称、历史别名和模型可见性。
 */
public enum InternalAiTool {
    DIR_READ("dir_read", true, "readDir", "read_dir"),
    FILE_READ("file_read", true, "readFile", "read_file"),
    FILE_WRITE("file_write", true, "writeFile", "write_file"),
    FILE_MODIFY("file_modify", true, "modifyFile", "modify_file"),
    FILE_DELETE("file_delete", true, "deleteFile", "delete_file"),
    ARTIFACT_CONTEXT("artifact_context", false),
    ARTIFACT_VALIDATE("artifact_validate", false, "artifact_validation"),
    ARTIFACT_PUBLISH("artifact_publish", false),
    PROJECT_BUILD("project_build", false);

    private static final Map<String, InternalAiTool> BY_EXTERNAL_NAME;

    static {
        Map<String, InternalAiTool> tools = new HashMap<>();
        for (InternalAiTool tool : values()) {
            register(tools, tool.canonicalName, tool);
            tool.aliases.forEach(alias -> register(tools, alias, tool));
        }
        BY_EXTERNAL_NAME = Map.copyOf(tools);
    }

    private final String canonicalName;
    private final boolean modelCallable;
    private final Set<String> aliases;

    InternalAiTool(String canonicalName, boolean modelCallable, String... aliases) {
        this.canonicalName = canonicalName;
        this.modelCallable = modelCallable;
        Set<String> names = new LinkedHashSet<>();
        Collections.addAll(names, aliases);
        this.aliases = Set.copyOf(names);
    }

    public String canonicalName() {
        return canonicalName;
    }

    public boolean modelCallable() {
        return modelCallable;
    }

    public Set<String> aliases() {
        return aliases;
    }

    public static InternalAiTool fromExternalName(String name) {
        InternalAiTool tool = BY_EXTERNAL_NAME.get(name);
        if (tool == null) {
            throw new IllegalArgumentException("Unsupported tool: " + name);
        }
        return tool;
    }

    private static void register(Map<String, InternalAiTool> tools, String name, InternalAiTool tool) {
        InternalAiTool previous = tools.putIfAbsent(name, tool);
        if (previous != null) {
            throw new IllegalStateException("Duplicate internal AI tool name: " + name);
        }
    }
}
