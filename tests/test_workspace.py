"""检查前端与 FastAPI 同源集成，不触发真实模型调用。"""

from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient
from app.api.routes import workspace
from app.main import app


class WorkspaceTests(unittest.TestCase):
    def test_missing_build_shows_setup_message(self):
        with tempfile.TemporaryDirectory(prefix="visionsoul-frontend-test-") as directory:
            with patch.object(workspace, "FRONTEND_DIR", Path(directory)), TestClient(app) as client:
                response = client.get("/app/")
                self.assertEqual(response.status_code, 503)
                self.assertIn("图片工作台尚未构建", response.text)

    @unittest.skipUnless((workspace.FRONTEND_DIR / "index.html").is_file(), "先构建 frontend 再验证打包资源")
    def test_built_frontend_and_assets_are_served(self):
        with TestClient(app) as client:
            response = client.get("/app")
            self.assertEqual(response.status_code, 200)
            self.assertIn("图片与视频工作台", response.text)
            assets = re.findall(r'(?:src|href)="(/app/assets/[^\"]+)"', response.text)
            self.assertTrue(assets)
            for asset in assets:
                self.assertEqual(client.get(asset).status_code, 200)
            self.assertNotIn("/app/", client.get("/openapi.json").json()["paths"])


if __name__ == "__main__":
    unittest.main()
