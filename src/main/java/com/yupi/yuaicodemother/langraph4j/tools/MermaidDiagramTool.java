package com.yupi.yuaicodemother.langraph4j.tools;

import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.RuntimeUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.system.SystemUtil;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.langraph4j.enums.ImageCategoryEnum;
import com.yupi.yuaicodemother.langraph4j.model.ImageResource;
import com.yupi.yuaicodemother.manager.OssManager;
import dev.langchain4j.agent.tool.P;
import dev.langchain4j.agent.tool.Tool;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;


/**
 * Mermaid 架构图生成工具
 */

@Slf4j
@Component
public class MermaidDiagramTool {

    @Resource
    private OssManager ossManager;

    @Tool("将 Mermaid 代码转换为架构图图片，用于展示系统结构和技术关系")
    public List<ImageResource> generateMermaidDiagram(@P("Mermaid 图表代码") String mermaidCode,
                                                      @P("架构图描述") String description) {
        if (StrUtil.isBlank(mermaidCode)) {
            return new ArrayList<>();
        }
        File diagramFile = null;
        try {
            //转换为svg图片
            diagramFile = convertMermaidToSvg(mermaidCode);
            String keyName = String.format("mermaid/%s/%s",
                    RandomUtil.randomString(5), diagramFile.getName());
            //上传到oss
            String ossUrl = ossManager.uploadFile(keyName, diagramFile);
            if (StrUtil.isNotBlank(ossUrl)) {
                return Collections.singletonList(ImageResource.builder()
                        .category(ImageCategoryEnum.ARCHITECTURE)
                        .description(description)
                        .url(ossUrl)
                        .build());
            }
        } catch (Exception e) {
            log.error("生成架构图失败: {}", e.getMessage(), e);
        } finally {
            //删除临时文件
            if (diagramFile != null) {
                FileUtil.del(diagramFile);
            }
        }
        return new ArrayList<>();
    }

    /**
     * 将 Mermaid 代码转换为 SVG 图片
     */
    private File convertMermaidToSvg(String mermaidCode) {
        //创建临时输入文件
        File tempInputFile = FileUtil.createTempFile("mermaid_input_", ".mmd", true);
        FileUtil.writeUtf8String(mermaidCode, tempInputFile);
        //创建临时输出文件
        File tempOutputFile = FileUtil.createTempFile("mermaid_output_", ".svg", true);
        //根据操作系统选择命令
        String command = SystemUtil.getOsInfo().isWindows() ? "mmdc.cmd" : "mmdc";
        //构建命令
        String cmdLine = String.format("%s -i %s -o %s -b transparent",
                command,
                quotePath(tempInputFile.getAbsolutePath()),
                quotePath(tempOutputFile.getAbsolutePath())
        );
        try {
            //执行命令
            RuntimeUtil.execForStr(cmdLine);
            //检查输出文件
            if (!tempOutputFile.exists() || tempOutputFile.length() == 0) {
                throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Mermaid CLI 执行失败");
            }
            return tempOutputFile;
        } finally {
            //清理输入文件 保存输出文件供上传使用
            FileUtil.del(tempInputFile);
        }
    }

    private String quotePath(String path) {
        return "\"" + path + "\"";
    }
}
