"""验证新模型回复的保守整理，不放宽持久化引用检查。"""
from copy import deepcopy
import unittest

import test_audio_evidence as audio_tests
from app.services.audio_evidence import AudioLink, normalize_model_links, validate_links
from app.services.deepseek import DeepSeekError
from app.services.videos import VideoService


class ModelNormalizationTests(unittest.TestCase):
    setUp = audio_tests.AudioChatTests.setUp
    def test_five_valid_links_survive_analysis_snapshot_and_cache(self):
        self.payload['audio_links'] = [{**self.link, 'detail': f'依据{i}'} for i in range(5)]
        result = self.videos.analyze(self.info.video_id)
        self.assertEqual(len(result.audio_links), 5)
        self.assertIn('已保存5条', result.evidence_notes[0])
        snapshot = self.videos._snapshot(self.videos.info(self.info.video_id), result)
        self.assertEqual(len(snapshot['audio_links']), 5)
        restored = self.videos.analyze(self.info.video_id)
        self.assertTrue(restored.cached)
        self.assertEqual(len(restored.audio_links), 5)
        self.assertEqual(len(self.calls), 1)

    def test_audio_only_valid_extra_candidate_cleared_without_visual_claim(self):
        self.payload['audio_links'] = [{'segment_id': 0, 'candidate_id': 0,
                                       'relation': 'audio_only', 'detail': '仅对白提到地点。'}]
        result = self.videos.analyze(self.info.video_id)
        link = result.audio_links[0]
        self.assertEqual(link.relation, 'audio_only')
        self.assertIsNone(link.candidate_id)
        self.assertIsNone(link.frame_index)
        self.assertIsNone(link.frame_time)
        self.assertIn('去除多余画面绑定', result.evidence_notes[0])
        self.assertEqual(len(self.calls), 1)

    def test_missing_audio_links_keeps_result_with_boundary_note(self):
        self.payload.pop('audio_links', None)
        result = self.videos.analyze(self.info.video_id)
        self.assertEqual(result.audio_links, [])
        self.assertTrue(any('未给出对白依据' in note for note in result.evidence_notes))
        self.assertLessEqual(len(result.evidence_notes), 8)
        self.assertEqual(len(self.calls), 1)
        # 缓存回读仍按严格规则校验，不因本轮放宽而改变旧记录要求。
        restored = VideoService(self.settings, self.client).analyze(self.info.video_id)
        self.assertTrue(restored.cached)
        self.assertEqual(restored.audio_links, [])

    def test_audio_prompt_requires_links_and_allows_no_speech_note(self):
        from app.services.audio_evidence import audio_prompt
        text = audio_prompt(self.audio, self.videos.info(self.info.video_id).frames)
        self.assertIn('audio_links', text)
        self.assertIn('画面已足够', text)
        self.assertIn('最多 8 条', text)
        self.assertIn('evidence_notes', text)

    def test_model_may_return_up_to_eight_evidence_notes(self):
        self.payload['evidence_notes'] = [f'说明{i}' for i in range(6)]
        result = self.videos.analyze(self.info.video_id)
        self.assertTrue(any(note.startswith('说明') for note in result.evidence_notes))
        self.assertLessEqual(len(result.evidence_notes), 8)

    def test_invalid_fifth_link_cannot_hide_behind_ui_limit(self):
        self.payload['audio_links'] = [deepcopy(self.link) for _ in range(4)] + [{**self.link, 'candidate_id': 0, 'frame_time': 0}]
        with self.assertRaises(DeepSeekError):
            self.videos.analyze(self.info.video_id)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(list(self.directory.glob('analysis-*.json')))

    def test_out_of_window_link_becomes_only_speech_and_visual_analysis_is_cached(self):
        self.payload['audio_links'] = [self.link, {'segment_id': 0, 'frame_index': 0, 'frame_time': 0,
            'relation': 'supports', 'content_visible': True, 'detail': '不应保留这条画面判断。'}]
        result = self.videos.analyze(self.info.video_id)
        self.assertEqual(result.audio_links[0].frame_time, 11)
        link = result.audio_links[1]
        self.assertEqual(link.relation, 'audio_only')
        self.assertEqual(link.segment_id, 0)
        self.assertFalse(link.content_visible)
        self.assertIsNone(link.frame_index)
        self.assertIsNone(link.frame_time)
        self.assertIsNone(link.candidate_id)
        self.assertNotIn('不应保留', link.detail)
        self.assertIn('未匹配', result.evidence_notes[0])
        self.assertEqual(len(result.observations), 2)
        restored = self.videos.analyze(self.info.video_id)
        self.assertTrue(restored.cached)
        self.assertEqual(restored.audio_links, result.audio_links)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_audio_only_candidate_and_conflicting_coordinates_stay_rejected(self):
        for fields in [{'candidate_id': 11}, {'candidate_id': 0, 'frame_time': 0, 'frame_index': 0}]:
            self.payload['audio_links'] = [{'segment_id': 0, 'relation': 'audio_only', 'detail': '越界', **fields}]
            with self.assertRaises(DeepSeekError):
                self.videos.analyze(self.info.video_id)
        self.assertEqual(len(self.calls), 2)

    def test_upper_bound_stays_bounded_and_cache_remains_strict(self):
        self.payload['audio_links'] = [deepcopy(self.link) for _ in range(65)]
        with self.assertRaises(DeepSeekError):
            self.videos.analyze(self.info.video_id)
        raw = AudioLink(segment_id=0, candidate_id=0, relation='audio_only', detail='仅对白')
        with self.assertRaises(ValueError):
            validate_links([raw], self.audio, self.videos.info(self.info.video_id).frames)
        normalized, _ = normalize_model_links([raw], self.audio, self.videos.info(self.info.video_id).frames)
        self.assertIsNotNone(raw.candidate_id)
        validate_links(normalized, self.audio, self.videos.info(self.info.video_id).frames)
