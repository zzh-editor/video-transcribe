"""Tests for cleanup_segments.py — 4 hallucination rules + gap-constrained dedup."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cleanup_segments import (
    cleanup,
    _is_dense_hallucination,
    _is_no_speech_hallucination,
    _is_known_hallucination,
    _is_repeat_loop,
    _normalize_text,
)


class TestKnownHallucination(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(_is_known_hallucination("请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"))

    def test_spacing_variant(self):
        self.assertTrue(_is_known_hallucination("请不吝点赞订阅转发打赏支持明镜与点点栏目"))

    def test_substring_match(self):
        self.assertTrue(_is_known_hallucination("xxx请准确转写专业yyy"))

    def test_second_blacklist(self):
        self.assertTrue(_is_known_hallucination("保持简体中文。"))

    def test_no_false_positive(self):
        self.assertFalse(_is_known_hallucination("今天我们一起来做一个风格化水材质"))

    def test_empty_not_flagged(self):
        self.assertFalse(_is_known_hallucination("   "))


class TestRepeatLoop(unittest.TestCase):
    def test_short_text_not_flagged(self):
        self.assertFalse(_is_repeat_loop("你好"))

    def test_long_repetitive_flagged(self):
        # 30+ chars with only 2 unique chars
        self.assertTrue(_is_repeat_loop("啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊啊"))

    def test_long_normal_not_flagged(self):
        self.assertFalse(_is_repeat_loop("今天我们一起来做一个风格化水材质的教程然后讲解一下具体步骤"))


class TestDenseHallucination(unittest.TestCase):
    def test_dense_flagged(self):
        seg = {"start": 0.0, "end": 0.54, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertTrue(_is_dense_hallucination(seg))

    def test_dense_whitespace_variant(self):
        seg = {"start": 0.0, "end": 0.54, "text": "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"}
        self.assertTrue(_is_dense_hallucination(seg))

    def test_normal_not_flagged(self):
        seg = {"start": 0.0, "end": 2.0, "text": "今天我们一起来做一个风格化水材质"}
        self.assertFalse(_is_dense_hallucination(seg))

    def test_short_text_not_flagged(self):
        seg = {"start": 0.0, "end": 0.5, "text": "你好"}
        self.assertFalse(_is_dense_hallucination(seg))

    def test_long_duration_not_flagged(self):
        seg = {"start": 0.0, "end": 2.0, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertFalse(_is_dense_hallucination(seg))


class TestNoSpeechHallucination(unittest.TestCase):
    def test_both_metrics_flagged(self):
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "no_speech_prob": 0.95, "avg_logprob": -2.3}
        self.assertTrue(_is_no_speech_hallucination(seg))

    def test_only_high_nsp_not_flagged(self):
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "no_speech_prob": 0.95}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_only_low_logprob_not_flagged(self):
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "avg_logprob": -3.0}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_real_speech_not_flagged(self):
        seg = {"start": 0.0, "end": 2.0, "text": "今天我们一起来做一个风格化水材质",
               "no_speech_prob": 0.02, "avg_logprob": -0.15}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_short_text_not_flagged(self):
        seg = {"start": 0.0, "end": 1.0, "text": "对",
               "no_speech_prob": 0.98, "avg_logprob": -2.0}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_no_metrics_not_flagged(self):
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏"}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_boundary_thresholds(self):
        # Exactly at threshold: nsp 0.8, logprob -1.0 should flag (>= and <=)
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏测试",
               "no_speech_prob": 0.8, "avg_logprob": -1.0}
        self.assertTrue(_is_no_speech_hallucination(seg))


class TestCleanupIntegration(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(cleanup([]), [])

    def test_empty_text_removed(self):
        segs = [{"start": 0, "end": 1, "text": "   "},
                {"start": 1, "end": 2, "text": "你好"}]
        self.assertEqual(len(cleanup(segs)), 1)

    def test_known_hallucination_removed(self):
        seg = {"start": 0, "end": 1, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertEqual(len(cleanup([seg])), 0)

    def test_dense_hallucination_removed_via_cleanup(self):
        seg = {"start": 0.0, "end": 0.5, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertEqual(len(cleanup([seg])), 0)

    def test_no_speech_hallucination_removed_via_cleanup(self):
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏测试",
               "no_speech_prob": 0.9, "avg_logprob": -2.0}
        self.assertEqual(len(cleanup([seg])), 0)

    def test_normal_kept(self):
        seg = {"start": 0, "end": 2, "text": "今天我们一起来做一个风格化水材质"}
        self.assertEqual(len(cleanup([seg])), 1)


class TestDedupGapConstraint(unittest.TestCase):
    """P1-3: identical text only merges when gap <= 1.0s."""

    def test_close_gap_merged(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 1.5, "end": 2.5, "text": "你好"},  # gap 0.5
        ]
        out = cleanup(segs)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["end"], 2.5)

    def test_exact_1s_gap_merged(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 2.0, "end": 3.0, "text": "你好"},  # gap 1.0
        ]
        self.assertEqual(len(cleanup(segs)), 1)

    def test_large_gap_not_merged(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 2.5, "end": 3.5, "text": "你好"},  # gap 1.5 > 1.0
        ]
        self.assertEqual(len(cleanup(segs)), 2)

    def test_distant_repetition_preserved(self):
        # Real case: same phrase 30s apart should not merge
        segs = [
            {"start": 0.0, "end": 1.0, "text": "谢谢"},
            {"start": 30.0, "end": 31.0, "text": "谢谢"},
        ]
        self.assertEqual(len(cleanup(segs)), 2)

    def test_case_insensitive_merge(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "Hello"},
            {"start": 1.2, "end": 2.0, "text": "hello"},
        ]
        self.assertEqual(len(cleanup(segs)), 1)

    def test_three_consecutive_same_text(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 1.1, "end": 2.0, "text": "你好"},
            {"start": 2.1, "end": 3.0, "text": "你好"},
        ]
        out = cleanup(segs)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["end"], 3.0)

    def test_different_text_not_merged(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 1.2, "end": 2.0, "text": "再见"},
        ]
        self.assertEqual(len(cleanup(segs)), 2)


if __name__ == "__main__":
    unittest.main()
