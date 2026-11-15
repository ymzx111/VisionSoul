"""隔离 SQLite 与模拟 API，检查上下文、人格、原子保存和重复请求。"""
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.api.routes.chat import get_chat_service
from app.core.config import Settings, get_settings
from app.core.persona import PERSONA_PROMPT
from app.main import app
from app.services.chat import ChatService, context_messages
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply
from app.services.images import save_image


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-chat-test-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, deepseek_api_key="test-key-not-valid",
            image_storage_dir=Path(self.temp.name) / "images", chat_database_path=Path(self.temp.name) / "chat.sqlite3")
        self.payloads = []

        def handler(request):
            payload = json.loads(request.content)
            self.payloads.append(payload)
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {"content": f"小灵回复{len(self.payloads)}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}})
        self.service = ChatService(self.settings, DeepSeekClient(self.settings, httpx.MockTransport(handler)))

    def send(self, text="你好", request="1", conversation=None, image=None):
        return self.service.send(conversation_id=conversation, request_id=request * 32, message=text, image_id=image)

    def image(self, color="green"):
        buffer = BytesIO()
        Image.new("RGB", (24, 16), color).save(buffer, "PNG")
        return save_image(buffer.getvalue(), self.settings).image_id

    def test_work_photo_group_preserves_order_and_restores_without_resending(self):
        images = [self.image(color) for color in ("red", "green", "blue", "white", "black")]
        app.dependency_overrides[get_chat_service] = lambda: self.service
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        body = {"request_id": "a" * 32, "space": "work", "message": "", "image_id": images[0], "image_ids": images}
        with TestClient(app) as client:
            too_many = client.post("/api/chat/messages", json={**body, "image_ids": [*images, "f" * 32]})
            self.assertEqual(too_many.status_code, 422)
            self.assertFalse(self.payloads)
            first = client.post("/api/chat/messages", json=body)
            self.assertEqual(first.status_code, 200, first.text)
            result = first.json()
            self.assertEqual(result["image_ids"], images)
            self.assertEqual(result["user_message"]["image_ids"], images)
            parts = self.payloads[0]["messages"][-1]["content"]
            self.assertEqual(sum(part["type"] == "image_url" for part in parts), 5)
            replay = client.post("/api/chat/messages", json=body).json()
            self.assertTrue(replay["replayed"])
            restored = client.get(f'/api/chat/conversations/{result["conversation_id"]}?space=work').json()
            self.assertEqual(restored["image_ids"], images)
            self.assertEqual(restored["messages"][0]["image_ids"], images)
            self.assertEqual(len(self.payloads), 1)

    def test_local_work_photo_saves_python_and_checks_sample_before_opening_editor(self):
        if not find_spec("wasmtime") or not (self.settings.python_sandbox_runtime / "python.wasm").is_file():
            self.skipTest("Python WASI runtime is optional")
        root = Path(self.temp.name)
        self.settings.local_source_workspace = root / "workspace"
        launcher = root / "pycharm64.exe"
        launcher.touch()
        self.settings.local_pycharm = launcher
        picture = self.image()
        code = 'print(int(input()) * 2)\n'
        fence = chr(96) * 3
        source_reply = f"[语言: Python]\n[文件名: main.py]\n{fence}python\n{code}{fence}"

        def handler(request):
            payload = json.loads(request.content)
            self.payloads.append(payload)
            text = payload["messages"][-1]["content"][0]["text"]
            answer = "这是一张绿色图片。" if text == "描述图片" else source_reply
            return httpx.Response(200, json={"model": "test", "choices": [
                {"message": {"content": answer}, "finish_reason": "stop"}]})

        self.service.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        body = {"request_id": "1" * 32, "message": "按照片里的题目写 Python", "space": "work",
                "image_id": picture, "open_in_ide": True, "python_test": {"stdin": "1234", "expected": "2468"}}
        with (TestClient(local_app, base_url=base) as client,
              patch("app.services.local_ide.launch_ide") as launch,
              patch("app.services.local_ide.show_sandbox_result", return_value=True) as viewer):
            response = client.post("/api/chat/messages", json=body, headers={"Origin": base})
            self.assertEqual(response.status_code, 200, response.text)
            local = response.json()["local_source"]
            self.assertEqual(local["status"], "opened")
            self.assertTrue(local["desktop_result_opened"])
            self.assertEqual(viewer.call_args.args[1].stdout.strip(), "2468")
            self.assertEqual(local["python_check"]["status"], "passed")
            saved = Path(local["path"])
            self.assertEqual(saved.parent, root / "workspace" / "Python")
            self.assertEqual(saved.read_text(encoding="utf-8"), code)
            prompt = self.payloads[0]["messages"][0]["content"]
            self.assertIn("[文件名: main.c]", prompt)
            self.assertIn("main.py", prompt)
            self.assertNotIn('"1234"', json.dumps(self.payloads[0]))
            self.assertEqual(self.payloads[0]["messages"][-1]["content"][1]["type"], "image_url")
            self.assertEqual(list((root / "workspace" / ".sandbox-jobs").iterdir()), [])
            for data, origin in [
                (body, base),
                ({**body, "request_id": "2" * 32, "message": "描述图片"}, base),
                ({**body, "request_id": "3" * 32, "space": "companion"}, base),
                ({**body, "request_id": "4" * 32}, "https://example.invalid"),
            ]:
                with self.subTest(request_id=data["request_id"]):
                    response = client.post("/api/chat/messages", json=data, headers={"Origin": origin})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIsNone(response.json()["local_source"])
            launch.assert_called_once_with([str(launcher), str(saved)], saved.parent)
        self.assertEqual(len(self.payloads), 4)

    def test_python_rerun_binds_generated_source_and_keeps_file_permission_single_use(self):
        if not find_spec("wasmtime") or not (self.settings.python_sandbox_runtime / "python.wasm").is_file():
            self.skipTest("Python WASI runtime is optional")
        root = Path(self.temp.name)
        self.settings.local_source_workspace = root / "workspace"
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        fence = chr(96) * 3
        code = """import os
value = input('number? ')
print(int(value) * 2)
if os.path.isfile('sample.txt'):
    with open('sample.txt') as sample:
        print(sample.read())
"""
        reply = f"[文件名: main.py]\n{fence}python\n{code}{fence}"
        self.service.client.complete = lambda *args, **kwargs: ModelReply(answer=reply, model="test")

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        local = {"Origin": base}
        body = {"request_id": "5" * 32, "message": "写 Python", "space": "work",
                "image_id": self.image(), "open_in_ide": True}
        with (TestClient(local_app, base_url=base) as client,
              patch("app.services.local_ide.launch_ide"),
              patch("app.services.local_ide.show_sandbox_result", return_value=True),
              patch("app.api.routes.chat.show_sandbox_result", return_value=True) as viewer):
            original = client.post("/api/chat/messages", json=body, headers=local)
            self.assertEqual(original.status_code, 200, original.text)
            source = original.json()["local_source"]
            self.assertEqual(source["python_check"]["status"], "awaiting_input")
            self.assertEqual(source["python_check"]["stdout"], "")
            self.assertIn("未取得非空自测输入", source["python_check"]["detail"])
            saved = Path(source["path"])
            saved.write_text("raise RuntimeError('edited file')", encoding="utf8")
            retry = {"run_id": source["python_run_id"], "stdin": "21"}
            approved = {"filename": "sample.txt", "content": "LOCAL-ONLY"}
            with patch("app.services.deepseek.DeepSeekClient.complete", side_effect=AssertionError("model called")):
                result = client.post("/api/chat/python-runs", json={**retry, "approved_file": approved}, headers=local)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["stdout"], "number? 42\nLOCAL-ONLY\n")
                result = client.post("/api/chat/python-runs", json=retry, headers=local)
                self.assertEqual(result.json()["stdout"], "number? 42\n")
                self.assertTrue(result.json()["desktop_result_opened"])
                self.assertEqual(viewer.call_args.args[1].stdout, result.json()["stdout"])
                self.assertEqual(viewer.call_count, 2)
                bad = client.post("/api/chat/python-runs", json={**retry, "stdin": "不是数字",
                                  "open_result_window": False}, headers=local).json()
                self.assertEqual((bad["status"], bad["error_line"]), ("failed", 3), bad)
                self.assertIn("int(value)", bad["source_line"])
                self.assertIn("数据转换失败", bad["detail"])
                self.assertEqual(bad["stdin"], "不是数字")
            for extra in ({"code": "print(1)"}, {"path": str(saved)}):
                self.assertEqual(client.post("/api/chat/python-runs", json={**retry, **extra}, headers=local).status_code, 422)
            self.assertEqual(client.post("/api/chat/python-runs", json=retry, headers={"Origin": "https://example.invalid"}).status_code, 403)
            self.assertEqual(client.post("/api/chat/python-runs", json={**retry, "run_id": "0" * 32}, headers=local).status_code, 404)
            self.assertEqual(list((root / "workspace" / ".sandbox-jobs").iterdir()), [])

    def test_file_task_waits_for_permission_and_uses_later_selected_filename(self):
        if not find_spec("wasmtime") or not (self.settings.python_sandbox_runtime / "python.wasm").is_file():
            self.skipTest("Python WASI runtime is optional")
        self.settings.local_source_workspace = Path(self.temp.name) / "workspace"
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        source = (
            "OUT_FILE = 'restock_order.txt'\n"
            "with open('inventory.txt', encoding='utf-8') as data:\n"
            "    rows = [line for line in data if int(line.split()[1]) < 5]\n"
            "import os\n"
            "if os.getenv('VISIONSOUL_SANDBOX') == '1':\n"
            "    print('[VISIONSOUL_OUTPUT_BEGIN]')\n"
            "    print(''.join(rows), end='')\n"
            "    print('[VISIONSOUL_OUTPUT_END]')\n"
            "else:\n"
            "    with open(OUT_FILE, 'w', encoding='utf-8') as output:\n"
            "        output.writelines(rows)\n"
            "print('需要补货的物资列表：')\n"
            "print(''.join(rows), end='')\n"
            "print('低库存：', len(rows))\n"
        )
        fence = chr(96) * 3
        sample = {"sample_file": {"filename": "inventory.txt", "content": "样品 1\n"},
                  "expected": "需要补货的物资列表：\n样品 1\n低库存： 1\n"}
        reply = (f"[需要数据文件]\n[文件名: main.py]\n{fence}python\n{source}{fence}\n"
                 f"{fence}visionsoul-cases\n{json.dumps(sample)}\n{fence}")
        self.service.client.complete = lambda *args, **kwargs: ModelReply(answer=reply, model="test")

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        with (TestClient(local_app, base_url=base, headers={"Origin": base}) as client,
              patch("app.services.local_ide.launch_ide"),
              patch("app.services.local_ide.show_sandbox_result", return_value=True)):
            first = client.post("/api/chat/messages", json={
                "request_id": "6" * 32, "message": "Python题：用本机数据统计库存", "space": "work",
                "open_in_ide": True,
            })
            self.assertEqual(first.status_code, 200, first.text)
            delivery = first.json()["local_source"]
            self.assertEqual(delivery["python_check"]["status"], "awaiting_file")
            self.assertEqual(delivery["python_check"]["case_source"], "model")
            self.assertIn("样例试跑成功", delivery["python_check"]["detail"])
            self.assertEqual(delivery["python_check"]["stdout"], "")
            self.assertFalse((self.settings.local_source_workspace / "outputs").exists())
            rerun = {"run_id": delivery["sandbox_run_id"], "open_result_window": False}
            self.assertEqual(client.post("/api/chat/sandbox-runs", json=rerun).json()["status"], "awaiting_file")
            authorized = {**rerun, "approved_file": {
                "filename": "仓库数据.txt", "content": "杯子 3\n笔 12\n",
            }}
            mismatch = client.post("/api/chat/sandbox-runs", json={**authorized, "expected": "错误答案"}).json()
            self.assertEqual(mismatch["status"], "mismatch", mismatch)
            self.assertFalse((self.settings.local_source_workspace / "outputs").exists())
            expected = "需要补货的物资列表：\n杯子 3\n低库存： 1\n"
            result = client.post("/api/chat/sandbox-runs", json={**authorized, "expected": expected}).json()
            self.assertEqual(result["status"], "passed", result)
            self.assertEqual(result["data_file"], "仓库数据.txt")
            self.assertEqual(result["stdout"], "需要补货的物资列表：\n杯子 3\n低库存： 1\n")
            output = Path(result["output_path"])
            self.assertEqual(output.name, "restock_order.txt")
            self.assertEqual(output.parent.parent, self.settings.local_source_workspace / "outputs")
            self.assertEqual(output.read_text(encoding="utf-8"), "杯子 3\n")
            self.assertEqual(Path(delivery["path"]).read_text(encoding="utf-8"), source)

            with patch.object(self.service.client, "complete", return_value=ModelReply(
                answer=reply.replace(json.dumps(sample), json.dumps({**sample, "expected": "错"})),
                model="test",
            )):
                failure = client.post("/api/chat/messages", json={
                    "request_id": "7" * 32, "message": "Python题：用本机数据统计库存",
                    "space": "work", "open_in_ide": True, "python_test": {"approved_file": authorized["approved_file"]},
                }).json()["local_source"]["python_check"]
            self.assertEqual((failure["status"], failure["case_source"]), ("mismatch", "model"))
            self.assertIn("样品", failure["sample_file"]["content"])
            self.assertEqual(failure["expected"], "错")
            self.assertIn("低库存： 1", failure["stdout"])
            self.assertIsNone(failure["error_line"])
            self.assertIsNone(failure["data_file"])
            self.assertIsNone(failure["output_path"])
            self.assertEqual(len(list(output.parent.parent.iterdir())), 1)

    def test_csv_delivery_checks_bad_row_and_later_record_before_saving_source(self):
        if not self.settings.c_sandbox_compiler.is_file():
            self.skipTest("Wasm compiler assets are optional")
        workspace = Path(self.temp.name) / "workspace"
        self.settings.local_source_workspace = workspace
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        source = r'''#include <stdio.h>
#include <stdlib.h>
int main(void) {
    FILE *f = fopen(getenv("VISIONSOUL_DATA_FILE"), "r");
    if (!f) return 1;
    char line[128], name[20];
    int n, total = 0;
    if (!fgets(line, sizeof line, f) || sscanf(line, "%d", &n) != 1) { fclose(f); return 1; }
    for (int i = 1; i <= n && fgets(line, sizeof line, f); ++i) {
        int value;
        if (sscanf(line, "%19[^,],%d", name, &value) != 2) {
            printf("警告：第 %d 行数据非法，已跳过\n", i);
            continue;
        }
        total += value;
    }
    fclose(f);
    printf("总数：%d\n", total);
    return 0;
}
'''
        schema = {"delimiter": ",", "field_count": 2, "header": "count", "bad_row": 2,
                  "warning": "警告：第 2 行数据非法，已跳过", "after_bad": "总数：10"}
        sample = {"sample_file": {"filename": "scores.txt", "content": "3\nA,4\nbad\nB,6\n"},
                  "expected": "警告：第 2 行数据非法，已跳过\n总数：10\n", "row_check": schema}
        fence = chr(96) * 3
        body = {"message": "编写 C 程序，从成绩文件逐行读取并跳过坏行。", "space": "work", "open_in_ide": True}

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        with (TestClient(local_app, base_url=base, headers={"Origin": base}) as client,
              patch("app.services.local_ide.launch_ide"),
              patch("app.services.local_ide.show_sandbox_result", return_value=True)):
            for index, (code, case, expected_status) in enumerate((
                (source, {**sample, "row_check": {**schema, "delimiter": "space"}}, "failed"),
                (source.replace("continue;", "break;"), sample, "mismatch"),
                (source, sample, "awaiting_file"),
            ), 1):
                reply = (f"[需要数据文件]\n[逐行验收]\n[文件名: main.c]\n{fence}c\n{code}{fence}\n"
                         f"{fence}visionsoul-cases\n{json.dumps(case, ensure_ascii=False)}\n{fence}")
                with patch.object(self.service.client, "complete", return_value=ModelReply(answer=reply, model="test")):
                    response = client.post("/api/chat/messages", json={**body, "request_id": str(index) * 32})
                self.assertEqual(response.status_code, 200, response.text)
                delivery = response.json()["local_source"]
                self.assertIsNotNone(delivery, response.text)
                self.assertIsNotNone(delivery["python_check"], delivery)
                self.assertEqual(delivery["python_check"]["status"], expected_status, delivery)
                if expected_status != "awaiting_file":
                    self.assertEqual(delivery["status"], "error")
                    self.assertIsNone(delivery["path"])
                    self.assertFalse(list(workspace.glob("C/*.c")))

            self.assertIn("后续合法记录", delivery["python_check"]["detail"])
            self.assertTrue(Path(delivery["path"]).is_file())
            rerun = {"run_id": delivery["sandbox_run_id"], "open_result_window": False}
            wrong = client.post("/api/chat/sandbox-runs", json={**rerun, "approved_file": {
                "filename": "another.txt", "content": "2\nA 4\nB 6\n",
            }}).json()
            self.assertEqual((wrong["status"], wrong["attempts"]), ("file_unused", 0))
            self.assertIn("2 列", wrong["detail"])
            verified = client.post("/api/chat/sandbox-runs", json={**rerun, "approved_file": {
                "filename": "real.txt", "content": "3\nA,4\nbad\nB,9\n",
            }, "expected": "警告：第 2 行数据非法，已跳过\n总数：13\n"}).json()
            self.assertEqual((verified["status"], verified["data_file"]), ("passed", "real.txt"), verified)

    def test_c_and_cpp_desktop_delivery_and_local_rerun(self):
        if not self.settings.c_sandbox_compiler.is_file():
            self.skipTest("Wasm compiler assets are optional")
        root = Path(self.temp.name)
        self.settings.local_source_workspace = root / "workspace"
        launcher = root / "devenv.exe"
        launcher.touch()
        self.settings.local_visual_studio = launcher
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        picture = self.image()

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        origin = {"Origin": base}
        fence = chr(96) * 3
        code = '#include <stdio.h>\nint main(void) { int n; if (scanf("%d", &n) == 1) printf("%d\\n", n * 2); return 0; }\n'
        with (TestClient(local_app, base_url=base) as client,
              patch("app.services.local_ide.launch_ide") as launch,
              patch("app.services.local_ide.show_sandbox_result", return_value=True) as viewer,
              patch("app.api.routes.chat.show_sandbox_result", return_value=True) as rerun_viewer):
            for index, lang in enumerate(("c", "cpp"), 1):
                with self.subTest(language=lang):
                    filename = f"main.{lang}"
                    reply = f"[文件名: {filename}]\n{fence}{lang}\n{code}{fence}"
                    with patch.object(self.service.client, "complete", return_value=ModelReply(answer=reply, model="test")):
                        response = client.post("/api/chat/messages", headers=origin, json={
                            "request_id": str(index) * 32, "space": "work", "image_id": picture,
                            "message": f"写 {lang} 程序", "open_in_ide": True,
                            "python_test": {"stdin": "21", "expected": "42"},
                        })
                    self.assertEqual(response.status_code, 200, response.text)
                    source = response.json()["local_source"]
                    self.assertEqual(source["status"], "opened")
                    self.assertTrue(source["desktop_result_opened"])
                    self.assertEqual(source["python_check"]["status"], "passed")
                    self.assertEqual(viewer.call_args.args[1].stdout, "42\n")
                    self.assertEqual(viewer.call_args.args[1].stdin, "21")
                    path = Path(source["path"])
                    self.assertTrue(launch.call_args.args[0][1].endswith(f"{path.name}.sln"))
                    path.write_text("edited source", encoding="utf8")
                    request = {"run_id": source["sandbox_run_id"], "stdin": "22"}
                    with patch("app.services.deepseek.DeepSeekClient.complete", side_effect=AssertionError("model called")):
                        retry = client.post("/api/chat/sandbox-runs", headers=origin, json=request)
                    self.assertEqual(retry.status_code, 200, retry.text)
                    self.assertEqual(retry.json()["stdout"], "44\n")
                    self.assertTrue(retry.json()["desktop_result_opened"])
                    self.assertEqual(rerun_viewer.call_args.args[0], path)
                    self.assertEqual(rerun_viewer.call_args.args[1].stdout, "44\n")
                    self.assertEqual(rerun_viewer.call_args.args[1].stdin, "22")
                    self.assertEqual(viewer.call_args.kwargs, {"run_id": source["sandbox_run_id"], "origin": base})
                    window_count = rerun_viewer.call_count
                    with patch("app.services.deepseek.DeepSeekClient.complete", side_effect=AssertionError("model called")):
                        manual = client.post("/api/chat/sandbox-runs", headers=origin,
                                             json={**request, "stdin": "23\n", "open_result_window": False})
                    self.assertEqual(manual.status_code, 200, manual.text)
                    self.assertEqual(manual.json()["stdin"], "23\n")
                    self.assertEqual(manual.json()["stdout"], "46\n")
                    self.assertFalse(manual.json()["desktop_result_opened"])
                    self.assertEqual(rerun_viewer.call_count, window_count)
            self.assertEqual(launch.call_count, 2)
        self.assertEqual(list((root / "workspace" / ".sandbox-jobs").iterdir()), [])

    def test_two_turn_context_fixed_persona_and_persistence(self):
        first = self.send("我叫小夏")
        second = self.send("我叫什么？", "2", first["conversation_id"])
        messages = self.payloads[1]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": PERSONA_PROMPT})
        self.assertIn("活泼俏皮", messages[0]["content"])
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "我叫小夏")
        self.assertEqual(messages[2]["content"], first["assistant_message"]["content"])
        fresh_service = ChatService(self.settings)
        fresh_service.store.initialize()
        stored = fresh_service.store.messages(first["conversation_id"])
        self.assertEqual(len(stored), 4)
        self.assertEqual(stored[-1]["content"], second["assistant_message"]["content"])
        self.assertEqual(self.payloads[1]["thinking"], {"type": "disabled"})

    def test_picture_is_single_and_carried_forward_without_duplicate_attachments(self):
        picture = self.image()
        first = self.send("看看这张图", image=picture)
        self.send("里面什么颜色？", "2", first["conversation_id"])
        for payload in self.payloads:
            pictures = [part for message in payload["messages"] if isinstance(message["content"], list)
                        for part in message["content"] if part["type"] == "image_url"]
            self.assertEqual(len(pictures), 1)
            self.assertTrue(pictures[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sum(bool(item["image_id"]) for item in self.service.store.messages(first["conversation_id"])), 1)
        with self.assertRaises(DeepSeekError) as error:
            self.send("换成这张", "3", first["conversation_id"], self.image("blue"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.payloads), 2)

    def test_text_topic_can_attach_first_picture_later(self):
        first = self.send("今天刚学会画图")
        second = self.send("这是刚画的", "2", first["conversation_id"], self.image())
        self.assertTrue(second["image_id"])
        self.assertEqual(self.payloads[1]["messages"][1]["content"], "今天刚学会画图")

    def test_completed_retry_is_cached_and_reuse_for_other_content_is_rejected(self):
        first = self.send("你好")
        replay = self.send("你好")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["assistant_message"], first["assistant_message"])
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(len(self.service.store.messages(first["conversation_id"])), 2)
        with self.assertRaises(DeepSeekError) as error:
            self.send("另一条内容")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.payloads), 1)

    def test_new_topics_are_isolated(self):
        first = self.send("第一段的私密上下文")
        second = self.send("第二段你好", "2")
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(len(self.payloads[1]["messages"]), 2)
        self.assertNotIn("第一段", json.dumps(self.payloads[1], ensure_ascii=False))

    def test_failed_call_does_not_save_a_partial_turn(self):
        first = self.send("第一轮")
        failing = ChatService(self.settings, DeepSeekClient(self.settings, httpx.MockTransport(lambda request: httpx.Response(503))))
        with self.assertRaises(DeepSeekError):
            failing.send(conversation_id=first["conversation_id"], request_id="2" * 32, message="失败轮次", image_id=None)
        self.assertEqual(len(self.service.store.messages(first["conversation_id"])), 2)
        self.assertIsNone(self.service.store.cached("2" * 32))
        with self.assertRaises(DeepSeekError):
            failing.send(conversation_id=None, request_id="3" * 32, message="失败的新话题", image_id=None)
        self.assertEqual(len(self.service.store.recent()), 1)

    def test_unknown_image_or_topic_never_calls_provider(self):
        for conversation, image in [("0" * 32, None), (None, "0" * 32)]:
            with self.subTest(conversation=conversation, image=image):
                with self.assertRaises(DeepSeekError) as error:
                    self.send(conversation=conversation, image=image)
                self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(len(self.payloads), 0)

    def test_concurrent_requests_for_same_topic_do_not_make_duplicate_calls(self):
        first = self.send("开始")
        entered, release = threading.Event(), threading.Event()

        class SlowClient:
            calls = 0
            def complete(inner, messages):
                inner.calls += 1; entered.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release request")
                return ModelReply(answer="慢一点也能聊好。", model="deepseek-flash")

        client = SlowClient()
        service = ChatService(self.settings, client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(service.send, conversation_id=first["conversation_id"], request_id="2" * 32, message="等待中", image_id=None)
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(DeepSeekError) as error:
                    service.send(conversation_id=first["conversation_id"], request_id="3" * 32, message="重复点击", image_id=None)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(client.calls, 1)
            finally:
                release.set()
            future.result(timeout=3)
        self.assertEqual(len(service.store.messages(first["conversation_id"])), 4)

    def test_context_budget_keeps_complete_recent_pairs(self):
        history = []
        for number in range(10):
            history.extend([{"role": "user", "content": f"问题{number}"}, {"role": "assistant", "content": f"回复{number}"}])
        result = context_messages(history, self.settings, "新消息")
        self.assertEqual(len(result), 13)
        self.assertEqual(result[1]["content"], "问题4")
        tight = self.settings.model_copy(update={"chat_context_chars": len(PERSONA_PROMPT) + 3 + 9})
        result = context_messages(history, tight, "新消息")
        self.assertEqual([item["role"] for item in result], ["system", "user", "assistant"])
        self.assertEqual(result[-2]["content"], "问题9")

    def test_storage_failure_is_safe_and_does_not_call_provider(self):
        with patch.object(self.service.store, "initialize", side_effect=PermissionError("private-path")):
            with self.assertRaises(DeepSeekError) as error:
                self.send()
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("private-path", str(error.exception))
        self.assertEqual(self.payloads, [])

    def test_http_validation_history_and_non_sensitive_status(self):
        original = app.dependency_overrides.copy()
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(original)))
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        with TestClient(app) as client:
            status = client.get("/api/chat/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["name"], "小灵")
            self.assertNotIn("test-key-not-valid", status.text)
            for extras in [{"role": "system"}, {"messages": []}, {"persona": "replace"}, {"image_ids": ["not-an-id"]}]:
                response = client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": "你好", **extras})
                self.assertEqual(response.status_code, 422)
            for message in ["   ", "字" * 2001]:
                self.assertEqual(client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": message}).status_code, 422)
            sent = client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": "你好"})
            self.assertEqual(sent.status_code, 200, sent.text)
            target = sent.json()["conversation_id"]
            self.assertEqual(len(client.get(f"/api/chat/conversations/{target}").json()["messages"]), 2)
            self.assertEqual(len(client.get("/api/chat/conversations").json()), 1)
            self.assertEqual(client.get("/api/chat/conversations/" + "0" * 32).status_code, 404)
            self.assertEqual(client.get("/api/chat/conversations/../bad").status_code, 404)
            self.assertNotIn("test-key-not-valid", sent.text)

    def test_deleting_test_topic_also_removes_messages_and_cache(self):
        first = self.send()
        self.service.store.delete(first["conversation_id"])
        self.assertEqual(self.service.store.messages(first["conversation_id"]), [])
        self.assertIsNone(self.service.store.cached("1" * 32))


if __name__ == "__main__":
    unittest.main()
