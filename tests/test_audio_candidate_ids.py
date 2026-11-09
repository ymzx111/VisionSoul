import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.audio_evidence import AudioLink, AudioTranscript, SpeechSegment, validate_links
from app.services.videos import SampleFrame


class CandidateIDTests(unittest.TestCase):
    def setUp(self):
        self.audio = AudioTranscript(status="ready", segments=[SpeechSegment(id=0,start=10,end=10.3,text="叶片")])
        self.frames = [SampleFrame(index=0,time_seconds=0,seen_at=[0,11],url="")]

    def test_candidate_id_binds_real_occurrence_time_and_cached_resolved_link_is_valid(self):
        link = AudioLink(segment_id=0,candidate_id=0,relation="uncertain",detail="待确认")
        validate_links([link],self.audio,self.frames)
        self.assertEqual((link.frame_index,link.frame_time),(0,11))
        validate_links([AudioLink.model_validate_json(link.model_dump_json())],self.audio,self.frames)

    def test_unknown_candidate_or_conflicting_explicit_time_never_silently_repaired(self):
        for link in [AudioLink(segment_id=0,candidate_id=1,relation="uncertain",detail="未知"),
                     AudioLink(segment_id=0,candidate_id=0,frame_index=0,frame_time=0,relation="supports",detail="冲突"),
                     AudioLink(segment_id=0,candidate_id=0,relation="audio_only",detail="纯对白")]:
            with self.assertRaises(ValueError):
                validate_links([link],self.audio,self.frames)
