"""生成可在本机浏览器打开的视频索引，无外部脚本或网络请求。"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "datasets" / "video_eval"
escape = html.escape
sources = {row["id"]: row for row in json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]}
parts = ['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VisionSoul 公开评测视频</title><style>body{font:16px/1.6 system-ui;margin:32px;background:#f5f7f4;color:#243d33}
main{max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}
article{background:white;border:1px solid #d9e3da;border-radius:12px;padding:16px}video{width:100%;height:200px;background:#14231c}
p,small{overflow-wrap:anywhere}a{color:#235d49}small{display:block;color:#63766c}.notice{background:#fff3d9;padding:16px;border-radius:8px}</style>
<main><h1>VisionSoul 公开评测视频</h1><p>全部为已下载的本机MP4。原素材为第三方作品；署名和许可见 <a href="ATTRIBUTION.md">来源清单</a>。
调试集用于发现问题；测试集按原视频隔离，在冻结后评测。受控变体不冒充自然实拍。</p>
<p class="notice">这轮是代码调试与工程评测，未训练模型权重；不是规模化语义准确率测试。视频不因播放而调用AI。</p>''']
for split, title in [("dev", "调试集"), ("test", "独立测试集")]:
    data = json.loads((ROOT / f"{split}_manifest.json").read_text(encoding="utf-8"))
    parts.append(f'<h2>{title} · {len(data["clips"])}个视频</h2><div class="grid">')
    for clip in data["clips"]:
        parents = [sources[key] for key in clip["source_ids"]]
        description = "；".join(clip.get("expected_visual", []))
        if clip.get("audio_delay"):
            description += f'；音轨人为延后{clip["audio_delay"]}秒'
        if clip.get("blur_windows"):
            description += '；部分时间段人为模糊'
        if clip.get("silent"):
            description += '；音轨人为静音'
        if "mismatch" in clip["id"]:
            description += '；替换为另一段讲话，内容故意不匹配'
        credits = "；".join(f'<a href="{escape(row["page"])}">{escape(row["author"])}</a>' for row in parents)
        parts.append(f'<article><h3>{escape(clip["id"])}</h3><video controls preload="none" src="{escape(clip["file"])}"></video>'
            f'<p>{escape(description)}</p><a href="{escape(clip["file"])}">打开视频</a><small>{clip["seconds"]}秒 · {escape(clip["variant"])} · {escape(clip["license"])}</small>'
            f'<small>来源：{credits}</small></article>')
    parts.append('</div>')
parts.append('</main></html>')
(ROOT / "视频索引.html").write_text("\n".join(parts), encoding="utf-8")
print("视频索引已生成。")
