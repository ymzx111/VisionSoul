"""确认中文文档可加载，同时保留程序实际使用的字段名称。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient
from app.main import app


class ChineseDocsTests(unittest.TestCase):
    def test_localized_docs_preserve_api_contract(self):
        with TestClient(app) as client:
            page = client.get("/docs")
            self.assertEqual(page.status_code, 200)
            self.assertIn('lang="zh-CN"', page.text)
            self.assertIn("中文接口文档", page.text)
            for asset in ["/docs-assets/zh-cn.js", "/docs-assets/zh-cn.css"]:
                self.assertIn(asset, page.text)
                self.assertEqual(client.get(asset).status_code, 200)
            schema = client.get("/openapi.json").json()
            properties = schema["components"]["schemas"]["VisionRequest"]["properties"]
            self.assertEqual(set(properties), {"image_id", "question"})
            self.assertEqual(properties["image_id"]["title"], "图片编号")
            self.assertEqual(properties["question"]["title"], "提问内容")
            self.assertEqual(set(client.get("/").json()), {"project", "version", "status", "docs"})
            self.assertEqual(set(client.get("/health").json()), {"status", "project", "version", "uptime_seconds", "timestamp"})


if __name__ == "__main__":
    unittest.main()
