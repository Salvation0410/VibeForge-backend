package com.yupi.yuaicodemother.langraph4j.tools;

import cn.hutool.core.util.StrUtil;
import cn.hutool.http.HttpRequest;
import cn.hutool.http.HttpResponse;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.langraph4j.enums.ImageCategoryEnum;
import com.yupi.yuaicodemother.langraph4j.model.ImageResource;
import dev.langchain4j.agent.tool.P;
import dev.langchain4j.agent.tool.Tool;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 插画搜索工具 使用 undraw 网站
 */
@Slf4j
@Component
public class UndrawIllustrationTool {

    private static final String UNDRAW_API_URL = "https://undraw.co/_next/data/Drjj8o0PMGsZIMoiojT0v/search/%s.json?term=%s";

    @Tool("搜索插画图片，用于网站美化和装饰")
    public List<ImageResource> searchIllustrations(@P("搜索关键词") String query) {
        Set<String> candidates = new LinkedHashSet<>();
        if (StrUtil.isNotBlank(query)) {
            candidates.add(query);
        }
        candidates.add(buildFallbackQuery(query));
        candidates.add("web development");

        for (String candidate : candidates) {
            List<ImageResource> imageList = doSearchIllustrations(candidate);
            log.info("搜索插画完成，query: {}, count: {}", candidate, imageList.size());
            if (!imageList.isEmpty()) {
                return imageList;
            }
        }
        return new ArrayList<>();
    }

    private List<ImageResource> doSearchIllustrations(String query) {
        List<ImageResource> imageList = new ArrayList<>();
        int searchCount = 12;
        String encodedQuery = encodeUrlValue(query);
        String apiUrl = String.format(UNDRAW_API_URL, encodedQuery, encodedQuery);

        // 使用 try-with-resources 自动释放 HTTP 资源
        try (HttpResponse response = HttpRequest.get(apiUrl).timeout(10000).execute()) {
            if (!response.isOk()) {
                return imageList;
            }
            JSONObject result = JSONUtil.parseObj(response.body());
            JSONObject pageProps = result.getJSONObject("pageProps");
            if (pageProps == null) {
                return imageList;
            }
            JSONArray initialResults = pageProps.getJSONArray("initialResults");
            if (initialResults == null || initialResults.isEmpty()) {
                return imageList;
            }
            int actualCount = Math.min(searchCount, initialResults.size());
            for (int i = 0; i < actualCount; i++) {
                JSONObject illustration = initialResults.getJSONObject(i);
                String title = illustration.getStr("title", "插画");
                String media = illustration.getStr("media", "");
                if (StrUtil.isNotBlank(media)) {
                    imageList.add(ImageResource.builder()
                            .category(ImageCategoryEnum.ILLUSTRATION)
                            .description(title)
                            .url(media)
                            .build());
                }
            }
        } catch (Exception e) {
            log.error("搜索插画失败：{}", e.getMessage(), e);
        }
        return imageList;
    }

    private String buildFallbackQuery(String query) {
        if (StrUtil.isBlank(query)) {
            return "illustration";
        }
        if (StrUtil.containsAny(query, "电商", "购物", "商品", "商城")) {
            return "online shopping";
        }
        if (StrUtil.containsAny(query, "技术", "编程", "代码", "博客", "系统", "架构")) {
            return "web development";
        }
        return "illustration";
    }

    private String encodeUrlValue(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }
}
