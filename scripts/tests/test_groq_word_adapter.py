import json
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groq_word_adapter import (
    _AudioEnergy,
    _assign_words_to_segments,
    _build_char_time_map,
    _check_coverage,
    _build_phrased_units,
    _median_smooth,
    _protect_technical_tokens,
    _is_abnormal,
    _verify_text,
    _verify_times,
    _speech_span,
    _shrink_silent_tail,
    _merge_fragments_groq,
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
        self.assertGreater(len(result), 1)
        merged_text = "".join(r["text"] for r in result).replace(" ", "")
        self.assertEqual(merged_text, segments[0]["text"].replace(" ", ""))

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
        self.assertGreater(len(result), 1)
        merged_text = "".join(r["text"] for r in result).replace(" ", "")
        self.assertEqual(merged_text, segments[0]["text"].replace(" ", ""))


class TestSilentTailShrink(unittest.TestCase):

    def test_span_computation(self):
        words = [_ws("对", 0.0, 0.3), _ws("然后", 0.3, 0.6), _ws("里", 0.6, 0.9)]
        self.assertAlmostEqual(_speech_span(words), 0.9)

    def test_short_span_no_shrink(self):
        # 3 words covering 0.9s inside a 1.0s window → ratio > 0.4, no shrink
        seg = {"start": 0.0, "end": 1.0, "text": "对然后里"}
        words = [_ws("对", 0.0, 0.3), _ws("然后", 0.3, 0.6), _ws("里", 0.6, 0.9)]
        out = _shrink_silent_tail(seg, words)
        self.assertAlmostEqual(out["end"], 1.0)

    def test_silence_bloated_window_is_shrunk(self):
        # 3 words spanning 0.9s inside a 6.0s window → heavily padded
        seg = {"start": 0.0, "end": 6.0, "text": "对然后里"}
        words = [_ws("对", 0.0, 0.3), _ws("然后", 0.3, 0.6), _ws("里", 0.6, 0.9)]
        out = _shrink_silent_tail(seg, words)
        self.assertLess(out["end"], 1.2)

    def test_no_words_returns_original(self):
        seg = {"start": 0.0, "end": 6.0, "text": "对然后里"}
        out = _shrink_silent_tail(seg, [])
        self.assertIs(out, seg)

    def test_last_word_at_end_no_shrink(self):
        # last word ends at window end → saved < 1s, no shrink
        seg = {"start": 0.0, "end": 5.5, "text": "对然后里"}
        words = [_ws("对", 0.0, 0.3), _ws("然后", 0.3, 0.6), _ws("里", 5.0, 5.5)]
        out = _shrink_silent_tail(seg, words)
        self.assertAlmostEqual(out["end"], 5.5)


class TestMergeFragmentsGroq(unittest.TestCase):

    def test_merges_adjacent_crumbs(self):
        segs = [
            {"start": 0.0, "end": 5.0, "text": "对然后里"},
            {"start": 5.2, "end": 10.0, "text": "面就是可"},
            {"start": 10.2, "end": 15.0, "text": "以去调整"},
        ]
        out = _merge_fragments_groq(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "对然后里面就是可以去调整")
        self.assertAlmostEqual(out[0]["start"], 0.0)
        self.assertAlmostEqual(out[0]["end"], 15.0)

    def test_large_gap_breaks_merge(self):
        segs = [
            {"start": 0.0, "end": 5.0, "text": "对然后里"},
            {"start": 20.0, "end": 25.0, "text": "面就是可"},
        ]
        out = _merge_fragments_groq(segs)
        self.assertEqual(len(out), 2)

    def test_long_segment_blocks_merge(self):
        segs = [
            {"start": 0.0, "end": 5.0, "text": "对然后里"},
            {"start": 5.2, "end": 10.0, "text": "面就是可以去调整一下这个法线的速度"},
        ]
        out = _merge_fragments_groq(segs)
        self.assertEqual(len(out), 2)

    def test_single_segment_unchanged(self):
        segs = [{"start": 0.0, "end": 5.0, "text": "对然后里"}]
        out = _merge_fragments_groq(segs)
        self.assertEqual(len(out), 1)

    def test_single_fragment_with_long_neighbor_stays(self):
        # One crumb followed by a long segment → no merge, crumb kept alone
        segs = [
            {"start": 0.0, "end": 5.0, "text": "对然后里"},
            {"start": 5.2, "end": 10.0, "text": "这是一个很长很长的正常句子"},
        ]
        out = _merge_fragments_groq(segs)
        self.assertEqual(len(out), 2)


class TestFullSegmentMode(unittest.TestCase):

    def setUp(self):
        self.fixture = _load_fixture("groq_zh_verbose.json")

    def test_full_segment_splits_normal_short_window(self):
        # seg[0]: 19 chars in a 2.5s window → not abnormal (chars < 25, dur < 4000),
        # but with full_segment the scoring engine re-splits it into >= 2 lines.
        segments = [self.fixture["segments"][0]]
        words = [w for w in self.fixture["words"] if w["start"] >= 0.0 and w["end"] <= 2.5]
        result = refine_groq_segments(
            segments, words,
            max_chars=10, max_line_ms=4000, pause_threshold=0.3,
            full_segment=True,
        )
        self.assertGreaterEqual(len(result), 2)

    def test_normal_mode_keeps_short_window_whole(self):
        # With default max_chars=25, seg[0] (19 chars) is not abnormal and
        # is kept as a single segment in normal (non-full) mode.
        segments = [self.fixture["segments"][0]]
        words = [w for w in self.fixture["words"] if w["start"] >= 0.0 and w["end"] <= 2.5]
        result = refine_groq_segments(
            segments, words,
            max_chars=25, max_line_ms=4000, pause_threshold=0.3,
        )
        self.assertEqual(len(result), 1)

    def test_full_segment_preserves_text_conservation(self):
        words = self.fixture["words"]
        result = refine_groq_segments(
            self.fixture["segments"], words,
            max_chars=15, max_line_ms=4000, pause_threshold=0.3,
            full_segment=True,
        )
        merged_text = "".join(r["text"] for r in result)
        orig_text = "".join(s["text"] for s in self.fixture["segments"])
        self.assertEqual(
            merged_text.replace(" ", ""),
            orig_text.replace(" ", ""),
        )


class TestHallucinationFiltering(unittest.TestCase):

    def test_dense_hallucination_flagged(self):
        from cleanup_segments import _is_dense_hallucination, cleanup
        seg = {"start": 0.0, "end": 0.54, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertTrue(_is_dense_hallucination(seg))
        out = cleanup([seg])
        self.assertEqual(len(out), 0)

    def test_dense_hallucination_whitespace_variant(self):
        from cleanup_segments import _is_dense_hallucination
        seg = {"start": 0.0, "end": 0.54, "text": "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"}
        self.assertTrue(_is_dense_hallucination(seg))

    def test_normal_speech_not_flagged_dense(self):
        from cleanup_segments import _is_dense_hallucination
        seg = {"start": 0.0, "end": 2.0, "text": "今天我们一起来做一个风格化水材质"}
        self.assertFalse(_is_dense_hallucination(seg))

    def test_short_text_no_flag(self):
        from cleanup_segments import _is_dense_hallucination
        seg = {"start": 0.0, "end": 0.5, "text": "你好"}
        self.assertFalse(_is_dense_hallucination(seg))

    def test_long_duration_no_flag(self):
        from cleanup_segments import _is_dense_hallucination
        seg = {"start": 0.0, "end": 2.0, "text": "请不吝点赞订阅转发打赏支持明镜与点点栏目"}
        self.assertFalse(_is_dense_hallucination(seg))

    def test_no_speech_hallucination_flagged(self):
        from cleanup_segments import _is_no_speech_hallucination, cleanup
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "no_speech_prob": 0.95, "avg_logprob": -2.3}
        self.assertTrue(_is_no_speech_hallucination(seg))
        self.assertEqual(len(cleanup([seg])), 0)

    def test_no_speech_high_prob_only_not_flagged(self):
        from cleanup_segments import _is_no_speech_hallucination
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "no_speech_prob": 0.95}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_no_speech_low_logprob_only_not_flagged(self):
        from cleanup_segments import _is_no_speech_hallucination
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏",
               "avg_logprob": -3.0}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_real_speech_not_flagged(self):
        from cleanup_segments import _is_no_speech_hallucination
        seg = {"start": 0.0, "end": 2.0, "text": "今天我们一起来做一个风格化水材质",
               "no_speech_prob": 0.02, "avg_logprob": -0.15}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_short_text_no_speech_not_flagged(self):
        from cleanup_segments import _is_no_speech_hallucination
        seg = {"start": 0.0, "end": 1.0, "text": "对",
               "no_speech_prob": 0.98, "avg_logprob": -2.0}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_no_speech_without_metrics_skipped(self):
        from cleanup_segments import _is_no_speech_hallucination
        seg = {"start": 0.0, "end": 1.0, "text": "请不吝点赞订阅转发打赏"}
        self.assertFalse(_is_no_speech_hallucination(seg))

    def test_prompt_hallucination_removed(self):
        from cleanup_segments import _is_known_hallucination, cleanup
        text = "请准确转写专业术语．保持简体中文。"
        self.assertTrue(_is_known_hallucination(text))
        seg = {"start": 389.9, "end": 394.8, "text": text,
               "no_speech_prob": 0.02, "avg_logprob": -0.21}
        self.assertEqual(len(cleanup([seg])), 0)

    def test_prompt_hallucination_partial_text_removed(self):
        from cleanup_segments import _is_known_hallucination
        self.assertTrue(_is_known_hallucination("请准确转写专业术"))
        self.assertTrue(_is_known_hallucination("保持简体中文。"))
        self.assertFalse(_is_known_hallucination("保持中文简体：意思是保持"))

    def test_prompt_hallucination_embedded_in_real_text_removed(self):
        from cleanup_segments import _is_known_hallucination
        self.assertTrue(_is_known_hallucination("请准确转写专业术语，然后开始讲解"))

    def test_mixed_prompt_and_real_text_removed(self):
        from cleanup_segments import cleanup
        seg = {"start": 760.12, "end": 791.82,
               "text": "Out。请准确转写专业术．保持简体中文。",
               "no_speech_prob": 0.05, "avg_logprob": -0.41}
        self.assertEqual(len(cleanup([seg])), 0)


