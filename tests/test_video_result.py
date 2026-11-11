"""本机模拟各种真实风格的模型格式偏差；不重试、不补造证据。"""
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply
from app.services.video_result import result_failure, ResultFormatError
from app.services.videos import VideoService
from app.services.audio_evidence import AudioTranscript, SpeechSegment
from test_video import make_video, result_payload


class VideoResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="visionsoul-result-video-")
        cls.addClassCleanup(cls.fixture.cleanup)
        path = Path(cls.fixture.name) / "test.mp4"
        make_video(path)
        cls.data = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-result-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, deepseek_api_key="not-a-real-key", audio_enabled=False,
            chat_database_path=root/"chat.sqlite3", role_database_path=root/"roles.sqlite3", video_storage_dir=root/"videos")
        self.calls = []
        self.payload = result_payload()
        self.raw = None
        self.truncated = False
        def handler(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={"model":"mock", "choices":[{"message":{"content":
                self.raw if self.raw is not None else json.dumps(self.payload, ensure_ascii=False)},
                "finish_reason":"length" if self.truncated else "stop"}]})
        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.videos = VideoService(self.settings, self.client, system_prompt="测试工作解析", isolated=True)
        self.video = self.videos.upload(BytesIO(self.data))
        self.chat = ChatService(self.settings, self.client)

    def send(self):
        return self.chat.send(space="work", conversation_id=None, request_id="a"*32,
            message="请帮我完成这道题，用C写出来", image_id=None, video_id=self.video.video_id)

    def test_missing_uncertainty_is_conservatively_uncertain_and_long_answer_is_saved(self):
        for row in self.payload["observations"]:
            row.pop("uncertain")
        self.payload["reply"] = "必要说明" * 150
        result = self.send()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(row["uncertain"] for row in result["video_context"]["observations"]))
        self.assertEqual(result["assistant_message"]["content"], self.payload["reply"])
        self.assertEqual(len(self.chat.store.messages(result["conversation_id"])), 2)

    def test_complete_fenced_json_is_accepted_without_second_model_call(self):
        self.raw = "```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"
        self.send()
        self.assertEqual(len(self.calls), 1)

    def test_extra_valid_observations_are_saved_and_replayed(self):
        self.payload["observations"] = [
            {"frame_index": 0, "keywords": ["题目"], "description": f"可见条件 {index}", "uncertain": False}
            for index in range(12)
        ]
        result = self.send()
        self.assertEqual(len(result["video_context"]["observations"]), 12)
        self.assertEqual(len(self.chat.store.messages(result["conversation_id"])), 2)
        self.assertEqual(self.send()["video_context"]["observations"], result["video_context"]["observations"])
        self.assertEqual(len(self.calls), 1)

    def test_invalid_reference_after_eighth_observation_still_fails(self):
        self.payload["observations"] = [
            {"frame_index": 0, "keywords": ["题目"], "description": f"可见条件 {index}"}
            for index in range(8)
        ] + [{"frame_index": 60, "keywords": ["题目"], "description": "不存在的画面"}]
        with self.assertRaisesRegex(DeepSeekError, "引用了不存在的画面"):
            self.send()
        self.assertEqual(self.chat.store.recent(), [])
        self.assertEqual(len(self.calls), 1)

    def test_literal_newline_in_reply_is_preserved(self):
        self.payload["reply"] = "int main(void) {\n    return 0;\n}"
        self.raw = json.dumps(self.payload, ensure_ascii=False).replace("\\n", "\n")
        result = self.send()
        self.assertEqual(result["assistant_message"]["content"], self.payload["reply"])

    def test_answer_and_separate_code_field_are_normalized_without_inventing_code(self):
        self.payload["answer"] = self.payload.pop("reply")
        self.payload["code"] = 'int main(void) { puts("hello"); return 0; }'
        self.payload["language"] = "C"
        result = self.send()
        self.assertIn(self.payload["code"], result["assistant_message"]["content"])
        self.assertEqual(len(self.calls), 1)

    def test_unreadable_video_can_return_no_observations_instead_of_fake_evidence(self):
        self.payload = {"reply":"无法确认题目，请补拍清楚题干。", "summary":"缺少可靠画面观察。", "observations":[]}
        result = self.send()
        self.assertEqual(result["video_context"]["observations"], [])
        self.assertIn("补拍", result["assistant_message"]["content"])

    def test_invalid_model_owned_detail_metadata_does_not_break_server_selection(self):
        self.payload["detail_selection"] = {"profile":"garbage", "frame_indices":"not-an-array"}
        result = self.send()
        self.assertEqual(result["video_context"]["detail_selection"]["profile"], "text")

    def test_truncation_has_specific_reason_no_saved_turn_and_no_auto_retry(self):
        self.truncated = True
        self.raw = '{"reply":"private-code-not-for-log'
        with self.assertLogs("app.services.videos", level="WARNING") as logs:
            with self.assertRaises(DeepSeekError) as error:
                self.send()
        self.assertIn("达到输出上限", str(error.exception))
        self.assertIn("错误编号", str(error.exception))
        self.assertEqual(self.chat.store.recent(), [])
        self.assertEqual(len(self.calls), 1)
        reports = list((Path(self.temp.name)/"diagnostics").glob("*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text(encoding="utf8"))["reason"], "truncated")
        self.assertNotIn("private-code-not-for-log", reports[0].read_text(encoding="utf8") + "".join(logs.output))
        self.assertNotIn("not-a-real-key", reports[0].read_text(encoding="utf8") + "".join(logs.output))

    def test_bad_frame_and_incomplete_json_are_not_repaired_into_false_success(self):
        for raw, expected in [('```json\n{"reply":"cut off', "JSON格式无法读取"),
                              (json.dumps(result_payload(19)), "依据校验未通过"),
                              ('{"reply":"missing evidence"}', "字段格式不符合")]:
            self.raw = raw
            with self.assertRaises(DeepSeekError) as error:
                self.send()
            self.assertIn(expected, str(error.exception))
            self.assertEqual(self.chat.store.recent(), [])
        self.assertEqual(len(self.calls), 3)

    def test_schema_diagnostics_record_location_without_user_text(self):
        self.payload["observations"][0]["keywords"] = {"private-user-text":"do-not-log"}
        with self.assertRaises(DeepSeekError):
            self.send()
        report = next((Path(self.temp.name)/"diagnostics").glob("*.json")).read_text(encoding="utf8")
        self.assertIn("observations.0.keywords", report)
        self.assertNotIn("private-user-text", report)
        self.assertNotIn("do-not-log", report)

    def test_conflicting_answers_or_broken_code_are_not_silently_discarded(self):
        for extra in [{"answer":"a different answer"}, {"code":["not text"]}]:
            with self.subTest(extra=extra):
                payload = {**result_payload(), **extra}
                self.raw = json.dumps(payload)
                with self.assertRaises(DeepSeekError):
                    self.send()
        self.assertEqual(len(self.calls), 2)

    def test_unwritable_diagnostic_directory_does_not_hide_original_parse_failure(self):
        with patch("app.services.video_result.Path.write_text", side_effect=OSError("test denial")):
            report, message = result_failure(ResultFormatError("invalid_json"), ModelReply(answer="not JSON", model="mock"),
                                             Path(self.temp.name), "json")
        self.assertIn("JSON格式无法读取", message)
        self.assertEqual(report["reason"], "invalid_json")
        self.assertEqual(len(self.calls), 0)

    def test_long_reply_with_server_added_citations_stays_within_result_contract(self):
        info = self.video.model_copy(update={"audio": AudioTranscript(status="ready", segments=[
            SpeechSegment(id=0, start=0, end=1, text="题目要求输入两个整数并求和。")])})
        self.payload["reply"] = "解释" * 3000
        self.payload["audio_links"] = [{"segment_id":0,"relation":"audio_only","detail":"只有对白说明。"}]
        result = self.videos._observe(info, "为什么这样做，依据在哪里")
        self.assertGreater(len(result.reply), 6000)
        self.assertIn("时间依据", result.reply)
        self.assertEqual(len(self.calls), 1)
