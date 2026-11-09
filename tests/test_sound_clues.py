from array import array
import math
from pathlib import Path
import sys
import tempfile
import unittest
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.audio_evidence import AudioTranscript, SpeechSegment
from app.services.sound_clues import measure_sound, sound_prompt


class SoundCluesTests(unittest.TestCase):
    def test_relative_levels_density_and_gap_are_derived_locally(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            values = array("h", (round(math.sin(i * 2 * math.pi * 440 / 16000) * (1000 if i < 16000 else 0 if i < 32000 else 10000)) for i in range(48000)))
            with wave.open(str(directory / "audio.wav"), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(values.tobytes())
            transcript = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=0, end=1, text="你好"), SpeechSegment(id=1, start=2, end=3, text="请看这片叶子")])
            result = measure_sound(directory, transcript)
            first, second = result["segments"]
            self.assertEqual(first["volume"], "quieter")
            self.assertEqual(second["volume"], "louder")
            self.assertAlmostEqual(second["relative_db"] - first["relative_db"], 20, delta=0.2)
            self.assertEqual(first["text_units_per_second"], 2)
            self.assertEqual(second["gap_before"], 1)
            self.assertIn("不据此断言", sound_prompt(result))

    def test_missing_or_invalid_wave_never_fabricates_clues(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            transcript = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=0, end=1, text="你好")])
            self.assertIsNone(measure_sound(directory, transcript))
            (directory / "audio.wav").write_bytes(b"invalid")
            self.assertIsNone(measure_sound(directory, transcript))

    def test_one_segment_does_not_claim_comparative_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with wave.open(str(directory / "audio.wav"), "wb") as output:
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                output.writeframes(array("h", [1000] * 16000).tobytes())
            transcript = AudioTranscript(status="ready", segments=[SpeechSegment(id=0, start=0, end=1, text="你好")])
            row = measure_sound(directory, transcript)["segments"][0]
            self.assertEqual(row["volume"], "not_compared")
            self.assertIsNone(row["gap_before"])
