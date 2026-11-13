# 同名书籍身份验收实施计划

**Goal:** 通过独立探针识别同名书籍在筛选删除时的误删及计数错误。

**Architecture:** 新固定同源浏览器探针使用书名/状态唯一定位，独立手写夹具验证其失败能力；原探针不变。

**Tech Stack:** Node test、HTML手写夹具、Chrome、现有Spring静态预览。

- [x] 新增 scripts/acceptance_reading_duplicate_checks.mjs，记录固定步骤、计数及白名单错误，不收集源码或Cookie。
- [x] 新增 scripts/tests/reading-duplicate-browser.html 与 reading-duplicate-browser.test.mjs；接受ID合法删除并拒绝五种故障。
- [x] 执行 `node --test scripts/tests/reading-duplicate-browser.test.mjs scripts/tests/reading-delete-browser.test.mjs scripts/tests/reading-list-browser.test.mjs`。
- [x] 仅启动隔离Spring和Redis，读取第36节应用实际预览；1440/390px执行新探针，零模型，失败保留。
- [x] 同步交接结果、关闭本轮进程，检查账本/既有修改、git diff --check，精确提交中文信息，不推送。
