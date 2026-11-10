# 代码视频排错受控验证

这组素材是本项目生成的屏幕图像组成的视频，包含真实运行固定Python测试程序得到的终端报错；**不是课堂实拍、独立自然视频或盲测数据集**。用于验证“画面读取→排错结构→时间引用→话题保存”的首版链路，不计算普遍识别准确率。

本轮没有训练模型。所有运行数据位于本目录`runtime/`，未使用正式聊天或人物数据库。

## 样例

| ID | 用途 | 预设检查点 |
|---|---|---|
| dev_name | 调试 | `price`与`prices`不一致产生NameError，指出变量名及最小修改 |
| test_type | 冻结后受控检查 | 字符串`"12"`与整数8相加产生TypeError，按用户求和目标修正；混入无关讲话作音频负对照 |
| test_unreadable | 冻结后受控检查 | 全片模糊，说明信息不足并请求清楚画面/文本，不编造补丁 |

三个clip均为16秒H.264 MP4；0—7秒为源码，7秒后为终端。`test_type`的无关音轨从1秒开始，取自先前已下载的第三方样例，不能当成用户朗读代码问题。其自动转写仍有专名误听，不能作为语音真值。

打开[视频索引](视频索引.html)可播放。`media/`保存视频、屏幕图像和固定测试源码，`runtime/`保存隔离音频/截图/数据库，均由Git忽略。manifest记录素材SHA、实际报错、来源与预设目标，报告和API原始回执可提交。

## 音轨来源与准备说明

`test_type`包含Wikitongues作品[Widya 20210426 nan](https://commons.wikimedia.org/wiki/File:Widya_20210426_nan.ogg)的讲话，许可[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)，署名Wikitongues。来自本机`datasets/video_eval_v2/test/widya_nan.mp4`，具体父文件SHA在manifest中；原作及实际官方VP9转码来源见v2准备记录。

本项目将前15秒音轨转单声道、延后1秒，配上自制代码屏幕，构成明确标注的受控混音；此混合作品的新增处理按CC BY-SA 4.0提供，分享时附本说明、原作者及许可链接，不暗示作者认可项目。其他无音轨屏幕样例为本项目测试材料，不冒充第三方或自摄。

曾尝试本机Windows语音合成，系统安全设置不允许使用语音引擎，未绕过权限；实际提交的测试视频没有使用该合成语音，也没有声称有人口述了测试问题。带音频MP4生成时修正了测试工具音轨流创建顺序，原冻结留作`freeze-before-fixture-fix.json`，新的`freeze.json`保留相同生产代码指纹，只更新了评测器；这是素材准备修复，未据冻结后模型输出调参。

## 复现与结果

已安装原项目依赖、本机语音模型，且已准备v2音轨父素材时，从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_code_video.py prepare-dev
.\.venv\Scripts\python.exe -B -X utf8 tools/evaluate_code_video.py prepare-test
```

`dev_name`、`test_type`、`test_unreadable`动作明确调用付费API；已有报告复用，已有回执不自动重试。没有要求模型执行代码；生成器仅执行脚本中固定的测试程序，错误代码/报错本身可审阅。已有冻结不覆盖，代码变更后的新评测应另存版本。

本轮3次真实调用共9916token：NameError与TypeError诊断符合预设，模糊样例返回insufficient。详见[结果报告](reports/评测结果.md)。旧样例回归另存`reports/old_sample_regressions.json`，没有改写v1/v2报告。
