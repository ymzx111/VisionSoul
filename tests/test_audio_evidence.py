"""时间轴、窗口边界、证据引用、缓存与话题记忆；不调用真实API/下载模型。"""
from fractions import Fraction
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import httpx
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings
from app.services.audio_evidence import AudioLink, AudioTranscript, SpeechSegment, SpeechWord, audio_prompt, frame_candidates, validate_links, add_time_citations, model_context
from app.services.audio_extract import decode_timeline, programming_languages, retry_uncertain_speech, speech_segments, transcribe
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.videos import SampleFrame, VideoService, VideoError
from test_video import make_video, result_payload


def timeline_video(path, audio_start=2, silent=False):
    """5秒视频，音轨真实PTS延后2秒；不用插入假静音冒充轨道偏移。"""
    with av.open(str(path), "w", format="mp4") as output:
        video = output.add_stream("libx264", rate=2)
        video.width, video.height, video.pix_fmt = 96, 64, "yuv420p"
        video.options = {"preset": "ultrafast"}
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(10):
            for packet in video.encode(av.VideoFrame.from_image(Image.new("RGB", (96, 64), "red"))):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        values = np.zeros(16000, dtype=np.int16) if silent else (np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 12000).astype(np.int16)
        for start in range(0, len(values), 1024):
            frame = av.AudioFrame.from_ndarray(values[start:start + 1024][None, :], format="s16", layout="mono")
            frame.sample_rate, frame.time_base, frame.pts = 16000, Fraction(1, 16000), round(audio_start * 16000) + start
            for packet in audio.encode(frame):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)