class TestAbsorbIsolatedCrumbs(unittest.TestCase):
    def _seg(self, text, start, end, words=None):
        s = {"start": start, "end": end, "text": text}
        if words is not None:
            s["words"] = words
        return s

    def test_absorb_cjk_filler_into_next(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("然后", 965.29, 972.27),
            self._seg("我们这边需要增加一个新的一个节点", 972.27, 975.91),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "然后我们这边需要增加一个新的一个节点")
        self.assertEqual(out[0]["start"], 972.27)
        self.assertEqual(out[0]["end"], 975.91)

    def test_absorb_english_ok_into_next(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("OK", 555.51, 559.96),
            self._seg("那我们继续做下去", 559.716, 561.336),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "OK 那我们继续做下去")

    def test_isolated_kept_when_next_gap_large(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("对然后", 1008.51, 1017.45),
            self._seg("我们可以用到叫做cloudspeed", 1020.00, 1022.00),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 2)

    def test_short_duration_kept(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("然后", 965.29, 966.98),
            self._seg("我们这边需要增加一个新的一个节点", 967.27, 970.91),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 2)

    def test_normal_segment_kept(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("然后我们这边需要增加一个新的一个节点", 965.29, 975.91),
            self._seg("接下来", 975.91, 977.5),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 2)

    def test_no_words_still_absorbed(self):
        from groq_word_adapter import _absorb_isolated_crumbs
        segs = [
            self._seg("对", 1008.51, 1012.0),
            self._seg("然后我们可以用到叫做cloudspeed", 1012.0, 1019.0),
        ]
        out = _absorb_isolated_crumbs(segs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "对然后我们可以用到叫做cloudspeed")


