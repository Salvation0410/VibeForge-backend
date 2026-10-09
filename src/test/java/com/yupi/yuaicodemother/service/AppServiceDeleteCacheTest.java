package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.mapper.AppMapper;
import com.yupi.yuaicodemother.service.impl.AppServiceImpl;
import org.junit.jupiter.api.Test;
import org.springframework.aop.framework.ProxyFactory;
import org.springframework.cache.Cache;
import org.springframework.cache.annotation.AnnotationCacheOperationSource;
import org.springframework.cache.concurrent.ConcurrentMapCacheManager;
import org.springframework.cache.interceptor.CacheInterceptor;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

/** 使用真实 Spring 缓存拦截器验证删除行为，不依赖外部数据库或 Redis。 */
class AppServiceDeleteCacheTest {
    private final AppMapper mapper = mock(AppMapper.class);
    private final ConcurrentMapCacheManager cacheManager = new ConcurrentMapCacheManager("good_app_page");

    @Test
    void successfulDeletionClearsAllFeaturedPages() {
        Cache cache = cacheManager.getCache("good_app_page");
        cache.put("page-1", "包含应用的首页");
        cache.put("search-page-2", "包含应用的搜索分页");
        when(mapper.deleteById(101L)).thenReturn(1);

        assertTrue(proxiedService().removeById(101L));

        assertNull(cache.get("page-1"));
        assertNull(cache.get("search-page-2"));
        verify(mapper).deleteById(101L);
    }

    @Test
    void unsuccessfulDeletionKeepsFeaturedPages() {
        Cache cache = cacheManager.getCache("good_app_page");
        cache.put("page-1", "原列表");

        assertFalse(proxiedService().removeById(101L));

        assertEquals("原列表", cache.get("page-1", String.class));
    }

    private AppService proxiedService() {
        // 只装配删除路径需要的依赖，不启动生成工作流或外部服务。
        AppServiceImpl target = new AppServiceImpl(null, null, mock(ChatHistoryService.class), null, null, null);
        ReflectionTestUtils.setField(target, "mapper", mapper);
        ReflectionTestUtils.setField(target, "chatHistoryOriginalService", mock(ChatHistoryOriginalService.class));
        CacheInterceptor interceptor = new CacheInterceptor();
        interceptor.setCacheManager(cacheManager);
        interceptor.setCacheOperationSources(new AnnotationCacheOperationSource());
        interceptor.afterPropertiesSet();
        interceptor.afterSingletonsInstantiated();
        ProxyFactory factory = new ProxyFactory(target);
        factory.setProxyTargetClass(true);
        factory.addAdvice(interceptor);
        return (AppService) factory.getProxy();
    }
}