class AlignmentTests(unittest.TestCase):
    def test_requested_citation_is_derived_from_original_not_invented_model_quote(self):
        audio = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=10, end=10.3, text="原始对白")])
        link = AudioLink(segment_id=0, frame_index=0, frame_time=11, relation="uncertain", detail="待确认")
        reply = add_time_citations("简短回答", "为什么，依据在哪里", audio, [link])
        self.assertIn('10—10.3秒：“原始对白”', reply)
        self.assertIn("画面11秒（关联待确认）", reply)
        self.assertEqual(add_time_citations("普通接话", "好漂亮", audio, [link]), "普通接话")
    def test_boundaries_and_primary_secondary_are_relative_to_whole_utterance(self):
        speech = SpeechSegment(id=0, start=10, end=12, text="观察叶片")
        frames = [SampleFrame(index=i, time_seconds=t, seen_at=[t], url="") for i, t in enumerate([6.9, 7, 8.5, 10, 12, 13.5, 15, 15.1])]
        rows = frame_candidates(speech, frames)
        self.assertEqual({row["frame_time"] for row in rows if row["window"] == "primary"}, {8.5, 10, 12, 13.5})
        self.assertEqual({row["frame_time"] for row in rows if row["window"] == "secondary"}, {7, 15})

    def test_reappearing_identical_frame_uses_actual_occurrence_not_first_time(self):
        speech = SpeechSegment(id=0, start=10, end=10.3, text="这片叶子")
        frame = SampleFrame(index=0, time_seconds=0, seen_at=[0, 11], url="")
        link = AudioLink(segment_id=0, frame_index=0, frame_time=11, relation="supports", detail="可见叶片")
        validate_links([link], AudioTranscript(status="ready", segments=[speech]), [frame])
        self.assertEqual(frame_candidates(speech, [frame])[0]["frame_time"], 11)

    def test_invalid_quote_reference_time_and_audio_only_are_rejected(self):
        audio = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=10, end=10.3, text="这片叶子")])
        frames = [SampleFrame(index=0, time_seconds=11, seen_at=[11], url="")]
        for link in [AudioLink(segment_id=1, relation="audio_only", detail="未知对白"),
                     AudioLink(segment_id=0, frame_index=0, frame_time=11.1, relation="supports", detail="伪造时间"),
                     AudioLink(segment_id=0, frame_index=0, frame_time=11, relation="audio_only", detail="伪造来源")]:
            with self.assertRaises(ValueError):
                validate_links([link], audio, frames)
        with self.assertRaises(ValueError):
            validate_links([], audio, frames)

    def test_transcript_rejects_nan_nonmonotonic_and_wrong_status(self):
        for payload in [{"status": "ready", "segments": []}, {"status": "no_speech", "segments": [{"id": 0, "start": 0, "end": 1, "text": "词"}]},
                        {"status": "ready", "segments": [{"id": 0, "start": float("nan"), "end": 1, "text": "词"}]}]:
            with self.assertRaises(ValueError):
                AudioTranscript.model_validate(payload)

    def test_word_times_split_long_speech_and_keep_vad_restored_original_times(self):
        word = lambda start, end, text: SimpleNamespace(start=start, end=end, word=text, probability=0.42)
        rows = speech_segments([SimpleNamespace(no_speech_prob=0, avg_logprob=-0.1,
            words=[word(10, 12, "请看"), word(12, 14.2, "这里"), word(14.3, 15, "有叶子。")])], 16)
        self.assertEqual([(row.start, row.end) for row in rows], [(10, 14.2), (14.3, 15)])
        self.assertEqual(rows[0].text, "请看这里")
        self.assertEqual([(word.start, word.end, word.score) for word in rows[0].words],
                         [(10, 12, 0.42), (12, 14.2, 0.42)])

    def test_retry_only_reprocesses_one_task_window_and_keeps_both_results(self):
        def segment(index, start, token, low, score):
            return SpeechSegment(id=index, start=start, end=start + 2, text=token + low, words=[
                SpeechWord(start=start, end=start + 1, text=token, score=0.9),
                SpeechWord(start=start + 1, end=start + 2, text=low, score=score),
            ])

        ordinary = segment(0, 1, "沿着湖边", "散补", 0.1)
        secondary = segment(1, 4, "请看这道题", "呀", 0.3)
        task = segment(2, 8, "代码", "振兴补权", 0.41)
        later_task = segment(3, 12, "题目要求", "呲语", 0.45)
        audio = AudioTranscript(status="ready", segments=[ordinary, secondary, task, later_task])
        model = SimpleNamespace()
        samples = np.arange(16 * 16000, dtype=np.float32)
        with patch.object(model, "transcribe", create=True, return_value=(
                iter([SimpleNamespace(text="代码进行补全，用 C 语言。")]), None)) as run:
            retry_uncertain_speech(model, samples, AudioTranscript(status="ready", segments=[ordinary]))
            run.assert_not_called()
            retry_uncertain_speech(model, samples, audio)
            run.assert_called_once()
            np.testing.assert_array_equal(run.call_args.args[0], samples[120000:168000])
            self.assertIn("C 语言", run.call_args.kwargs["initial_prompt"])

        self.assertEqual(task.text, "代码振兴补权")
        self.assertEqual((task.retry.start, task.retry.end), (7.5, 10.5))
        self.assertTrue(all(row.retry is None for row in (ordinary, secondary, later_task)))
        restored = AudioTranscript.model_validate_json(audio.model_dump_json())
        prompt = audio_prompt(restored, [])
        self.assertIn("低分：振兴补权@9", prompt)
        self.assertIn("同音轨复识别候选 7.5–10.5秒：代码进行补全，用 C 语言。", prompt)
        saved = model_context({"audio": restored.model_dump()})["audio"]["segments"][2]
        self.assertNotIn("words", saved)
        self.assertEqual(saved["low_score_words"][0]["text"], "振兴补权")

    def test_retry_checks_high_scoring_language_misread_across_segments(self):
        preceding = SpeechSegment(id=0, start=0.3, end=4.9, text="把黄色的这一道题")
        task = SpeechSegment(id=1, start=5.1, end=7.2, text="去用C远镜实现", words=[
            SpeechWord(text="C", start=5.4, end=5.6, score=0.77),
            SpeechWord(text="远镜", start=5.6, end=6.2, score=0.59),
        ])
        samples = np.arange(10 * 16000, dtype=np.float32)
        audio = AudioTranscript(status="ready", segments=[preceding, task])
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True, return_value=(
                iter([SimpleNamespace(text="去用 C 语言实现。")]), None)) as run:
            retry_uncertain_speech(model, samples, audio)
            run.assert_called_once()
            np.testing.assert_array_equal(run.call_args.args[0], samples[73600:123200])
            self.assertIn("C 语言", run.call_args.kwargs["initial_prompt"])
        self.assertEqual(task.text, "去用C远镜实现")
        self.assertEqual(task.retry.text, "去用 C 语言实现。")

    def test_valid_technical_phrases_and_unrelated_speech_do_not_trigger(self):
        preceding = SpeechSegment(id=0, start=0, end=4, text="把这一道题")
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True) as run:
            for phrase in ("用 C 语言实现", "用 C 数组实现", "用 C++ 标准库实现", "用 Python 来实现",
                           "用 C 加加实现", "用 C 井号实现", "用远镜实现", "用 Canva 完成"):
                with self.subTest(phrase=phrase):
                    row = SpeechSegment(id=1, start=4, end=7, text=phrase)
                    audio = AudioTranscript(status="ready", segments=[preceding, row])
                    retry_uncertain_speech(model, np.zeros(160000), audio)
            for preceding in (SpeechSegment(id=0, start=0, end=2, text="我看了一场展览"),
                              SpeechSegment(id=0, start=0, end=1, text="有一道题")):
                row = SpeechSegment(id=1, start=4, end=7, text="用 C 远镜实现")
                audio = AudioTranscript(status="ready", segments=[preceding, row])
                retry_uncertain_speech(model, np.zeros(160000), audio)
            run.assert_not_called()

    def test_programming_language_names_preserve_symbols_and_spoken_aliases(self):
        cases = {
            "用 C 语言完成": {"c"},
            "用 C 写": {"c"},
            "用Ｃ＋＋、C 加加和 C plus plus": {"c++"},
            "C#、C 井号和 C sharp": {"c#"},
            "用 C 语言实现，另给 C++ 对照": {"c", "c++"},
            "Python 和 JavaScript，另有 Java": {"python", "javascript", "java"},
            "用 C 区完成，文档提到 JAVASCRIPTING": set(),
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(programming_languages(phrase), expected)

    def test_retry_cannot_substitute_or_remove_a_known_language(self):
        candidates = ("请用 C 语言实现代码。", "请用 Python 实现代码。", "请完成代码。", "请用 C++ 和 C# 实现代码。")
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                row = SpeechSegment(id=0, start=0.5, end=3, text="请用 C 加加实现代妈", words=[
                    SpeechWord(text="请用 C 加加实现代码", start=0.5, end=2, score=0.91),
                    SpeechWord(text="代妈", start=2, end=3, score=0.31),
                ])
                model = SimpleNamespace()
                with patch.object(model, "transcribe", create=True, return_value=(
                        iter([SimpleNamespace(text=candidate)]), None)) as run:
                    retry_uncertain_speech(model, np.zeros(64000), AudioTranscript(status="ready", segments=[row]))
                    run.assert_called_once()
                    self.assertIn("C++", run.call_args.kwargs["initial_prompt"])
                    self.assertNotIn("请用 C 语言", run.call_args.kwargs["initial_prompt"])
                self.assertIsNone(row.retry)

    def test_retry_uses_adjacent_explicit_language_to_avoid_injecting_c(self):
        preceding = SpeechSegment(id=0, start=0, end=1.7, text="这道题用 C++")
        row = SpeechSegment(id=1, start=2, end=4, text="用 C 远镜实现")
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True, return_value=(
                iter([SimpleNamespace(text="请用 C 语言实现。")]), None)) as run:
            retry_uncertain_speech(model, np.zeros(96000), AudioTranscript(status="ready", segments=[preceding, row]))
            run.assert_called_once()
            self.assertIn("C++", run.call_args.kwargs["initial_prompt"])
        self.assertIsNone(row.retry)

    def test_retry_accepts_equivalent_cpp_spelling_with_task_correction(self):
        row = SpeechSegment(id=0, start=1, end=3, text="用 C 加加写代妈", words=[
            SpeechWord(text="用 C 加加写代码", start=1, end=2, score=0.92),
            SpeechWord(text="代妈", start=2, end=3, score=0.45),
        ])
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True, return_value=(
                iter([SimpleNamespace(text="用 C++ 写代码。")]), None)):
            retry_uncertain_speech(model, np.zeros(64000), AudioTranscript(status="ready", segments=[row]))
        self.assertEqual(row.retry.text, "用 C++ 写代码。")

    def test_retry_discards_candidate_that_loses_known_domain(self):
        segment = SpeechSegment(id=0, start=1, end=3, text="请你编个代码", words=[
            SpeechWord(text="请你", start=1, end=2, score=0.9),
            SpeechWord(text="编个代码", start=2, end=3, score=0.4),
        ])
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True, return_value=(
                iter([SimpleNamespace(text="请你评个电话。")]), None)) as run:
            retry_uncertain_speech(model, np.zeros(64000), AudioTranscript(status="ready", segments=[segment]))
            run.assert_called_once()
        self.assertIsNone(segment.retry)

    def test_optional_retry_failure_keeps_initial_transcript(self):
        audio = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=0, end=2,
            text="请看代码补权", words=[SpeechWord(text="请看代码", start=0, end=1, score=0.92),
                                      SpeechWord(text="补权", start=1, end=2, score=0.4)])])
        model = SimpleNamespace()
        with patch.object(model, "transcribe", create=True, side_effect=RuntimeError) as run:
            retry_uncertain_speech(model, np.zeros(32000), audio)
            run.assert_called_once()
        self.assertEqual(audio.segments[0].text, "请看代码补权")
        self.assertIsNone(audio.segments[0].retry)

    def test_real_audio_pts_offset_and_silence_remain_on_video_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.mp4"
            timeline_video(path)
            samples = decode_timeline(path, 5)
            self.assertLess(float(np.max(np.abs(samples[:int(1.8 * 16000)]))), 0.001)
            self.assertGreater(float(np.std(samples[int(2.1 * 16000):int(2.8 * 16000)])), 0.1)
            self.assertLess(float(np.max(np.abs(samples[int(3.2 * 16000):]))), 0.001)
            self.assertEqual(len(samples), 5 * 16000)

    def test_no_audio_silent_and_missing_model_have_explicit_states(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "manifest.json").write_text('{"duration_seconds":5}', encoding="utf-8")
            make_video(directory / "source.mp4", seconds=5)
            self.assertEqual(transcribe(directory, directory / "missing", 1).status, "no_audio")
            timeline_video(directory / "source.mp4", silent=True)
            self.assertEqual(transcribe(directory, directory / "missing", 1).status, "no_speech")
            timeline_video(directory / "source.mp4")
            self.assertEqual(transcribe(directory, directory / "missing", 1).status, "unavailable")


class AudioChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        path = root / "source.mp4"
        make_video(path, seconds=14, varying=True)
        self.settings = Settings(_env_file=None, video_storage_dir=root / "videos", chat_database_path=root / "chat.sqlite3",
                                 deepseek_api_key="test-not-a-real-key")
        self.calls = []
        self.link = {"segment_id": 0, "frame_index": 11, "frame_time": 11, "relation": "uncertain", "detail": "时间相近，画面不足以确认对白。"}
        self.payload = {**result_payload(), "audio_links": [self.link]}
        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            content = json.dumps(self.payload) if body.get("response_format") else "10秒处对白提到叶片，11秒画面仍不足以确认。"
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {"content": content}, "finish_reason": "stop"}]})
        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.videos = VideoService(self.settings, self.client)
        with path.open("rb") as source:
            self.info = self.videos.upload(source)
        self.directory = self.settings.video_storage_dir / self.info.video_id
        self.audio = AudioTranscript(status="ready", message="测试转写", language="zh",
            segments=[SpeechSegment(id=0, start=10, end=10.3, text="看看这片叶子的边缘。")])
        (self.directory / "audio.json").write_text(self.audio.model_dump_json(), encoding="utf-8")

    def test_first_call_contains_candidates_and_followup_remembers_quote_without_images(self):
        chat = ChatService(self.settings, self.client, self.videos)
        first = chat.send(conversation_id=None, request_id="a" * 32, message="看看这段视频", image_id=None, video_id=self.info.video_id)
        self.assertEqual(len(self.calls), 1)
        prompt = json.dumps(self.calls[0], ensure_ascii=False)
        self.assertIn("S0@10-10.3", prompt)
        self.assertIn("11@11", prompt)
        self.assertIn("时间接近不等于", prompt)
        self.assertEqual(first["video_context"]["audio"]["segments"][0]["start"], 10)
        self.assertEqual(first["video_context"]["audio_links"][0]["frame_time"], 11)
        body = dict(conversation_id=first["conversation_id"], request_id="b" * 32, message="为什么这样判断，依据在哪里？", image_id=None)
        second = ChatService(self.settings, self.client).send(**body)
        self.assertEqual(len(self.calls), 2)
        followup = json.dumps(self.calls[1], ensure_ascii=False)
        self.assertNotIn("image_url", followup)
        self.assertIn(self.audio.segments[0].text, followup)
        self.assertIn("原时间范围", followup)
        self.assertTrue(chat.send(**body)["replayed"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second["video_context"], first["video_context"])
        self.assertIn('10—10.3秒：“看看这片叶子的边缘。”', second["assistant_message"]["content"])

    def test_first_visual_answer_also_gets_requested_original_citation(self):
        answer = self.videos._observe(self.videos.info(self.info.video_id), "依据在哪里？")
        self.assertIn('10—10.3秒：“看看这片叶子的边缘。”', answer.reply)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_audio_link_does_not_cache_or_retry_or_save_half_chat(self):
        self.link["segment_id"] = 1
        chat = ChatService(self.settings, self.client, self.videos)
        with self.assertRaises(DeepSeekError):
            chat.send(conversation_id=None, request_id="c" * 32, message="依据呢", image_id=None, video_id=self.info.video_id)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(chat.store.recent("video"), [])

    def test_audio_cache_reuses_and_changed_transcript_invalidates_without_auto_api(self):
        answer = self.videos.analyze(self.info.video_id)
        self.assertFalse(answer.cached)
        self.assertTrue(self.videos.analyze(self.info.video_id).cached)
        self.assertEqual(len(self.calls), 1)
        self.audio.segments[0].text = "用户校对的新对白"
        (self.directory / "audio.json").write_text(self.audio.model_dump_json(), encoding="utf-8")
        self.assertIsNone(self.videos.info(self.info.video_id).result)
        self.assertEqual(len(self.calls), 1)

    def test_audio_timeout_falls_back_locally_and_status_never_calls_model(self):
        with patch("app.services.videos.subprocess.run", side_effect=subprocess.TimeoutExpired("audio", 1)):
            self.videos._prepare_audio(self.directory)
        self.assertEqual(self.videos.info(self.info.video_id).audio.status, "failed")
        self.assertFalse(self.calls)

    def test_legacy_half_second_record_and_its_cached_result_remain_readable(self):
        manifest_path = self.directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["pipeline_version"] = "video-v3-half-second-exact-global-dedup"
        manifest["sample_interval_seconds"] = 0.5
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (self.directory / "audio.json").unlink()
        self.payload.pop("audio_links")
        self.videos.analyze(self.info.video_id)
        restored = VideoService(self.settings, self.client).info(self.info.video_id)
        self.assertEqual(restored.sampling_policy, "half_second")
        self.assertEqual(restored.audio.status, "legacy")
        self.assertTrue(restored.result.cached)
        self.assertEqual(len(self.calls), 1)
