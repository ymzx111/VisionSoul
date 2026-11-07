# P004：DeepSeek 图片理解接口开发记录

## 1. 基本信息

- 日期：2026-11-07（Asia/Shanghai）
- AI 工具：Codex 桌面应用；执行模型：gpt-6.1-sol
- 起点提交：124156a，Add validated image uploads and preview
- 本阶段目标：将已上传图片和用户问题交给 DeepSeek，返回单次视觉理解回复。
- 交付状态：接口、配置模板、22 项自动验证及一次真实 API 联调均完成；等待保存本阶段 Git 提交。

## 2. 用户实际指令与反馈（原文）

此前确认的项目继续执行指令：

> 可以，你接着刚才的任务做吧，给我提示

已确认的 API 模型选择：

> 服务器用deepseekV4.1flash10.17吧，省点钱，刚好我用的习惯

本轮用户验收上一模块：

> 服务能成功，导入图片显示图片都正常，终端命令已执行

本轮询问是否已创建 API Key 后，用户回答：

> 已经创建，稍后填入本机配置

用户填写配置后回复：

> 已填好

认证失败后询问密钥来源，用户确认：

> DeepSeek 官方开放平台

用户更正配置后要求继续执行：

> 我发现我刚才导入的是个id，我的问题，我现在创建一个新的导入进去了，你执行一下任务

继承的项目要求：保留已有项目与历史；模块范围清晰；每阶段保留实际指令、工具/模型、文件清单、验证及结果；密钥不进入 Git；先完成文字和图片 MVP。

## 3. 前置核实

- 用户已手动验收上传与预览。
- Git 实际提交为 124156a，开始本阶段时工作区干净。
- 开始本阶段时 backend/.env 不存在，进程环境也没有配置 DEEPSEEK_API_KEY。
- 只检查密钥是否存在，不输出密钥值。
- 当前官方模型名为 deepseek-flash；依据官方视觉、Chat Completions 和 Thinking Mode 文档实施。

## 4. 实现范围和成本控制

1. POST /api/vision/analyze 接收 image_id 和 question，只读取本项目已保存图片。
2. 图片通过 Base64 作为用户消息的一部分发给 DeepSeek 官方地址，不接受客户端传入 API 地址、磁盘路径或 API Key。
3. GET /api/vision/status 只报告非敏感配置；configured 仅表示已填密钥，不表示认证和余额已验证。
4. 默认使用 deepseek-flash、图片低精度、关闭思考模式、输出上限 512 token，无自动重试。
5. 有限的网络连接和读写等待时间，处理认证、余额、限流、超时、网络、无效结果及空回复。
6. 返回回复、模型名、可选 token 用量和输出截断标志；不返回内部推理内容或上游错误原文。
7. 使用 SecretStr 保存后端密钥配置；.env 路径固定到 backend；示例文件密钥为空。
8. 本轮尚不增加人格系统、会话记忆、前端或视频语音功能。

## 5. 修改文件

- `.gitignore`：允许只包含空密钥的 backend/.env.example，继续忽略真实 .env。
- `README.md`：配置方式、接口调用、成本控制与错误说明。
- `backend/requirements.txt`：新增 httpx==0.28.1。
- `backend/.env.example`：无真实密钥的配置模板。
- `backend/.env`：首次创建的本机空密钥配置，已被 Git 忽略；不作为提交文件。
- `backend/app/core/config.py`：SecretStr 密钥、模型、图片精度、输出与超时设置；固定 .env 路径。
- `backend/app/main.py`：注册图片理解路由并更新阶段说明。
- `backend/app/api/routes/vision.py`：请求校验、配置状态和分析接口。
- `backend/app/services/vision.py`：构造多模态请求、调用官方 API、解析和错误映射。
- `tests/test_vision.py`：模拟传输、协议、费用限制、状态和失败分支验证。
- `prompts/P004_DeepSeek图片理解接口.md`：本记录。

## 6. 实际验证

在项目 .venv 安装依赖，`python -m pip check` 返回 No broken requirements found。

项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：22 项验证全部通过，其中原图片模块 11 项，新图片理解模块 11 项。覆盖：

- 正确的官方地址、认证头、图片 Base64、问题、模型和低成本参数；只调用一次。
- 缺少密钥、未知图片、异常已保存文件不发起网络调用。
- 401/403 认证失败、402 余额不足、429 限流、500/400/307 上游异常；原始诊断内容不传给客户端，不重试/跳转。
- 网络超时与连接失败。
- 非 JSON、错误结构、空回复处理。
- 输出截断标志和可选 token 用量。
- 配置状态不暴露密钥、HTTP 正常回复及缺少密钥的说明。
- 图片 ID、问题长度、空白问题、额外字段校验。

所有新验证使用 MockTransport 和无效测试字符串，不使用用户密钥、不发起真实模型调用。原图片模块继续使用临时目录和独立本地服务器。

## 7. 真实联调结果

首次尝试：

用户填写 backend/.env 后已重启本地后端（127.0.0.1:8765）。配置状态 configured=true，确认进程环境没有覆盖文件中的密钥。
生成一张白色背景、左红色方块、右蓝色圆形的简单图，通过本地上传接口取得标识，再向图片理解接口发起一次真实调用。
本地接口返回 503 和安全错误消息“DeepSeek 密钥认证失败，请检查后端 API Key 配置”，未获得图片描述或 token 用量。
随后只调用不生成模型回复的官方 GET /models 检查认证，确认上游状态为 HTTP 401。
没有继续重试图片分析；自动生成的临时测试图已按其标识删除，未修改用户上传图片。
用户确认密钥来自 DeepSeek 官方开放平台，之后发现此前填写的是 ID，并重新创建、填写了真实密钥。

更正后再次验证（2026-11-07）：

- 已重启后端，使用新密钥。
- 通过本地上传接口上传自动生成的白底几何测试图，再调用 POST /api/vision/analyze。
- 本地接口返回 HTTP 200，模型返回 deepseek-flash。
- 实际回复：“背景是白色。左边是一个红色的正方形，右边是一个蓝色的圆形。”
- 对照生成图，背景、颜色、形状和左右位置全部正确。
- 用量：prompt_tokens=303，completion_tokens=14，total_tokens=317。
- truncated=false；未自动重试。
- 生成的临时图片已清理；用户上传图片保留。
- 验证过程中没有输出、记录或提交真实密钥；backend/.env 确认被 Git 忽略。

本阶段的单张图片理解链路已通过实际验证；此结果不代表复杂场景或细节识别均已评估。

## 8. Git 提交与下一步

实际验证后，代码及记录可以统一提交。已知本聊天的 Windows 权限仍限制 .git 写入，由用户在 PyCharm 终端执行：

```powershell
Set-Location G:\VisionSoul
git add -- .gitignore README.md backend/requirements.txt backend/.env.example backend/app/core/config.py backend/app/main.py backend/app/api/routes/vision.py backend/app/services/vision.py tests/test_vision.py prompts/P004_DeepSeek图片理解接口.md
git commit -m "Integrate DeepSeek image understanding"
```

后续先由用户在文档页上传一张实际照片，结合其 image_id 和问题验证回复；再进入固定人格、连续对话、前端和基础记忆模块。

参考：

- https://api-docs.deepseek.com/api/create-chat-completion/
- https://api-docs.deepseek.com/guides/vision/
- https://api-docs.deepseek.com/guides/thinking_mode/
- https://www.python-httpx.org/advanced/timeouts/
