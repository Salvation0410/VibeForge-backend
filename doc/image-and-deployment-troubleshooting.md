# 图片生成与部署访问

## 图片检索

LangGraph 首次生成 Vue 项目或用户明确要求新增、补充、替换、搜索或修复图片时，编排器通过 Spring 内部网关调用 `image_search`，将最多六张真实图片的 URL 与说明放入模型上下文 `imageAssets`。常见中文主题转换为简短英文查询；不支持的主题保留用户查询前 160 字符。

Spring 使用 `pexels.api-key` 调用 `GET https://api.pexels.com/v1/search`，携带 Authorization 请求头，查询参数为 query、per_page=12、page=1，超时 20 秒。返回 photos 的 large URL（没有时回退 medium）与 alt 说明。这里只把 URL 写入源码，不下载图片二进制，浏览器需能访问 `images.pexels.com`。

模型必须把检索到的地址写入页面。编排器检查成功的 file_write/content 或 file_modify/newContent 是否包含图片 URL；仅口头宣布完成不能通过，最多补充两轮后失败。此检查证明地址已写入源码，不能代替浏览器图片加载与布局验收。修复阶段继承已写入标记，避免重复插图。搜图失败时显示提示并降级生成，日志记录上游 HTTP 状态码，不能将空结果描述成搜图成功。主动检索不占模型文件循环预算，模型主动调用仍占预算。现有项目的普通功能修改不会主动插图。优化提示中的“保留图片”“不要删除或替换原有图片”和单纯调整图片布局不触发搜索，也不要求重新写入原有 URL；不能仅凭出现图片关键词判断配图需求。中英文动作与否定按分句识别，混合请求中“不要替换旧图，但请添加新照片”仍会触发配图。

## 部署 URL

默认部署链接为 `http://127.0.0.1:8123/api/deployed/<deployKey>/`，Spring 从 `tmp/code_deploy` 提供产物，独立于预览/源码目录，无需 Nginx。入口缺少末尾斜线时重定向；资源使用 no-store，路径穿越与逃出应用目录的符号链接被拒绝。Vue 仍应使用 Vite `base: './'` 和 hash 路由。

可通过 Spring 配置 `app.deploy-base-url`（环境变量 `APP_DEPLOY_BASE_URL`）设置实际对外域名与前缀。例如继续使用本地 Nginx 时设置为 `http://127.0.0.1`，并将 Nginx root 指向该实例的 `tmp/code_deploy`。生产环境必须设置可由用户访问的正式地址；默认回环地址仅用于本机开发。

本次实际排查中，Nginx 在 IPv4 的 80 端口，Docker/WSL 同时监听 IPv6 的 80 端口。`localhost` 在浏览器中走 IPv6，展示了另一服务的 404；`127.0.0.1/kv3piY/` 则正常显示恋爱日记页面。启动 Nginx 并不能消除另一个地址族的监听。不要为修复此问题关闭无关服务，使用明确地址/端口或正确配置反向代理。

更新后重启 Spring 与 Python，重新部署获取新链接。已有链接可将 localhost 改成 127.0.0.1；已有缺图项目需请求“保留功能，补充首页和相册的真实图片”触发新生成，再部署。
