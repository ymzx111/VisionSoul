from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import Settings, get_settings
from app.api.routes.videos import get_video_service
from app.services.audio_evidence import AudioTranscript, SpeechRetry, SpeechSegment, SpeechWord, model_context
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient
from app.services.videos import VideoService, VideoError, ANALYSIS_LOCK
from test_video import make_video, result_payload
from test_video_comparison import comparison


class AudioCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        path = Path(cls.fixtures.name) / "source.mp4"
        make_video(path, seconds=4, varying=True)
        cls.source = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, video_storage_dir=root / "videos", chat_database_path=root / "chat.sqlite3",
            audio_enabled=False, deepseek_api_key="test-key")
        self.calls = []
        self.payload = {**result_payload(), "comparison": comparison(), "audio_links": [{"segment_id": 0,
            "frame_index": 2, "frame_time": 2, "relation": "uncertain", "detail": "可能相关"}]}
        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {"content":
                json.dumps(self.payload) if body.get("response_format") else "按当前对白回应。"}, "finish_reason": "stop"}]})
        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.service = VideoService(self.settings, self.client)
        self.video = self.service.upload(BytesIO(self.source))
        self.directory = self.settings.video_storage_dir / self.video.video_id
        self.raw = AudioTranscript(status="ready", message="原始转写", segments=[SpeechSegment(id=0, start=1, end=1.4, text="打磨院",
            words=[SpeechWord(text="打磨", start=1, end=1.2, score=0.4)],
            retry=SpeechRetry(start=0.5, end=1.9, text="达磨院"))])
        self.raw_path = self.directory / "audio.json"
        self.raw_path.write_text(self.raw.model_dump_json(), encoding="utf-8")
        self.raw_bytes = self.raw_path.read_bytes()
        self.edits = [SpeechSegment(id=0, start=1.1, end=1.8, text="达摩院")]
        self.chat = ChatService(self.settings, self.client, self.service)

    def save(self, revision=0, segments=None):
        return self.service.correct_audio(self.video.video_id, revision, segments or self.edits)

    def test_edit_is_separate_atomic_and_survives_restart_without_any_model_call(self):
        saved = self.save()
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["audio"].source, "corrected")
        self.assertEqual(self.raw_path.read_bytes(), self.raw_bytes)
        self.assertFalse(list(self.directory.glob(".audio-edit-*")))
        restored = VideoService(self.settings).info(self.video.video_id)
        self.assertEqual(restored.audio_revision, 1)
        self.assertEqual(restored.audio.segments, self.edits)
        self.assertEqual(saved["original"].segments[0].retry.text, "达磨院")
        self.assertFalse(self.calls)

    def test_unchanged_save_does_not_invalidate_cache_and_stale_revision_conflicts(self):
        unchanged = self.service.correct_audio(self.video.video_id, 0, self.raw.segments)
        self.assertEqual(unchanged["revision"], 0)
        self.assertFalse((self.directory / "audio-corrections.json").exists())
        self.save()
        with self.assertRaises(VideoError) as error:
            self.save()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.save(revision=1)["revision"], 1)

    def test_invalid_range_count_and_order_never_write_or_change_original(self):
        for segments in [[SpeechSegment(id=0, start=1, end=4.1, text="超出")], [],
                         [SpeechSegment(id=0, start=1, end=1, text="零长度")],
                         [SpeechSegment(id=1, start=1, end=2, text="编号错误")]]:
            with self.assertRaises(VideoError):
                self.service.correct_audio(self.video.video_id, 0, segments)
        self.assertEqual(self.raw_path.read_bytes(), self.raw_bytes)
        self.assertFalse((self.directory / "audio-corrections.json").exists())

    def test_busy_analysis_blocks_edit_and_releases_upload_lock(self):
        with ANALYSIS_LOCK:
            with self.assertRaises(VideoError) as error:
                self.save()
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.save()["revision"], 1)

    def test_old_analysis_cache_preserved_but_not_reused_for_corrected_text(self):
        self.service.analyze(self.video.video_id)
        old = self.service._cache_path(self.directory)
        old_bytes = old.read_bytes()
        self.save()
        self.assertTrue(old.is_file())
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertIsNone(self.service.info(self.video.video_id).result)
        self.assertEqual(len(self.calls), 1)

    def test_existing_topics_and_success_replay_mark_old_analysis_stale(self):
        body = dict(conversation_id=None, request_id="a" * 32, message="我分享这段视频", image_id=None, video_id=self.video.video_id)
        first = self.chat.send(**body)
        raw_snapshot = self.chat.store.get(first["conversation_id"])["video_context"]
        first_reply = first["assistant_message"]["content"]
        self.save()
        replay = self.chat.send(**body)
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["video_context"]["analysis_stale"])
        self.assertEqual(replay["assistant_message"]["content"], first_reply)
        self.assertEqual(self.chat.store.get(first["conversation_id"])["video_context"], raw_snapshot)
        self.assertEqual(len(self.calls), 1)
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            restored = client.get(f"/api/chat/conversations/{first['conversation_id']}").json()
        self.assertTrue(restored["video_context"]["analysis_stale"])
        self.assertEqual(restored["video_context"]["current_audio"]["segments"][0]["text"], "达摩院")
        self.assertEqual(self.chat.store.get(first["conversation_id"])["video_context"], raw_snapshot)

    def test_followup_uses_correction_and_removes_stale_report_without_sending_images(self):
        first = self.chat.send(conversation_id=None, request_id="b" * 32, message="分享", image_id=None, video_id=self.video.video_id)
        self.save()
        second = self.chat.send(conversation_id=first["conversation_id"], request_id="c" * 32, message="依据是什么？", image_id=None)
        self.assertTrue(second["video_context"]["analysis_stale"])
        effective = model_context(second["video_context"])
        self.assertEqual(effective["audio_links"], [])
        self.assertIsNone(effective["comparison"])
        self.assertEqual(effective["audio"]["segments"][0]["text"], "达摩院")
        self.assertNotIn("current_audio", effective)
        self.assertNotIn("image_url", json.dumps(self.calls[-1]))
        self.assertIn('1.1—1.8秒：“达摩院”', second["assistant_message"]["content"])
        self.assertIn("用户校对白", second["assistant_message"]["content"])
        self.assertEqual(len(self.calls), 2)

    def test_explicit_new_inspection_resets_stale_flag_and_uses_new_revision(self):
        self.save()
        snapshot, reply = self.service.inspect(self.video.video_id, "依据是什么", [])
        self.assertFalse(snapshot["analysis_stale"])
        self.assertEqual(snapshot["analysis_audio_revision"], 1)
        self.assertIn("达摩院", reply.answer)
        self.assertEqual(len(self.calls), 1)

    def test_new_topic_after_correction_reuses_visual_facts_without_reopening_images(self):
        self.chat.send(conversation_id=None, request_id="d" * 32, message="分享", image_id=None, video_id=self.video.video_id)
        self.save()
        result = self.chat.send(conversation_id=None, request_id="e" * 32, message="换个话题继续聊", image_id=None, video_id=self.video.video_id)
        self.assertTrue(result["video_context"]["analysis_stale"])
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("image_url", json.dumps(self.calls[-1]))

    def test_api_validates_unknown_fields_nan_and_concurrent_editor_revision(self):
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_video_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        url = f"/api/videos/{self.video.video_id}/audio"
        payload = {"expected_revision": 0, "segments": [segment.model_dump() for segment in self.edits]}
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/videos/{'0' * 64}/audio").status_code, 404)
            self.assertEqual(client.put(f"/api/videos/{'0' * 64}/audio", json=payload).status_code, 404)
            self.assertEqual(client.get(url).json()["revision"], 0)
            self.assertEqual(client.put(url, json={**payload, "model": "override"}).status_code, 422)
            self.assertEqual(client.put(url, json=payload).status_code, 200)
            self.assertEqual(client.put(url, json=payload).status_code, 409)
            original = client.get(url).json()["original"]
            self.assertEqual(original["segments"][0]["text"], "打磨院")
        self.assertFalse(self.calls)

    def test_changed_original_is_detected_and_never_overwrites_correction(self):
        self.save()
        correction = (self.directory / "audio-corrections.json").read_bytes()
        self.raw_path.write_text(self.raw.model_dump_json() + "\n", encoding="utf-8")
        with self.assertRaises(VideoError):
            self.service.audio_record(self.video.video_id)
        self.assertEqual((self.directory / "audio-corrections.json").read_bytes(), correction)
