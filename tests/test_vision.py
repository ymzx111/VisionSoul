"""图片理解协议与失败路径验证；全部使用模拟传输，不连接真实模型。"""

import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient
from app.api.routes.vision import get_vision_service
from app.core.config import Settings, get_settings
from app.main import app
from app.services.images import save_image
from app.services.vision import API_URL, DeepSeekVisionService, VisionServiceError


def completion(answer="图中是一块绿色区域。", reason="stop"):
    return {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": answer}, "finish_reason": reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 12, "total_tokens": 112},
    }


class VisionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-vision-test-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(
            _env_file=None, image_storage_dir=Path(self.temp.name),
            deepseek_api_key="test-key-not-valid",
        )
        buffer = BytesIO()
        Image.new("RGB", (32, 24), "green").save(buffer, "PNG")
        self.saved = save_image(buffer.getvalue(), self.settings)

    def service(self, handler, settings=None):
        return DeepSeekVisionService(settings or self.settings, httpx.MockTransport(handler))


class VisionTests(VisionFixture):
    def test_request_protocol_cost_controls_and_result(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), API_URL)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["authorization"], "Bearer test-key-not-valid")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "deepseek-flash")
            self.assertEqual(payload["max_tokens"], 512)
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["messages"][0]["role"], "system")
            parts = payload["messages"][1]["content"]
            self.assertEqual(parts[0]["text"], "什么颜色？")
            image = parts[1]["image_url"]
            self.assertEqual(image["detail"], "low")
            prefix, encoded = image["url"].split(",", 1)
            self.assertEqual(prefix, "data:image/png;base64")
            with Image.open(BytesIO(base64.b64decode(encoded))) as decoded:
                self.assertEqual(decoded.size, (32, 24))
            return httpx.Response(200, json=completion())

        result = self.service(handler).analyze(self.saved.image_id, "什么颜色？")
        self.assertEqual(result.answer, "图中是一块绿色区域。")
        self.assertEqual(result.image_id, self.saved.image_id)
        self.assertEqual(result.usage.total_tokens, 112)
        self.assertFalse(result.truncated)
        self.assertEqual(len(requests), 1)

    def test_missing_key_or_image_never_calls_network(self):
        def unexpected(request):
            self.fail("配置缺失或图片不存在时不应调用模型")

        empty_key = self.settings.model_copy(update={"deepseek_api_key": Settings(_env_file=None, deepseek_api_key="").deepseek_api_key})
        for settings, image_id, status in [
            (empty_key, self.saved.image_id, 503),
            (self.settings, "0" * 32, 404),
        ]:
            with self.subTest(status=status):
                with self.assertRaises(VisionServiceError) as error:
                    self.service(unexpected, settings).analyze(image_id, "描述图片")
                self.assertEqual(error.exception.status_code, status)

    def test_provider_failures_are_sanitized_and_not_retried(self):
        for upstream, expected, phrase in [
            (401, 503, "认证失败"), (403, 503, "认证失败"),
            (402, 503, "余额不足"), (429, 503, "过于频繁"),
            (500, 502, "暂时无法完成"), (400, 502, "暂时无法完成"),
            (307, 502, "暂时无法完成"),
        ]:
            with self.subTest(upstream=upstream):
                requests = []

                def handler(request):
                    requests.append(request)
                    return httpx.Response(upstream, json={"error": "test-key-not-valid private diagnostic"})

                with self.assertRaises(VisionServiceError) as error:
                    self.service(handler).analyze(self.saved.image_id, "描述图片")
                self.assertEqual(error.exception.status_code, expected)
                self.assertIn(phrase, str(error.exception))
                self.assertNotIn("test-key-not-valid", str(error.exception))
                self.assertNotIn("private diagnostic", str(error.exception))
                self.assertEqual(len(requests), 1)

    def test_timeout_and_connection_failure(self):
        for error_type, expected in [(httpx.ReadTimeout, 504), (httpx.ConnectError, 502)]:
            with self.subTest(error=error_type.__name__):
                def handler(request):
                    raise error_type("private network diagnostic", request=request)

                with self.assertRaises(VisionServiceError) as error:
                    self.service(handler).analyze(self.saved.image_id, "描述图片")
                self.assertEqual(error.exception.status_code, expected)
                self.assertNotIn("private network diagnostic", str(error.exception))

    def test_malformed_and_empty_results(self):
        for response in [
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json=completion("  ")),
            httpx.Response(200, json=completion(None)),
        ]:
            with self.subTest(body=response.text):
                with self.assertRaises(VisionServiceError) as error:
                    self.service(lambda request: response).analyze(self.saved.image_id, "描述图片")
                self.assertEqual(error.exception.status_code, 502)

    def test_truncated_answer_and_optional_usage(self):
        payload = completion("部分回复", "length")
        payload.pop("usage")
        result = self.service(lambda request: httpx.Response(200, json=payload)).analyze(self.saved.image_id, "描述图片")
        self.assertTrue(result.truncated)
        self.assertIsNone(result.usage)

    def test_oversized_saved_file_never_calls_network(self):
        settings = self.settings.model_copy(update={"upload_max_bytes": 1})

        def unexpected(request):
            self.fail("文件异常时不应调用模型")

        with self.assertRaises(VisionServiceError) as error:
            self.service(unexpected, settings).analyze(self.saved.image_id, "描述图片")
        self.assertEqual(error.exception.status_code, 400)


class VisionRouteTests(VisionFixture):
    def setUp(self):
        super().setUp()
        original = app.dependency_overrides.copy()
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(original)))
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.mock_service = self.service(lambda request: httpx.Response(200, json=completion()))
        app.dependency_overrides[get_vision_service] = lambda: self.mock_service
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_status_only_reports_non_secret_settings(self):
        response = self.client.get("/api/vision/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"configured": True, "model": "deepseek-flash", "image_detail": "low", "max_tokens": 512})
        self.assertNotIn("test-key-not-valid", response.text)

    def test_analyze_http_response(self):
        response = self.client.post("/api/vision/analyze", json={"image_id": self.saved.image_id, "question": "什么颜色？"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "图中是一块绿色区域。")
        self.assertNotIn("test-key-not-valid", response.text)

    def test_request_validation(self):
        for body in [
            {"image_id": "../../file"}, {"image_id": "0" * 32, "question": "   "},
            {"image_id": "0" * 32, "question": "x" * 2001},
            {"image_id": "0" * 32, "api_key": "client-key"},
        ]:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/vision/analyze", json=body).status_code, 422)

    def test_missing_key_returns_actionable_error(self):
        settings = Settings(_env_file=None, image_storage_dir=Path(self.temp.name), deepseek_api_key="")
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_vision_service] = lambda: DeepSeekVisionService(settings)
        self.assertFalse(self.client.get("/api/vision/status").json()["configured"])
        response = self.client.post("/api/vision/analyze", json={"image_id": self.saved.image_id})
        self.assertEqual(response.status_code, 503)
        self.assertIn("backend/.env", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
