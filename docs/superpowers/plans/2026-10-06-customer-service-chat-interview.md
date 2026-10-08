# 智能客服聊天界面与面试材料 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将智能客服改为用户已批准的微信式单会话，并交付基于真实源码的 2027 届秋招 RAG 讲解。

**Architecture:** 保留单问题 REST 请求和既有取消、防迟到响应控制器；页面维护临时消息数组。聊天历史不发送给模型，不新增服务端记忆。资料与工程能力以源码为准。

**Tech Stack:** Vue 3、TypeScript、Ant Design Vue；Markdown、Mermaid。

### Task 1: 聊天页面

文件：独立前端 `src/views/customer-service/CustomerServiceView.vue`。

- [ ] 改为左右气泡、消息滚动区、固定输入区，复用现有请求控制器。
- [ ] 实现取消、重试、复制、清空和折叠来源；处理中允许编辑草稿但禁止重复发送。
- [ ] 验证输入法 Enter、移动端长文本、滚动、错误和取消状态。
- [ ] 执行客服控制器测试、类型检查、构建及浏览器检查。

### Task 2: 面试材料

文件：桌面 `客服RAG秋招面试讲解-2027届.md`。

- [ ] 核实 ETL、模型配置、检索重排、引用校验和版本租约实现。
- [ ] 写入两条调用链、模型参数、面试口述、追问、评测方法和局限。
- [ ] 检查事实与源码对应，不将用户报告的评测通过写成实测指标。

### 交付边界

不启动 Python 服务，由用户在 PyCharm 中控制。保留两个仓库已有修改；无需数据库变更。前端服务已在 5173 运行，优先复用。无法完成的真实模型或浏览器验收明确说明。
