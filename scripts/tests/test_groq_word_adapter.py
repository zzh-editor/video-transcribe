import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groq_word_adapter import (
    _assign_words_to_segments,
    _build_char_time_map,
    _check_coverage,
    _build_phrased_units,
    _protect_technical_tokens,
    _is_abnormal,
    _verify_text,
    _verify_times,
    refine_groq_segments,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    path = _FIXTURE_DIR / name
    with open(path, "r") as f:
        return json.load(f)


def _ws(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


class TestAssignWords(unittest.TestCase):

    def test_simple_assignment(self):
        segs = [{"start": 0, "end": 3, "text": "你好世界"}, {"start": 3, "end": 6, "text": "再见"}]
        words = [_ws("你", 0, 0.5), _ws("好", 0.5, 1), _ws("世", 1, 2), _ws("界", 2, 3), _ws("再", 3, 4), _ws("见", 4, 5)]
        assigned = _assign_words_to_segments(words, segs)
        self.assertEqual(len(assigned[0]), 4)
        self.assertEqual(len(assigned[1]), 2)

    def test_boundary_word_goes_to_earlier_segment(self):
        segs = [{"start": 0, "end": 3, "text": "一二"}, {"start": 3, "end": 6, "text": "三四"}]
        word = _ws("三", 3, 3.5)
        assigned = _assign_words_to_segments([word], segs)
        self.assertEqual(len(assigned[0]), 0)
        self.assertEqual(len(assigned[1]), 1)

    def test_no_overlap_word_is_skipped(self):
        segs = [{"start": 0, "end": 3, "text": "一二"}]
        word = _ws("三", 10, 12)
        assigned = _assign_words_to_segments([word], segs)
        self.assertEqual(len(assigned[0]), 0)


class TestCharTimeMap(unittest.TestCase):

    def test_simple_char_mapping(self):
        text = "技术美术"
        words = [
            {"word": "技", "start": 0.0, "end": 0.2},
            {"word": "术", "start": 0.2, "end": 0.35},
            {"word": "美", "start": 0.35, "end": 0.5},
            {"word": "术", "start": 0.5, "end": 0.65},
        ]
        ct = _build_char_time_map(text, words)
        self.assertEqual(len(ct), 4)
        self.assertAlmostEqual(ct[0][0], 0.0)
        self.assertAlmostEqual(ct[3][1], 0.65)

    def test_text_with_punctuation(self):
        text = "你好，世界。"
        words = [
            {"word": "你", "start": 0, "end": 0.3},
            {"word": "好", "start": 0.3, "end": 0.6},
            {"word": "世", "start": 0.7, "end": 1.0},
            {"word": "界", "start": 1.0, "end": 1.3},
        ]
        ct = _build_char_time_map(text, words)
        self.assertIn(0, ct)  # 你
        self.assertIn(1, ct)  # 好
        self.assertIn(2, ct)  # ，
        self.assertIn(3, ct)  # 世
        self.assertIn(4, ct)  # 界
        self.assertIn(5, ct)  # 。
        self.assertNotIn(-1, ct)

    def test_mismatch_returns_empty(self):
        text = "计算机科学"
        words = [
            {"word": "技", "start": 0, "end": 0.2},
            {"word": "术", "start": 0.2, "end": 0.4},
        ]
        ct = _build_char_time_map(text, words)
        self.assertEqual(ct, {})


class TestCoverage(unittest.TestCase):

    def test_full_coverage(self):
        text = "技术美术"
        ct = {0: (0, 0.2), 1: (0.2, 0.35), 2: (0.35, 0.5), 3: (0.5, 0.65)}
        cov = _check_coverage(text, ct)
        self.assertAlmostEqual(cov, 1.0)

    def test_partial_coverage(self):
        text = "技术美术"
        ct = {0: (0, 0.2), 1: (0.2, 0.35)}
        cov = _check_coverage(text, ct)
        self.assertAlmostEqual(cov, 0.5)

    def test_only_punct_returns_full(self):
        text = "，。！"
        ct = {}
        cov = _check_coverage(text, ct)
        self.assertAlmostEqual(cov, 1.0)


class TestPhrasedUnits(unittest.TestCase):

    def test_chinese_phrases_from_fixture(self):
        fixture = _load_fixture("groq_zh_verbose.json")
        seg = fixture["segments"][0]
        seg_words = [w for w in fixture["words"] if w["start"] >= seg["start"] and w["end"] <= seg["end"]]
        ct = _build_char_time_map(seg["text"], seg_words)
        self.assertGreater(len(ct), 0)
        units = _build_phrased_units(seg["text"], ct)
        self.assertGreater(len(units), 0)
        self.assertTrue(_verify_text(units, seg["text"]))

    def test_phrased_units_preserve_text(self):
        text = "你好世界"
        words = [
            {"word": "你", "start": 0, "end": 0.3},
            {"word": "好", "start": 0.3, "end": 0.6},
            {"word": "世", "start": 0.6, "end": 0.9},
            {"word": "界", "start": 0.9, "end": 1.2},
        ]
        ct = _build_char_time_map(text, words)
        units = _build_phrased_units(text, ct)
        self.assertTrue(_verify_text(units, text))


class TestTechnicalTokenProtection(unittest.TestCase):

    def test_cpp_remains_intact(self):
        units = [
            {"word": "C", "start": 0, "end": 0.1},
            {"word": "+", "start": 0.1, "end": 0.15},
            {"word": "+", "start": 0.15, "end": 0.2},
        ]
        merged = _protect_technical_tokens(units)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["word"], "C++")

    def test_chinese_not_merged_with_english(self):
        units = [
            {"word": "技术", "start": 0, "end": 0.3},
            {"word": "美术", "start": 0.3, "end": 0.6},
            {"word": "C", "start": 0.6, "end": 0.7},
            {"word": "+", "start": 0.7, "end": 0.8},
            {"word": "+", "start": 0.8, "end": 0.9},
        ]
        merged = _protect_technical_tokens(units)
        self.assertGreater(len(merged), 2)


class TestAbnormalDetection(unittest.TestCase):

    def test_short_normal(self):
        seg = {"start": 0, "end": 2, "text": "你好世界"}
        self.assertFalse(_is_abnormal(seg, 10, 5000))

    def test_too_long_text(self):
        seg = {"start": 0, "end": 2, "text": "一二三四五六七八九十" * 5}
        self.assertTrue(_is_abnormal(seg, 10, 5000))

    def test_too_long_duration(self):
        seg = {"start": 0, "end": 10, "text": "你好"}
        self.assertTrue(_is_abnormal(seg, 10, 3000))


class TestVerifyTimes(unittest.TestCase):

    def test_valid_times(self):
        units = [
            {"word": "你好", "start": 0, "end": 0.5},
            {"word": "世界", "start": 0.5, "end": 1.0},
        ]
        self.assertTrue(_verify_times(units, 0, 1.0))

    def test_overlapping_times_fails(self):
        units = [
            {"word": "你好", "start": 0, "end": 0.6},
            {"word": "世界", "start": 0.5, "end": 1.0},
        ]
        self.assertFalse(_verify_times(units, 0, 1.0))

    def test_out_of_bounds_fails(self):
        units = [
            {"word": "你好", "start": -0.1, "end": 0.5},
        ]
        self.assertFalse(_verify_times(units, 0, 1.0))


class TestRefineGroqSegments(unittest.TestCase):

    def setUp(self):
        self.fixture = _load_fixture("groq_zh_verbose.json")

    def test_normal_segments_unchanged(self):
        result = refine_groq_segments(
            self.fixture["segments"],
            [],
            max_chars=100,
            max_line_ms=10000,
        )
        self.assertEqual(len(result), len(self.fixture["segments"]))

    def test_abnormal_segment_with_words_is_split(self):
        segments = [self.fixture["segments"][2]]
        words = [w for w in self.fixture["words"] if w["start"] >= 5.0 and w["end"] <= 12.0]
        result = refine_groq_segments(
            segments, words,
            max_chars=20, max_line_ms=4000, pause_threshold=0.3,
        )
        self.assertGreater(len(result), 1)

    def test_short_segment_stays_unchanged(self):
        segments = [self.fixture["segments"][0]]
        words = [w for w in self.fixture["words"] if w["start"] >= 0 and w["end"] <= 2.5]
        result = refine_groq_segments(
            segments, words,
            max_chars=25, max_line_ms=4000,
        )
        self.assertEqual(len(result), 1)

    def test_no_words_still_returns(self):
        segments = [self.fixture["segments"][2]]
        result = refine_groq_segments(
            segments, [],
            max_chars=20, max_line_ms=4000,
        )
        self.assertEqual(len(result), 1)

    def test_full_integration_with_fixture(self):
        words = self.fixture["words"]
        result = refine_groq_segments(
            self.fixture["segments"], words,
            max_chars=25, max_line_ms=4000, pause_threshold=0.3,
        )
        self.assertGreater(len(result), len(self.fixture["segments"]))

    def test_text_conservation(self):
        words = self.fixture["words"]
        segs = self.fixture["segments"]
        result = refine_groq_segments(segs, words, max_chars=20, max_line_ms=4000)
        merged_text = "".join(r["text"] for r in result)
        orig_text = "".join(s["text"] for s in segs)
        self.assertEqual(
            merged_text.replace(" ", ""),
            orig_text.replace(" ", ""),
        )

    def test_time_monotonic(self):
        words = self.fixture["words"]
        result = refine_groq_segments(
            self.fixture["segments"], words,
            max_chars=25, max_line_ms=4000,
        )
        for i in range(len(result) - 1):
            self.assertLessEqual(result[i]["end"], result[i + 1]["start"] + 0.01)

    def test_missing_top_words_local_fallback(self):
        segments = [self.fixture["segments"][2]]
        result = refine_groq_segments(
            segments, [],
            max_chars=20, max_line_ms=4000,
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
