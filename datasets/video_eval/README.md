# VisionSoul 公开短视频评测集 v1

本轮为代码调试和评测，未训练/微调模型权重。视频来自公开许可来源；代码按现有1秒采样、最多4张高清图、音画±1.5/±3秒候选规则运行。

## 在哪里看视频

- 用浏览器打开 [视频索引.html](视频索引.html)，可分组播放，不调用AI。
- `dev/`：6个调试视频，可用于发现与修复问题。
- `test/`：7个独立测试视频，仅在冻结代码后运行。
- `raw/`：6份原始WebM；长视频只截取前段作本项目输入。
- `previews/`：带时间和高清入选标记的关键帧预览。
- [ATTRIBUTION.md](ATTRIBUTION.md)：原作者、原作页面、许可及处理说明。
- `sources.json`、`dev_manifest.json`、`test_manifest.json`：来源分组、父视频ID、处理方式和文件校验值。
- `freeze.json`：调试完成后的代码、来源清单和调试清单指纹。
- `reports/`：本机检查、API回执、失败记录和人工可复核的结果说明。

本轮结果先看[评测结果](reports/评测结果.md)：本地工程检查13/13通过，独立API的3例中1例接受、2例拒绝；未通过的样例保留原始回执，不能视为语义准确率。逐例审阅由Codex辅助完成，尚未独立人工复核。

二进制素材和隔离运行目录保存在本机、由Git忽略；来源、脚本、许可、标注和报告可提交。复制测试集给他人时同时附上署名与manifest，不把外部视频当作原创作品。

## 分组原则

树木、水面、讲话共3个原视频进入调试集；机械、另一湖面、另一讲话者共3个原视频进入测试集。同一原视频的全部裁剪、延迟、模糊、静音及混合变体都留在同一组，混合时所有父素材也必须属于同组。

测试集独立于本次代码调试，但基础模型可能已经见过网络上的这些视频，不能承诺预训练数据层面的未见性。7个测试文件只有3个独立原视频，不能当成7个独立拍摄事件，更不能据此汇报普遍识别准确率。

`speech=unknown`的自然素材没有预设“无讲话”真值；自动转写不能反过来成为自己的正确答案。当前没有完整人工中文逐字稿，不能汇报CER/WER。可核对的内容包括文件有效性、采样覆盖、高清名额、受控模糊与延迟、静音状态、候选引用合法性；语义结果需看报告中的逐例复核。

## 复现

在项目根目录、已安装原项目依赖和本机语音模型的环境执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools/prepare_video_eval.py --split dev
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_video_corpus.py --split dev
```

已有freeze.json时不重复冻结。复现测试需要文件与冻结指纹一致：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools/prepare_video_eval.py --split test
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_video_corpus.py --split test
.\.venv\Scripts\python.exe -B -X utf8 tools/build_video_eval_index.py
```

后续改动使冻结检查失败时，应开启新版本/新独立测试，不删除旧freeze掩盖差异。本机检查读取真实视频、运行本地ASR，不调用付费API；重复导入可命中本机缓存，报告用`reused_local_cache`区分，缓存读取时间不当作重新转写耗时。

真实DeepSeek评测必须明确加`--api`，每组最多3例；现有报告直接复用，无报告但已有回执时停止，避免意外重复付费。`--replay-receipts`只重放已存回复验证结构，不调用API，也不代表模型采用了新的提示。实际结果与费用记录见报告。

本轮已修复调试暴露的候选引用和同帧多观察问题，再冻结运行测试；测试结果不用于本轮继续调参。方言、专有名词和转写事实错误仍需真实人工核对。
