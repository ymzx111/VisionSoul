"""使用两段原始静音视频，离线回归读题交付链路。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

from app.api.routes.chat import get_chat_service
from app.core.config import Settings
from app.main import app
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient
from app.services.local_ide import split_model_cases
from app.services.source_files import FILE_DECLARATION
from app.services.videos import VideoService


FIXTURES = {
    "3-3": "9444f4fef2c8a9217e1ed71a9e1228df2f410fdbcd69031131bc77a37f348177",
    "5-3": "e88c42f5edc147d742b6a17635beffdd0a16f86ebb03a1a70be1149989ea53e3",
}
CASES = [
    {"name": "直角三角形", "stdin": "3 4 5\n", "expected": "area=6.000000"},
    {"name": "交换边顺序", "stdin": "5 3 4\n", "expected": "area=6.000000"},
    {"name": "等腰三角形", "stdin": "5 5 6\n", "expected": "area=12.000000"},
]
SOURCE = """//实验3-3 求三角形面积（有3行命令需要填写）
#include<stdio.h>
#include<math.h>     // (1) 包含求平方根函数sqrt的头文件
int main()
{
    double a,b,c,s,area;
    scanf("%lf%lf%lf",&a,&b,&c); // (2) 输入双精度浮点型变量的值要用格式声明"%lf"
    s=(a+b+c)/2.0;
    area=sqrt(s*(s-a)*(s-b)*(s-c)); // (3) 按公式求三角形面积area
    printf("area=%lf\\n",area);
    return 0;
}
"""
FENCE = chr(96) * 3
DELIVERY = f"[文件名: 3-3.c]\n{FENCE}c\n{SOURCE}{FENCE}\n\n{FENCE}visionsoul-cases\n"
MATRIX_REPLY = "画面有先行后列、累计个数和制表符分隔的代码骨架，但未显示目标矩阵。请补充矩阵图或说明各行各列的数值规则。"


def verify(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(directory: Path):
    captures = {}
    for name, digest in FIXTURES.items():
        matches = list(directory.glob(f"{name}.c - Notepad*.mp4"))
        verify(len(matches) == 1, f"{name}：未找到唯一对应的原始录像")
        path = matches[0]
        with path.open("rb") as file:
            verify(sha256(file.read()).hexdigest() == digest, f"{name}：录像指纹不匹配")
        captures[name] = path

    with TemporaryDirectory(prefix="visionsoul-media-regression-") as folder:
        root = Path(folder)
        settings = Settings(_env_file=None, deepseek_api_key="offline-regression",
                            video_storage_dir=root / "videos", chat_database_path=root / "chat.sqlite3",
                            local_source_workspace=root / "workspace", audio_enabled=True)
        calls = []

        def respond(request):
            body = json.loads(request.content)
            parts = body["messages"][-1]["content"]
            verify(any(isinstance(item, dict) and item.get("type") == "image_url" for item in parts),
                   "视频分析请求没有携带抽帧")
            calls.append(body)
            report = {"summary": "屏幕中的 C 语言填空题", "observations": []}
            if len(calls) == 1:
                report["reply"] = "模拟遗漏 observations，验证一次格式重试。"
                report.pop("observations")
            elif len(calls) == 2:
                report["reply"] = DELIVERY + json.dumps(CASES, ensure_ascii=False) + "\n" + FENCE
            elif len(calls) == 3:
                report["reply"] = MATRIX_REPLY
            else:
                raise AssertionError("检测到多余的模型调用")
            return httpx.Response(200, json={"model": "offline-regression", "choices": [
                {"message": {"content": json.dumps(report, ensure_ascii=False)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}})

        model = DeepSeekClient(settings, httpx.MockTransport(respond))
        service = ChatService(settings, model)
        videos = VideoService(settings, model)
        base = "http://127.0.0.1:8765"

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        with patch.dict(app.dependency_overrides, {get_chat_service: lambda: service}), \
                patch("app.services.local_ide.launch_ide"), \
                TestClient(local_app, base_url=base) as client, \
                patch("app.services.c_sandbox.subprocess.run", wraps=subprocess.run) as processes:
            for name, path in captures.items():
                with path.open("rb") as file:
                    video = videos.upload(file)
                verify(video.audio.status == "no_speech", f"{name}：静音检测异常 {video.audio.status}")
                posted = client.post("/api/chat/messages", headers={"Origin": base}, json={
                    "request_id": uuid4().hex, "space": "work", "message": "",
                    "open_in_ide": True, "video_id": video.video_id,
                })
                verify(posted.is_success, f"{name}：请求失败 {posted.status_code} {posted.text[:300]}")
                turn = posted.json()
                source = turn["local_source"]
                reply = turn["assistant_message"]["content"]
                if name == "5-3":
                    verify(source is None and not FILE_DECLARATION.search(reply) and "目标矩阵" in reply,
                           "5-3：缺条件时应追问，且不写入源码")
                    print("5-3：识别为静音，模拟回复保留骨架信息并追问目标矩阵")
                    continue
                verify(source and "python_check" in source, "3-3：未交付源码或试跑")
                check = source["python_check"]
                verify(check and check["status"] == "passed" and check["case_source"] == "model",
                       f"3-3：AI 自测失败 {check}")
                verify(len(check["case_results"]) == 3 and turn["usage"]["total_tokens"] == 20,
                       "3-3：断言数量或格式重试累计用量异常")
                verify(Path(source["path"]).read_text(encoding="utf-8-sig") == SOURCE,
                       "3-3：落盘源码或注释发生变化")
                verify(len(split_model_cases(reply)[1].cases) == 3, "3-3：自测块解析失败")
                compiles = [call for call in processes.call_args_list
                            if Path(call.args[0][0]).name.lower() == "zig.exe"]
                verify(len(compiles) == 1, "3-3：三组用例没有复用单次编译")
                print("3-3：识别为静音；格式重试 1 次，编译 1 次，3 组输出全部通过")
        verify(len(calls) == 3, "模型调用次数异常")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures-dir", type=Path, default=Path.home() / "Videos" / "Captures")
    run(parser.parse_args().captures_dir)
