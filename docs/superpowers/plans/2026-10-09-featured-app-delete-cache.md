# 精选应用删除缓存修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除应用后清空精选应用分页缓存，使精选列表不再展示已逻辑删除的应用。

**Architecture:** 在 `AppServiceImpl.removeById` 统一删除服务边界增加 Spring Cache 的全量失效注解。用户和管理员 Controller 都调用该方法，因此两条删除路径共享同一缓存一致性行为。

**Tech Stack:** Java 21、Spring Boot Cache、Redis、MyBatis-Flex、Maven。

---

### Task 1: 使应用删除失效精选缓存

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java:332-347`
- Test: `src/test/java/com/yupi/yuaicodemother/service/AppServiceDeleteCacheTest.java`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/pages/HomeView.vue`

- [ ] **Step 1: 在统一删除方法增加缓存失效**

引入 `org.springframework.cache.annotation.CacheEvict`，在 `removeById` 方法上添加 `@CacheEvict(value = "good_app_page", allEntries = true, condition = "#result == true")`，保留现有聊天记录清理和逻辑删除代码。新增 Spring 缓存代理测试，验证成功删除清空多个分页键、删除返回 false 保留缓存。

首页删除成功后改为 `await Promise.all([loadMyApps(), loadGoodApps()])`，同时更新两个列表及总数。

- [ ] **Step 2: 编译并检查差异**

顺序运行 `mvn clean -DskipTests compile` 和 `mvn test '-Dtest=AppServiceDeleteCacheTest,AppServicePublicPageTest'`，预期成功。在前端仓库运行 `npm run type-check`、现有三组 Node 测试及 `npm run build-only`。两仓库运行 `git diff --check`，预期无空白错误。

- [ ] **Step 3: 提交后端修改**

后端只提交设计文档、实施计划、缓存测试和 `AppServiceImpl.java`，提交信息使用 `fix(app): 删除应用同步清理精选缓存`。前端只提交 `HomeView.vue`，提交信息使用 `fix(app): 删除应用后同步刷新精选列表`。
