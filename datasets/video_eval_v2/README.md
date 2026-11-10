# 音画协调评测 v2

本轮针对日常视频使用，修复关系条数与显示条数耦合、纯对白附带画面字段，以及相近场景的虚焦帧占高清名额。沿用每1秒采样及最多4张高清；没有更换或训练语音模型。

## 来源与许可

核对日期：2026-11-10。两份原作未出现在v1语料中；Wikitongues发布机构与v1部分素材相同，但讲话者和原视频不同，不宣称机构层面独立，也不保证基础模型预训练未见过。

| 原作 | 作者 | 许可 | 本轮处理 |
|---|---|---|---|
| [River Bank-Closeup](https://commons.wikimedia.org/wiki/File:River_Bank-Closeup.webm) | Chazzie1769 | [CC0](https://creativecommons.org/publicdomain/zero/1.0/) | 前11秒，最长边1280、25fps H.264 MP4；原有音轨转AAC |
| [Widya 20210426 nan](https://commons.wikimedia.org/wiki/File:Widya_20210426_nan.ogg) | Wikitongues | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 前20秒，同样转码；原作说明为Chinese与Min Nan，不冒称标准普通话 |

在同一测试组内另制作3个受控变体：Widya音轨延后2秒（截去片尾溢出声音）；河流画面配Widya讲话（取前11秒，故意错配）；河流0—4秒高斯模糊。新增处理按CC BY-SA 4.0提供，保留父原作许可与署名；CC0原作状态不因此改变。分享变体需同时附本说明及manifest，不能冒充自摄自然录音。

`raw/`与`test/`保存本机原片及5份MP4；[视频索引](视频索引.html)可离线播放；`runtime/`为隔离的音频/截图/缓存。上述二进制与运行目录由Git忽略；来源、manifest、冻结指纹、报告可提交。

Widya原Theora文件包含零字节重复帧包，本机PyAV解码失败。本次实际使用同一原作的[官方720p VP9转码](https://upload.wikimedia.org/wikipedia/commons/transcoded/4/48/Widya_20210426_nan.ogg/Widya_20210426_nan.ogg.720p.vp9.webm)制作MP4；原作、实际解码源及输出SHA见[准备记录](preparation_override.json)。这是容器准备调整，没有修改冻结的生产代码，也不以此调节评测结果。

## 独立性与复现

v1旧失败回执和湖面虚焦已经看过，本轮只作为回归。v2来源页面先固定，生产代码和评测脚本冻结后才下载、处理新原片。冻结后只记录结果，不反向调参；v1报告和冻结文件不覆盖。当前代码变更使v1的代码指纹检查不再通过是预期行为，复现v1应回到原版本。

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_video_v2.py regression
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_video_v2.py prepare
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_video_v2.py local
```

已有freeze.json时不要再次执行`freeze`。`api`是明确付费动作，只选择讲话和错配2例，各调用一次；已有报告复用，已有回执不自动重发。离线回归没有新的API用量，不等于新模型推理。

如果从零重新下载，先补上本机Theora解码兼容步骤，再运行`prepare`；已生成的MP4无需重复处理：

```powershell
@'
import sys
sys.path.insert(0, 'tools')
import evaluate_video_v2 as run
from prepare_video_eval import transcode
run.verify()
url = 'https://upload.wikimedia.org/wikipedia/commons/transcoded/4/48/Widya_20210426_nan.ogg/Widya_20210426_nan.ogg.720p.vp9.webm'
source = run.download({'id': 'widya_nan_official_vp9', 'url': url})
target = run.DATA / 'test/widya_nan-official-normalized.mp4'
transcode(source, target, 20)
target.replace(run.DATA / 'test/widya_nan.mp4')
'@ | .\.venv\Scripts\python.exe -B -X utf8 -
```

`test_manifest.json`的`raw_sha256`标识原作下载文件；实际解码的官方转码另在准备记录中标识。跨机器编码结果可能不同，不能覆盖本轮manifest；新环境重建结果需另存为新版本。

工程检查验证采样、引用及受控时间轴，不是语义识别准确率。没有完整人工中文逐字稿，不能计算CER/WER；方言专名和声音内容仍需回听、校对。精确时间、选帧及API结果以`reports/`实际记录为准。

本轮本机检查5/5通过，API两例结构接受；但讲话样例有重复转写和语义支持关系误判，模糊变体仍有1张模糊帧入选。详见[评测结果](reports/评测结果.md)，这些问题没有当成语义通过。
