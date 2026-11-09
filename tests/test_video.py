"""使用实际 H.264 解码与模拟模型，验证采样、证据、缓存和失败费用边界。"""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import av
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.api.routes.videos import get_video_service
from app.core.config import Settings, get_settings
from app.core.upload_limits import ImageUploadBodyLimit
from app.main import app
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.videos import ANALYSIS_LOCK, VideoError, VideoService


def make_video(path, *, seconds=6, static=False, rotation=0, fps=10, varying=False, revisit=False):
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 96, 64, "yuv420p"
        stream.options = {"crf": "0" if revisit else "18", "preset": "ultrafast"}
        if rotation:
            stream.set_display_rotation(rotation)
        for index in range(seconds * fps):
            color = (index % 200, 50 + (index // 200) * 20, 255 - index % 200) if varying else "red" if static or index < seconds * fps / 2 else "blue"
            if revisit:
                color = "red" if (index // fps) % 2 == 0 else "blue"
            image = Image.new("RGB", (96, 64), color)
            for packet in stream.encode(av.VideoFrame.from_image(image)):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def result_payload(frame=1):
    return {"observations": [
        {"frame_index": 0, "keywords": ["红色画面"], "description": "画面主要为红色。", "uncertain": False},
        {"frame_index": frame, "keywords": ["蓝色画面"], "description": "随后出现蓝色画面。", "uncertain": False},
    ], "summary": "先红色，后蓝色。", "reply": "镜头像是换了一件蓝色外套！"}


class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix="visionsoul-video-fixtures-")
        cls.addClassCleanup(cls.fixtures.cleanup)
        path = Path(cls.fixtures.name) / "two-scenes.mp4"
        make_video(path)
        cls.data = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-video-test-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, video_storage_dir=Path(self.temp.name) / "videos",role_database_path=Path(self.temp.name)/"roles.sqlite3",
                                 deepseek_api_key="test-not-a-real-key")
        self.calls = []
        self.payload = result_payload()
        self.upstream_status = 200

        def handler(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(self.upstream_status, json={"model": "deepseek-flash", "choices": [
                {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600}})

        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.service = VideoService(self.settings, self.client)

    def uploaded(self):
        return self.service.upload(BytesIO(self.data))

    def test_real_decode_order_and_read_only_does_not_call_model(self):
        video = self.uploaded()
        self.assertEqual(video.duration_seconds, 6)
        self.assertEqual(video.sample_count, 7)
        self.assertEqual(video.frames[0].time_seconds, 0)
        self.assertAlmostEqual(video.frames[1].time_seconds, 3, delta=0.2)
        self.assertEqual(len(self.calls), 0)
        directory = self.settings.video_storage_dir / video.video_id
        for index, channel in [(0, 0), (1, 2)]:
            with Image.open(directory / f"frame-{index}.jpg") as image:
                self.assertGreater(image.getpixel((20, 20))[channel], 220)

    def test_static_exact_duplicates_are_merged_with_times(self):
        path=Path(self.temp.name)/'static.mp4';make_video(path,seconds=12,static=True)
        video=self.service.upload(BytesIO(path.read_bytes()))
        self.assertEqual(video.sample_count,13)
        self.assertEqual(len(video.frames),1)
        self.assertEqual(len(video.frames[0].seen_at),13)
        self.assertEqual(video.frames[0].seen_at[-1],11.9)

    def test_one_second_samples_and_actual_tail_for_10_20_30_seconds(self):
        for seconds in [10,20,30]:
            with self.subTest(seconds=seconds):
                path=Path(self.temp.name)/f'{seconds}-seconds.mp4';make_video(path,seconds=seconds,varying=True)
                video=self.service.upload(BytesIO(path.read_bytes()))
                self.assertEqual(video.sample_count,seconds+1)
                self.assertEqual(len(video.frames),seconds+1)
                times=[frame.time_seconds for frame in video.frames]
                self.assertEqual(times[:-1],list(range(seconds)))
                self.assertEqual(times[-1],seconds-.1)
                self.assertFalse(self.calls)

    def test_fewer_than_30_source_frames_are_not_duplicated(self):
        path = Path(self.temp.name) / "low-fps.mp4"
        make_video(path, seconds=3, fps=2, varying=True)
        video = self.service.upload(BytesIO(path.read_bytes()))
        self.assertEqual(video.sample_count, 4)
        self.assertEqual([frame.time_seconds for frame in video.frames], [0, 1, 2, 2.5])

    def test_identical_reappearing_picture_is_sent_once_with_all_occurrences(self):
        path=Path(self.temp.name)/'revisit.mp4';make_video(path,seconds=3,revisit=True)
        video=self.service.upload(BytesIO(path.read_bytes()))
        self.assertEqual(video.sample_count,4)
        self.assertEqual(len(video.frames),2)
        self.assertEqual(video.frames[0].seen_at,[0,2,2.9])
        self.assertEqual(video.frames[1].seen_at,[1])
        self.assertFalse(self.calls)

    def test_one_fps_never_duplicates_source_frames_to_fill_half_second_targets(self):
        path=Path(self.temp.name)/'one-fps.mp4';make_video(path,seconds=3,fps=1,varying=True)
        video=self.service.upload(BytesIO(path.read_bytes()))
        self.assertEqual(video.sample_count,3)
        self.assertEqual([frame.time_seconds for frame in video.frames],[0,1,2])

    def test_old_thirty_frame_env_limit_upgrades_and_custom_small_limit_never_truncates(self):
        self.assertEqual(Settings(_env_file=None,video_max_frames=30).video_max_frames,31)
        self.assertEqual(Settings(_env_file=None,video_max_frames=61).video_max_frames,31)
        limited=VideoService(self.settings.model_copy(update={'video_max_frames':2}),self.client)
        with self.assertRaisesRegex(VideoError,'上限'):
            limited.upload(BytesIO(self.data))
        self.assertFalse(list(self.settings.video_storage_dir.iterdir()))

    def test_previous_uniform_pipeline_still_restores_without_model_call(self):
        video=self.uploaded();path=self.settings.video_storage_dir/video.video_id/'manifest.json'
        data=json.loads(path.read_text(encoding='utf-8'));data['pipeline_version']='video-v2-uniform-30-exact-dedup'
        path.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(self.service.info(video.video_id).sampling_policy,'legacy_uniform')
        self.assertFalse(self.calls)

    def test_over_30_seconds_is_rejected_before_model_use(self):
        path = Path(self.temp.name) / "too-long.mp4"
        make_video(path, seconds=31, fps=2)
        with self.assertRaises(VideoError) as error:
            self.service.upload(BytesIO(path.read_bytes()))
        self.assertIn("30", str(error.exception))
        self.assertFalse(self.calls)

    def test_legacy_sampling_record_requires_reupload_and_preserves_files(self):
        video = self.uploaded()
        manifest = self.settings.video_storage_dir / video.video_id / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data.pop("pipeline_version")
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(VideoError) as error:
            self.service.analyze(video.video_id)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("重新选择", str(error.exception))
        self.assertTrue(manifest.with_name("source.mp4").is_file())
        self.assertFalse(self.calls)

    def test_rotation_and_frame_budget(self):
        path = Path(self.temp.name) / "rotated.mp4"
        make_video(path, seconds=12, rotation=90)
        settings = self.settings
        video = VideoService(settings).upload(BytesIO(path.read_bytes()))
        self.assertEqual(video.sample_count, 13)
        with Image.open(settings.video_storage_dir / video.video_id / "frame-0.jpg") as image:
            self.assertEqual(image.size, (64, 96))

    def test_one_request_low_detail_json_and_cached_across_service_restart(self):
        video = self.uploaded()
        result = self.service.analyze(video.video_id)
        self.assertFalse(result.cached)
        self.assertEqual(result.usage.total_tokens, 600)
        self.assertEqual(len(self.calls), 1)
        sent = self.calls[0]
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(sent["max_tokens"], 1600)
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(len(sent["messages"]), 2)
        images = [part for part in sent["messages"][1]["content"] if part["type"] == "image_url"]
        self.assertEqual(len(images), 2)
        self.assertTrue(all(part["image_url"]["detail"] == "low" for part in images))
        restored = VideoService(self.settings, self.client).analyze(video.video_id)
        self.assertTrue(restored.cached)
        self.assertEqual(restored.reply, result.reply)
        self.assertTrue(self.service.upload(BytesIO(self.data)).result.cached)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(list(self.settings.video_storage_dir.iterdir())), 1)

    def test_bad_structure_or_unknown_evidence_never_cached_or_auto_retried(self):
        video = self.uploaded()
        for payload in [result_payload(19), result_payload(0), {"reply": "不完整"},
                        {**result_payload(), "observations": [{**result_payload()["observations"][0], "keywords": [""]}]}]:
            self.payload = payload
            count = len(self.calls)
            with self.assertRaises(DeepSeekError) as error:
                self.service.analyze(video.video_id)
            self.assertEqual(error.exception.status_code, 502)
            self.assertEqual(len(self.calls), count + 1)
            self.assertIsNone(self.service.info(video.video_id).result)

    def test_provider_failure_does_not_cache_or_retry(self):
        video = self.uploaded()
        self.upstream_status = 401
        with self.assertRaises(DeepSeekError) as error:
            self.service.analyze(video.video_id)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.service.info(video.video_id).result)

    def test_same_keyword_new_visual_feature_is_preserved_for_comparison(self):
        video = self.uploaded()
        self.payload["observations"][1]["keywords"] = ["红色画面"]
        result = self.service.analyze(video.video_id)
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.observations[0].frame_index, 0)

    def test_new_upload_stores_detail_frames_and_api_serves_them(self):
        video=self.uploaded()
        self.assertTrue(all(frame.detail_url and frame.clarity is not None for frame in video.frames))
        root=self.settings.video_storage_dir/video.video_id
        self.assertTrue((root/'detail-0.jpg').is_file())
        app.dependency_overrides[get_settings]=lambda:self.settings
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            self.assertEqual(client.get(video.frames[0].detail_url).status_code,200)
            self.assertEqual(client.get(f'/api/videos/{video.video_id}/frames/29/detail').status_code,404)
        self.assertFalse(self.calls)

    def test_concurrent_analysis_is_blocked_but_cache_is_readable(self):
        video = self.uploaded()
        with ANALYSIS_LOCK:
            with self.assertRaises(VideoError) as error:
                self.service.analyze(video.video_id)
            self.assertEqual(error.exception.status_code, 409)
        self.assertFalse(self.calls)
        self.service.analyze(video.video_id)
        with ANALYSIS_LOCK:
            self.assertTrue(self.service.analyze(video.video_id).cached)
        self.assertEqual(len(self.calls), 1)

    def test_configuration_change_invalidates_cache(self):
        video = self.uploaded()
        self.service.analyze(video.video_id)
        changed = self.settings.model_copy(update={"video_max_tokens": 1200})
        self.assertIsNone(VideoService(changed, self.client).info(video.video_id).result)

    def test_rejects_fake_empty_too_large_and_long_without_leftovers(self):
        for data in [b"", b"not a video"]:
            with self.assertRaises(VideoError):
                self.service.upload(BytesIO(data))
        small = self.settings.model_copy(update={"video_max_bytes": 10})
        with self.assertRaises(VideoError) as error:
            VideoService(small).upload(BytesIO(self.data))
        self.assertEqual(error.exception.status_code, 413)
        short = self.settings.model_copy(update={"video_max_seconds": 2.0})
        with self.assertRaises(VideoError):
            VideoService(short).upload(BytesIO(self.data))
        self.assertFalse(list(self.settings.video_storage_dir.iterdir()))
        self.assertFalse(self.calls)

    def test_decode_timeout_cleans_temporary_upload(self):
        with patch("app.services.videos.subprocess.run", side_effect=subprocess.TimeoutExpired("decode", 1)):
            with self.assertRaises(VideoError) as error:
                self.uploaded()
        self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(list(self.settings.video_storage_dir.iterdir()))

    def test_missing_key_and_path_traversal_do_not_call_model(self):
        video = self.uploaded()
        settings = self.settings.model_copy(update={"deepseek_api_key": Settings(_env_file=None, deepseek_api_key="").deepseek_api_key})
        with self.assertRaises(DeepSeekError):
            VideoService(settings, self.client).analyze(video.video_id)
        with self.assertRaises(VideoError):
            self.service.info("../outside")
        self.assertFalse(self.calls)

    def test_api_upload_playback_frames_analysis_and_restore(self):
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_video_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            response = client.post("/api/videos", files={"file": ("../../outside.mp4", self.data, "text/plain")})
            self.assertEqual(response.status_code, 201, response.text)
            video = response.json()
            self.assertFalse(self.calls)
            playable = client.get(video["video_url"], headers={"Range": "bytes=0-15"})
            self.assertEqual(playable.status_code, 206)
            self.assertEqual(playable.content, self.data[:16])
            self.assertEqual(client.get(video["frames"][0]["url"]).status_code, 200)
            self.assertEqual(client.get(f"/api/videos/{video['video_id']}/frames/19").status_code, 404)
            self.assertEqual(client.post("/api/videos/analyze", json={"video_id": video["video_id"], "system": "override"}).status_code, 422)
            self.assertEqual(client.post("/api/videos/analyze", json={"video_id": video["video_id"]}).status_code, 200)
            restored = client.get(f"/api/videos/{video['video_id']}").json()
            self.assertTrue(restored["result"]["cached"])
            self.assertEqual(len(self.calls), 1)

    def test_last_of_31_frames_is_served_and_valid_model_evidence(self):
        path = Path(self.temp.name) / "30-distinct.mp4"
        make_video(path, seconds=30, varying=True)
        video = self.service.upload(BytesIO(path.read_bytes()))
        self.payload = result_payload(30)
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_video_service] = lambda: self.service
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            self.assertEqual(client.get(video.frames[30].url).status_code, 200)
            response = client.post("/api/videos/analyze", json={"video_id": video.video_id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["observations"][-1]["frame_index"], 30)
            images = [part for part in self.calls[0]["messages"][1]["content"] if part["type"] == "image_url"]
            self.assertEqual(len(images), 31)
            self.assertEqual(len(self.calls), 1)

    def test_video_request_body_limit_before_multipart(self):
        limited = ImageUploadBodyLimit(app, max_bytes=1024, video_max_bytes=2048)
        with TestClient(limited) as client:
            response = client.post("/api/videos", content=b"x" * 2049)
            self.assertEqual(response.status_code, 413)
            self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
