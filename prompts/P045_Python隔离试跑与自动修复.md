# P045 Python 隔离试跑与自动修复

- 日期 2026-11-12
- 工具 Codex，项目现有 DeepSeek 客户端和本机 Wasmtime

## 实际请求

> 好，那么先将python设置好自动调试，要求就按上面的来。1.确定安全性，2.隔离环境调试。请你开始编写代码吧

## 变更

- 本机 Windows 家庭版没有可用的 Windows Sandbox，选用 Wasmtime 50.0.1 和 CPython 3.13.17 WASI。安装脚本校验 SHA-256，隔离进程只接受预定运行时。
- 工作视频生成 Python 源码后试跑。WASI 只读访问运行库和本轮代码及授权文件。每轮使用独立临时目录，启用执行时限、内存及输出上限。沙箱初始化失败就停止试跑。
- 页面支持可选标准输入、精确输出对比及一个用户主动选取的 UTF-8 TXT/CSV/JSON。验收数据仅在本机使用。无验收数据时，普通 Python 异常可触发一次 DeepSeek 修复及独立复跑，修复成功后另存源码。
- 变更涉及后端聊天路由和设置、local_ide、python_sandbox、python_wasi_worker、运行时安装脚本及依赖锁定文件。前端更新媒体提交和消息回显，后端与前端测试覆盖对应入口。

## 验证

- 后端 230 项和前端 77 项通过，前端生产构建通过。
- 实测普通输入输出、指定文件只读、输出比对、宿主路径和环境变量拦截、联网及子进程限制、死循环、超量输出、内存限额及临时目录清理。
- 用合成视频、模拟 DeepSeek 和真实 WASI 完成接口联调，验证样例通过、修复版另存、测试数据未进入模型请求、运行目录清理。未产生真实 API 用量。
- 沙箱首版面向 Python 标准库控制台程序。未提供预期输出时，运行成功仅说明该次执行完成。C/C++ 自动隔离运行留待后续。

## 组件来源

- [Wasmtime 安全文档](https://docs.wasmtime.dev/security.html)及 [Wasmtime 50.0.1](https://pypi.org/project/wasmtime/50.0.1/)
- [CPython WASI 构建说明](https://github.com/python/cpython/blob/main/Platforms/WASI/README.md)及 [Brett Cannon 的第三方运行时构建](https://github.com/brettcannon/cpython-wasi-build/releases/tag/v3.13.17)
