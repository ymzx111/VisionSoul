"""只兜底可无损恢复的类型与长度问题；其余仍交给严格校验。"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.video_result import sanitize_video_result


class SanitizeTests(unittest.TestCase):
    def test_single_string_keywords_become_list(self):
        payload = {"observations": [{"frame_index": 0, "keywords": "代码", "description": "屏幕上的C代码"}],
                   "summary": "概述", "reply": "回应"}
        data = sanitize_video_result(payload)
        self.assertEqual(data["observations"][0]["keywords"], ["代码"])

    def test_notes_and_long_text_are_capped(self):
        payload = {"observations": [], "summary": "概述" * 200, "reply": "回应" * 4000,
                   "evidence_notes": [f"说明{i}" for i in range(12)]}
        data = sanitize_video_result(payload)
        self.assertLessEqual(len(data["summary"]), 240)
        self.assertLessEqual(len(data["reply"]), 6000)
        self.assertEqual(len(data["evidence_notes"]), 8)

    def test_unknown_fields_and_bad_keywords_are_left_for_strict_check(self):
        payload = {"observations": [{"frame_index": 0, "keywords": {"private": "x"}, "description": "屏幕"}],
                   "summary": "概述", "reply": "回应", "unknown_field": {"a": 1}}
        data = sanitize_video_result(payload)
        self.assertIn("unknown_field", data)
        self.assertEqual(data["observations"][0]["keywords"], {"private": "x"})


if __name__ == "__main__":
    unittest.main()
