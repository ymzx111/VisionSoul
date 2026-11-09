# 公开素材来源、署名与许可

这些视频由第三方拍摄，VisionSoul只进行了截取、转码和明确标注的受控处理；不得称为本项目拍摄或自有训练模型的成果。`sources.json`、两个manifest记录实际下载地址及SHA256，原始WebM保存在本机raw目录。

| 原素材ID | 作者署名 | 原作/许可页面 | 用途 |
|---|---|---|---|
| trees_ready_street | Ready Street | [Video of trees](https://commons.wikimedia.org/wiki/File:Video_of_trees.webm)；[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 调试：树木与街景 |
| lake_closeup_editor | Editor. | [Close up of lake](https://commons.wikimedia.org/wiki/File:Close_up_of_lake.webm)；[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | 调试：水面纹理 |
| tao_mandarin | Wikitongues；Teddy Nee | [Tao speaking Yunnan Mandarin](https://commons.wikimedia.org/wiki/File:WIKITONGUES-_Tao_speaking_Yunnan_Mandarin_with_Thai_and_Taiwanese_accent.webm)；[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 调试：中文方言讲话 |
| tree_harvester | Wikideas1 | [Tree harvester](https://commons.wikimedia.org/wiki/File:Tree_harvester.webm)；[CC0](https://creativecommons.org/publicdomain/zero/1.0/) | 测试：机械与树木，无音轨 |
| skaha_lake | Extemporalist | [Lake Skaha on a Calm Winter Afternoon](https://commons.wikimedia.org/wiki/File:Lake_Skaha_on_a_Calm_Winter_Afternoon.webm)；[CC0](https://creativecommons.org/publicdomain/zero/1.0/) | 测试：湖面与远景 |
| ying_henan | Wikitongues；Ying Li | [Ying speaking Henan Chinese](https://commons.wikimedia.org/wiki/File:WIKITONGUES-_Ying_speaking_Henan_Chinese.webm)；[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 测试：另一位中文讲话者 |

原作许可按来源页记录，核对日期2026-11-10。保留作者、来源和许可链接，不暗示作者认可本项目。原作中的人物不因此可被用于身份认证、代言或用户经历推断。

所有基础MP4截取原视频前12或20秒，最长边1280、不放大，统一25fps H.264；有音轨时转AAC，按视频起点保留音轨时间。原片未因重编码变成VisionSoul自有拍摄。

受控变体由VisionSoul项目制作：延迟版将音轨后移1秒（dev）或2秒（test），超出片段末端的声音截去；静音版将音轨置零；模糊版只在manifest标明区间作高斯模糊；mismatch版将湖面画面与同组另一原视频的讲话结合。这些变体不能当作自然实拍录音。

变体的新增处理以CC BY-SA 4.0提供；父素材自身的署名与许可继续有效，CC0原素材的权利状态不被改变。混合素材的所有父source_ids在manifest中保留，分享变体时须随附此文件及相应许可信息。