class TestAudioEnergy(unittest.TestCase):
    """Energy detection uses a whole-file percentile threshold plus a
    median-smoothed RMS series, so identical audio wins a consistent
    speech/non-speech verdict regardless of the window being scanned."""

    RATE = 16000

    def _write_wav(self, path: str, data: bytes) -> None:
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(data)

    def _pcm(self, seconds: float, amp: int) -> bytes:
        n = int(seconds * self.RATE)
        return struct.pack(f"<{n}h", *(amp for _ in range(n)))

    def _mixed_wav(self, tmp: str) -> str:
        wav = os.path.join(tmp, "probe.wav")
        self._write_wav(wav, self._pcm(2.0, 100) + self._pcm(1.0, 3000) + self._pcm(2.0, 100))
        return wav

    def test_speech_cluster_found_in_mixed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            energy = _AudioEnergy(self._mixed_wav(tmp))
            try:
                clusters = energy.speech_clusters(0.0, 5.0)
            finally:
                energy.close()
        self.assertEqual(len(clusters), 1)
        onset, end = clusters[0]
        self.assertGreater(onset, 1.5)
        self.assertLess(onset, 2.6)
        self.assertGreater(end, 2.8)
        self.assertLess(end, 3.6)

    def test_silence_window_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            energy = _AudioEnergy(self._mixed_wav(tmp))
            try:
                clusters = energy.speech_clusters(0.0, 1.5)
            finally:
                energy.close()
        self.assertEqual(clusters, [])

    def test_median_smooth_drops_single_frame_glitch(self):
        points = [(i * 0.04, v) for i, v in enumerate([100, 100, 4000, 100, 100])]
        out = _median_smooth(points, 3)
        self.assertLess(out[2][1], 200)


if __name__ == "__main__":
    unittest.main()
