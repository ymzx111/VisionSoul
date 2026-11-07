# VisionSoul

## 1. 项目简介

VisionSoul 是一个具有视觉感知与动态人格模拟能力的多模态 AI 交互系统。

系统允许用户通过文字和图片与 AI 角色进行交流。AI 能够理解用户上传的图片内容，并结合预设人格生成自然、连贯的对话。

## 2. 核心功能

- 图片上传与展示
- 多模态视觉理解
- 人格化自然对话
- 模拟情绪表达
- 用户经历与偏好记忆

## 3. 技术架构

- 前端：React + Tailwind CSS
- 后端：Python + FastAPI
- 数据库：SQLite
- AI 能力：多模态大模型 API
- 版本管理：Git

## 4. 项目目录

- frontend：前端代码
- backend：后端代码
- database：数据库
- prompts：AI 提示词记录
- docs：项目技术文档
- tests：测试文件

## 5. 开发目标

构建一个能够理解用户视觉分享、保持稳定人格并具备基础记忆能力的 AI 交互应用。

本项目采用迭代开发方式，持续记录重要 Prompt、代码修改和 Git 提交历史。

## 6. 当前进度与启动

已完成后端基础接口，以及图片上传、内容校验、安全保存、预览和 DeepSeek 图片理解接口。
图片理解已通过模拟传输验证和一次真实 DeepSeek API 调用。前端、人格对话和 SQLite 记忆尚未实现。

在项目根目录的 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000/docs，展开 `POST /api/images`，点击 **试用接口**，选择图片后点击 **发送请求**。
成功时返回 HTTP 201，包含 `image_id`、`url`、实际保存大小和宽高。把返回的 `url` 接在服务地址后，即可预览图片。

例如返回 `/api/images/某个图片标识`，则访问 `http://127.0.0.1:8000/api/images/某个图片标识`。
终端按 `Ctrl+C` 停止服务。

## 7. 图片模块约定

- 上传接口：`POST /api/images`，表单文件字段名为 `file`。
- 预览接口：`GET /api/images/{image_id}`。
- 支持 JPG、PNG、WebP 静态图片；默认原文件及处理后文件均不超过 10 MiB（10,485,760 字节）。
- 默认单边不超过 8192 像素、总像素不超过 2000 万；不支持动图和 GIF。
- 根据实际内容解码校验，不信任文件后缀或浏览器声明的类型。有效图片即使文件名错误也可以上传。
- 自动纠正 EXIF 方向并重新编码，移除 EXIF、位置等元数据；JPEG/WebP 重新编码可能轻微改变画质，不保留原始文件。
- 图片使用随机标识保存到 `backend/storage/images/`，不采用用户文件名，不进入 Git。
- 完整请求体上限为图片大小上限加 64 KiB，在解析表单前检查，包括分块传输请求。
- 返回值中的 `size_bytes`、`width`、`height` 描述处理后图片，不包含本地磁盘路径。

可在 `backend/.env` 中覆盖 `IMAGE_STORAGE_DIR`、`UPLOAD_MAX_BYTES`、`IMAGE_MAX_PIXELS`、`IMAGE_MAX_DIMENSION`。
默认存储目录固定在 backend 下，不随启动目录改变。若覆盖存储目录，请使用绝对路径。

错误码：空文件/无效图片/损坏图片/动图为 400；大小或像素超限为 413；不支持的实际图片格式为 415；缺少文件字段为 422；图片不存在为 404。
存储写入失败为 500，客户端不会收到服务器的磁盘路径。

当前用于本机开发，尚未实现账号和图片访问权限；预览链接凭随机标识访问。对外部署前需补充用户隔离与访问控制。

## 8. 自动验证

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

验证会启动独立的临时后端，使用临时图片目录，结束时停止进程并清理数据，不依赖真实 API Key。

## 9. DeepSeek 图片理解

用户已选定 DeepSeek V4.1 Flash（10 月 17 日更新版）。官方当前 API 模型名为 `deepseek-flash`，这个名称会随服务商更新，不代表永久锁定 1017 权重。
后端根据图片标识读取已上传图片，结合用户文字调用官方 `https://api.deepseek.com/chat/completions`。
本阶段只做单次图片理解，之后再逐步加入固定人格与基础记忆。

本机 `backend/.env` 已创建为空密钥模板；如果在其他机器使用，可以复制 `backend/.env.example` 为 `backend/.env`。
用编辑器打开 `.env`，在 `DEEPSEEK_API_KEY=` 后填入真实密钥并保存，然后重启后端。
配置路径固定在 backend 下；无论在项目根目录还是 backend 目录启动，均读取同一个文件。
`.env` 被 Git 忽略，示例文件只有空密钥，可以进入 Git。

- `GET /api/vision/status`：只检查本机是否填写密钥，不调用 DeepSeek、不验证密钥或余额，返回内容不包含密钥。
- `POST /api/vision/analyze`：读取已上传图片并调用 DeepSeek，发送图片及问题，产生账户 API 用量。

在 `/docs` 中先上传图片，然后把返回的 `image_id` 填入图片理解接口，例如：

```json
{
  "image_id": "替换为上传成功返回的32位标识",
  "question": "这张照片里有什么？请描述主要场景。"
}
```

成功返回 `answer`、服务商返回的 `model`、`usage`（服务商提供的 token 计数，可能为空）和 `truncated`。
`truncated=true` 表示回复达到输出上限被截断。

默认配置以控制开销为目标：图片 `detail=low`、关闭思考模式、输出最多 512 token，单次调用不自动重试。
low 会降低输入图像细节；辨认小字或细节时可在 `.env` 中将 `DEEPSEEK_IMAGE_DETAIL` 改为 `high`，并重启服务。
`DEEPSEEK_MAX_TOKENS` 可在 32 至 2048 之间调整，`DEEPSEEK_TIMEOUT_SECONDS` 默认为 45 秒。
超时是网络阶段的等待上限，不代表整个请求必定在 45 秒内结束。

图片标识错误/问题为空或超长为 422，图片不存在为 404，密钥未填/认证失败/余额不足/限流为 503，网络或上游结果异常为 502，等待模型超时为 504。
错误消息不返回服务商原始响应或密钥，不会通过客户端指定任意 API 地址或本地文件路径。

已通过 22 项自动验证，并完成一次真实 API 调用：成功识别简单几何测试图，返回 HTTP 200。
详细联调结果与用量记录见 P004。当前是单次图片理解；连续对话、人格和记忆将在后续模块实现。

官方参考：[更新记录](https://api-docs.deepseek.com/updates/)、[视觉输入说明](https://api-docs.deepseek.com/guides/vision/)。

## 10. 中文接口文档

`/docs` 提供中文按钮、栏目、文件选择器、字段名称提示和错误说明；页面顶部有“先上传、再提问”的操作提示。
点击“字段说明”可查看图片编号、模型配置、回复和用量等字段的中文解释。
`/openapi.json`、请求示例和实际响应仍保留程序使用的原始字段名，例如 `image_id`、`question`，便于直接复制调用。

通用界面中文显示由 `backend/app/docs_assets/` 中的本地脚本和样式补充；Swagger 的核心资源仍使用原来的 CDN。
后续若升级 Swagger 的主要版本，需要重新检查动态按钮与文件选择器的中文显示。
