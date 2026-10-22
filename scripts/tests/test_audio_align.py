import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audio_align import align_segment

WINDOW = 0.2


def envelope(levels: list[tuple[float, float, float]]) -> np.ndarray:
    """Build a dB envelope: list of (start_s, end_s, db)."""
    frames = int(max(e for _, e, _ in levels) / WINDOW) + 1
    db = np.full(frames, -45.0)
    for start, end, level in levels:
        i0, i1 = int(start / WINDOW), int(end / WINDOW)
        db[i0:i1] = level
    return db


class TestAudioAlign(unittest.TestCase):
    def test_align_segment_drops_silent_tail(self):
        db = envelope([(0.0, 2.0, -20.0)])
        seg = {"start": 0.0, "end": 9.0, "text": "hello"}
        out = align_segment(seg, db, -35.0)
        self.assertLess(out["end"], 3.0)
        self.assertEqual(out["start"], 0.0)

    def test_align_segment_keeps_speech_cover(self):
        db = envelope([(0.0, 9.0, -20.0)])
        seg = {"start": 0.0, "end": 9.0, "text": "hello"}
        out = align_segment(seg, db, -35.0)
        self.assertEqual((out["start"], out["end"]), (0.0, 9.0))

    def test_align_segment_keeps_cue_without_speech(self):
        db = envelope([(0.0, 1.0, -20.0)])
        seg = {"start": 5.0, "end": 9.0, "text": "hello"}
        out = align_segment(seg, db, -35.0)
        self.assertEqual((out["start"], out["end"]), (5.0, 9.0))


if __name__ == "__main__":
    unittest.main()
