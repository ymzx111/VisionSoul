"""真实 HTTP 验证；使用临时存储目录，不改动用户上传图片。"""

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from io import BytesIO

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1024 * 1024


def make_image(image_format="PNG", size=(24, 16), **options):
    output = BytesIO()
    Image.new("RGB", size, "green").save(output, image_format, **options)
    return output.getvalue()


class ImageUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="visionsoul-upload-test-")
        cls.directory = Path(cls.temp.name) / "images"
        cls.log = (Path(cls.temp.name) / "server.log").open("w+", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        environment = {
            **os.environ,
            "IMAGE_STORAGE_DIR": str(cls.directory),
            "UPLOAD_MAX_BYTES": str(LIMIT),
            "IMAGE_MAX_PIXELS": "2000000",
            "IMAGE_MAX_DIMENSION": "2048",
        }
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT / "backend",
            env=environment,
            stdout=cls.log,
            stderr=cls.log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        cls.addClassCleanup(cls.cleanup_server)
        for _ in range(100):
            try:
                if cls.request("GET", "/health")[0] == 200:
                    return
            except OSError:
                pass
            if cls.process.poll() is not None:
                break
            time.sleep(0.1)
        cls.log.seek(0)
        raise RuntimeError("测试服务器未启动：" + cls.log.read())

    @classmethod
    def cleanup_server(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.log.close()
        cls.temp.cleanup()

    @classmethod
    def request(cls, method, path, body=None, headers=None, chunked=False):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {}, encode_chunked=chunked)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def upload(self, data, filename="photo.png", media_type="image/png"):
        boundary = "visionsoul-test-boundary"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {media_type}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", "/api/images", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})

    def assert_rejected_without_save(self, data, expected, **options):
        before = set(self.directory.glob("*"))
        status, _, body = self.upload(data, **options)
        self.assertEqual(status, expected, body)
        self.assertIn("detail", json.loads(body))
        self.assertEqual(set(self.directory.glob("*")), before)

    def test_original_endpoints_and_openapi(self):
        for path in ["/", "/health", "/docs", "/redoc", "/openapi.json"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 200)
        schema = json.loads(self.request("GET", "/openapi.json")[2])
        self.assertIn("/api/images", schema["paths"])

    def test_three_formats_and_preview(self):
        for image_format, media_type in [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]:
            with self.subTest(format=image_format):
                status, _, body = self.upload(make_image(image_format))
                self.assertEqual(status, 201, body)
                saved = json.loads(body)
                self.assertEqual((saved["width"], saved["height"]), (24, 16))
                self.assertEqual(saved["media_type"], media_type)
                self.assertRegex(saved["image_id"], r"^[0-9a-f]{32}$")
                status, headers, content = self.request("GET", saved["url"])
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], media_type)
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertEqual(saved["size_bytes"], len(content))
                with Image.open(BytesIO(content)) as result:
                    result.load()

    def test_ignores_untrusted_filename_and_mime(self):
        status, _, body = self.upload(make_image(), filename="../../outside.exe", media_type="application/octet-stream")
        self.assertEqual(status, 201, body)
        saved = json.loads(body)
        self.assertTrue((self.directory / (saved["image_id"] + ".png")).is_file())
        self.assertFalse((self.directory.parent / "outside.exe").exists())

    def test_strips_exif_and_honors_orientation(self):
        exif = Image.Exif()
        exif[274] = 6  # 旋转 90 度
        exif[270] = "private-location-note"
        status, _, body = self.upload(make_image("JPEG", (12, 20), exif=exif))
        self.assertEqual(status, 201, body)
        saved = json.loads(body)
        self.assertEqual((saved["width"], saved["height"]), (20, 12))
        content = self.request("GET", saved["url"])[2]
        self.assertNotIn(b"private-location-note", content)
        with Image.open(BytesIO(content)) as result:
            self.assertFalse(result.getexif())

    def test_rejects_empty_fake_and_truncated_images(self):
        for data in [b"", b"<svg>not a png</svg>", make_image()[:40]]:
            with self.subTest(length=len(data)):
                self.assert_rejected_without_save(data, 400)

    def test_rejects_unsupported_format(self):
        self.assert_rejected_without_save(make_image("GIF"), 415)

    def test_rejects_animated_images(self):
        output = BytesIO()
        Image.new("RGB", (10, 10), "red").save(
            output, "PNG", save_all=True,
            append_images=[Image.new("RGB", (10, 10), "blue")], duration=100,
        )
        self.assert_rejected_without_save(output.getvalue(), 400)

    def test_rejects_large_file(self):
        self.assert_rejected_without_save(b"x" * (LIMIT + 1), 413)

    def test_rejects_excessive_dimensions_and_pixel_count(self):
        for size in [(2049, 1), (1500, 1500)]:
            with self.subTest(size=size):
                self.assert_rejected_without_save(make_image(size=size), 413)

    def test_request_body_limit_with_and_without_content_length(self):
        payload = b"x" * (LIMIT + 65537)
        for path in ["/api/images", "/api/images/"]:
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, payload)[0], 413)
        chunks = (payload[index:index + 16384] for index in range(0, len(payload), 16384))
        self.assertEqual(self.request("POST", "/api/images", chunks, chunked=True)[0], 413)

    def test_missing_file_and_unknown_image(self):
        self.assertEqual(self.request("POST", "/api/images")[0], 422)
        for image_id in ["0" * 32, "not-an-id", "..%2Frequirements.txt"]:
            with self.subTest(image_id=image_id):
                self.assertEqual(self.request("GET", "/api/images/" + image_id)[0], 404)


if __name__ == "__main__":
    unittest.main()
