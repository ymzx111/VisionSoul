"""验证新模型回复的保守整理，不放宽持久化引用检查。"""
from copy import deepcopy
import unittest

import test_audio_evidence as audio_tests
from app.services.audio_evidence import AudioLink, normalize_model_links, validate_links
from app.services.deepseek import DeepSeekError


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

    def test_invalid_fifth_link_cannot_hide_behind_ui_limit(self):
        self.payload['audio_links'] = [deepcopy(self.link) for _ in range(4)] + [{**self.link, 'frame_time': 0}]
        with self.assertRaises(DeepSeekError):
            self.videos.analyze(self.info.video_id)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(list(self.directory.glob('analysis-*.json')))

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
