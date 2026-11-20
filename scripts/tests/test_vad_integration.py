"""VAD map integration tests: SpeechRegions queries, off-speech
hallucination cleanup, VAD-gap splitting, crumb merge gaps and wordless
start shrinking. No network/audio needed — all inputs are synthetic."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cleanup_segments import cleanup
from groq_transcribe import plan_chunks
from groq_word_adapter import (
    _conservative_quality,
    _merge_fragments_groq,
    _should_force_split,
    _shrink_wordless_start,
    _split_at_silence,
    _vad_split_indices,
)
from vad_map import SpeechRegions


def _regions(*pairs):
    return SpeechRegions(list(pairs))


class TestSpeechRegions(unittest.TestCase):
    def test_speech_in_clips_to_window(self):
        r = _regions((0, 2), (3, 5), (10, 12))
        self.assertEqual(r.speech_in(1, 4), [(1, 2), (3, 4)])

    def test_overlap_sums_clipped_regions(self):
        r = _regions((0, 2), (3, 5))
        self.assertAlmostEqual(r.overlap(1, 4), 2.0)

    def test_internal_gaps_filters_by_length(self):
        r = _regions((0, 2), (2.5, 5))
        self.assertEqual(r.internal_gaps(0, 5, min_len=0.8), [])
        self.assertEqual(r.internal_gaps(0, 5, min_len=0.4), [(2, 2.5)])

    def test_internal_gaps_ignores_edge_silence(self):
        r = _regions((2, 5))
        self.assertEqual(r.internal_gaps(0, 6, min_len=0.8), [])

    def test_from_map_round_trip(self):
        r = SpeechRegions.from_map({"speech": [{"start": 1.0, "end": 2.0}]})
        self.assertEqual(r.speech_in(0, 3), [(1.0, 2.0)])

    def test_distance_to_speech(self):
        r = _regions((2, 4))
        self.assertEqual(r.distance_to_speech(3.0), 0.0)
        self.assertAlmostEqual(r.distance_to_speech(5.1), 1.1)
        self.assertAlmostEqual(r.distance_to_speech(0.5), 1.5)


class TestOffspeechCleanup(unittest.TestCase):
    def _seg(self, start, end, text="字幕内容测试文本"):
        return {"start": start, "end": end, "text": text}

    def test_long_segment_deep_in_silence_removed(self):
        regions = _regions((0, 5), (20, 25))
        audit = []
        out = cleanup([self._seg(10, 15)], audit=audit, regions=regions)
        self.assertEqual(out, [])
        self.assertIn("offspeech_vad", audit[0]["reasons"])

    def test_short_you_loop_deep_in_silence_removed(self):
        # the 30s-periodic "you" hallucination loop is ~1s long
        regions = _regions((0, 5), (2800, 2802))
        audit = []
        out = cleanup([{"start": 1600.0, "end": 1601.0, "text": "you"}],
                       audit=audit, regions=regions)
        self.assertEqual(out, [])
        self.assertIn("offspeech_vad", audit[0]["reasons"])

    def test_short_cue_hugging_speech_kept(self):
        # a real cue starting just before speech onset: VAD boundary jitter
        # leaves a moment of non-overlap, but the midpoint stays close
        regions = _regions((11, 15))
        out = cleanup([self._seg(10, 10.9)], regions=regions)
        self.assertEqual(len(out), 1)

    def test_segment_with_real_overlap_kept(self):
        regions = _regions((10, 15))
        out = cleanup([self._seg(10, 15)], regions=regions)
        self.assertEqual(len(out), 1)

    def test_split_farewell_blacklisted(self):
        # the 明镜 farewell often arrives as two cues; halves must match
        a = {"start": 1, "end": 2, "text": "请不吝点赞 订阅 转发 打赏支持"}
        b = {"start": 2, "end": 3, "text": "明镜与点点栏目"}
        self.assertEqual(len(cleanup([a, b])), 0)

    def test_no_regions_rule_disabled(self):
        out = cleanup([self._seg(10, 15)], regions=None)
        self.assertEqual(len(out), 1)


def _stretched_words():
    """10 per-char words; the 5th word's end is stretched across a 2.2-5.4s
    silence so the word-gap scan sees nothing (the Groq fill poison)."""
    words = []
    text = "大家好今天我们来讲课"
    for i, ch in enumerate(text):
        if i <= 4:
            start = 0.1 + i * 0.5
            end = 5.5 if i == 4 else start + 0.4
        else:
            start = 5.5 + (i - 5) * 0.5
            end = start + 0.4
        words.append({"word": ch, "start": start, "end": end})
    return words


class TestVadSplit(unittest.TestCase):
    def setUp(self):
        self.words = _stretched_words()
        self.seg = {"start": 0.0, "end": 8.0, "text": "大家好今天我们来讲课"}
        self.regions = _regions((0.0, 2.2), (5.4, 8.0))

    def test_word_gap_misses_but_vad_finds_index(self):
        gaps = [i for i in range(1, len(self.words))
                if self.words[i]["start"] - self.words[i - 1]["end"] > 0.8]
        self.assertEqual(gaps, [])  # spans stretched → word scan blind
        self.assertEqual(_vad_split_indices(self.seg, self.words, self.regions), [5])

    def test_split_at_silence_uses_vad_gap(self):
        pieces = _split_at_silence(self.seg, self.words, 0.8, regions=self.regions)
        self.assertIsNotNone(pieces)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0]["text"], "大家好今天")
        self.assertEqual(pieces[1]["text"], "我们来讲课")
        # stretched overshoot must not overlap the next piece
        self.assertLessEqual(pieces[0]["end"], pieces[1]["start"])

    def test_split_at_silence_without_regions_stays_none(self):
        self.assertIsNone(_split_at_silence(self.seg, self.words, 0.8))

    def test_should_force_split_via_vad(self):
        self.assertTrue(_should_force_split(self.seg, self.words, self.regions))
        self.assertFalse(_should_force_split(self.seg, self.words, None))


class TestCrumbMergeGap(unittest.TestCase):
    def _crumb(self, start, end, text):
        return {"start": start, "end": end, "text": text}

    def test_crumbs_across_big_silence_not_merged(self):
        # window boundaries are seamless (2.0 == 2.0) but VAD sees 5.5s of
        # silence between the two utterances → no merge
        regions = _regions((0.3, 1.0), (6.5, 7.5))
        segs = [self._crumb(0, 2, "好"), self._crumb(2, 8, "的")]
        out = _merge_fragments_groq(segs, regions=regions)
        self.assertEqual(len(out), 2)

    def test_crumbs_in_continuous_speech_merged(self):
        regions = _regions((0.3, 1.8), (2.1, 3.5))
        segs = [self._crumb(0, 2, "好"), self._crumb(2, 4, "的")]
        out = _merge_fragments_groq(segs, regions=regions)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "好的")

    def test_vad_mode_keeps_pause_separated_ok(self):
        # W13 gold: "一个拆解" + "OK" with a real 0.7s pause between must
        # stay two cues; only gap≈0 mid-word fragments may glue.
        regions = _regions((49.0, 50.7), (51.4, 52.0))
        segs = [{"start": 47.9, "end": 50.9, "text": "我们现在需要对它做一个资产的一个拆解",
                 "words": [{"word": "解", "start": 50.4, "end": 50.7}]},
                {"start": 51.6, "end": 52.1, "text": "OK",
                 "words": [{"word": "OK", "start": 51.7, "end": 52.0}]}]
        out = _merge_fragments_groq(segs, regions=regions)
        self.assertEqual(len(out), 2)

    def test_no_vad_mode_keeps_legacy_generous_gap(self):
        # without regions the legacy 5.0s window-bloat tolerance applies
        segs = [self._crumb(0, 2, "好"), self._crumb(2, 4.5, "的")]
        out = _merge_fragments_groq(segs, regions=None)
        self.assertEqual(len(out), 1)


class TestAnchorStartToSpeech(unittest.TestCase):
    def _seg(self, start, first_word_start, end, text="测试字幕内容文本"):
        return {"start": start, "end": end, "text": text,
                "words": [{"word": "测", "start": first_word_start,
                           "end": first_word_start + 0.3}]}

    def test_start_snapped_when_window_leads_silence(self):
        from groq_word_adapter import _anchor_start_to_speech
        regions = _regions((50.0, 55.0))
        seg = self._seg(48.0, 50.2, 55.0)   # window leads speech by 2.2s
        out = _anchor_start_to_speech([seg], regions)
        self.assertAlmostEqual(out[0]["start"], 50.1)

    def test_start_kept_when_already_on_speech(self):
        from groq_word_adapter import _anchor_start_to_speech
        regions = _regions((50.0, 55.0))
        seg = self._seg(50.0, 50.2, 55.0)
        out = _anchor_start_to_speech([seg], regions)
        self.assertEqual(out[0]["start"], 50.0)

    def test_no_regions_noop(self):
        from groq_word_adapter import _anchor_start_to_speech
        seg = self._seg(48.0, 50.2, 55.0)
        out = _anchor_start_to_speech([seg], None)
        self.assertIs(out[0], seg)


class TestShrinkWordlessStart(unittest.TestCase):
    def test_start_pulled_to_speech_onset(self):
        seg = {"start": 0.0, "end": 10.0, "text": "嗯"}
        out = _shrink_wordless_start(seg, lambda t0, t1: [(4.0, 6.0)])
        self.assertAlmostEqual(out["start"], 3.75)
        self.assertAlmostEqual(out["end"], 6.2)

    def test_no_clusters_keeps_segment(self):
        seg = {"start": 0.0, "end": 10.0, "text": "嗯"}
        self.assertIs(_shrink_wordless_start(seg, lambda t0, t1: []), seg)


class TestPlanChunks(unittest.TestCase):
    def _map(self, *pairs):
        return {"speech": [{"start": s, "end": e} for s, e in pairs],
                "duration": 100.0}

    def test_close_regions_merge_far_apart_split(self):
        # Default policy: gaps < 300s merge, larger gaps start a new chunk.
        chunks = plan_chunks(self._map((0, 5), (195, 200), (600, 605)))
        self.assertEqual(chunks, [(0, 200), (600, 605)])

    def test_gap_under_threshold_joins(self):
        chunks = plan_chunks(self._map((0, 5), (12, 15)), merge_gap=10.0)
        self.assertEqual(chunks, [(0, 15)])

    def test_overlong_run_splits(self):
        regions = [(i * 20, i * 20 + 18) for i in range(31)]  # ~620s run, 2s gaps
        # Greedy split still triggers at an explicit tighter max_len.
        chunks = plan_chunks(self._map(*regions), max_len=600.0)
        self.assertGreater(len(chunks), 1)
        for s, e in chunks:
            self.assertLessEqual(e - s, 600.0 + 2.0)
        # chunks must stay ordered and non-overlapping
        for (s1, _), (s2, _) in zip(chunks, chunks[1:]):
            self.assertGreaterEqual(s2, s1)

    def test_halfhour_cap(self):
        # Default policy: every chunk span stays under the 30-minute cap.
        regions = [(i * 300, i * 300 + 240) for i in range(9)]  # 42 min, 60s gaps
        chunks = plan_chunks(self._map(*regions))
        for s, e in chunks:
            self.assertLessEqual(e - s, 1800.0)

    def test_no_chunks_inside_speech(self):
        regions = [(i * 20, i * 20 + 18) for i in range(31)]
        for s, e in plan_chunks(self._map(*regions)):
            for i in range(31):
                r0, r1 = i * 20, i * 20 + 18
                self.assertFalse(s < r1 and r0 < e and (s > r0 or e < r1),
                                 f"chunk {s}-{e} cuts inside region {r0}-{r1}")


class TestLongAudioFixes(unittest.TestCase):
    def test_punct_only_cue_removed(self):
        # decoder collapse after silences emits "."-only cues
        audit = []
        out = cleanup([{"start": 0, "end": 2, "text": "."},
                       {"start": 2, "end": 4, "text": "正常字幕内容"}], audit=audit)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "正常字幕内容")
        self.assertIn("punct_only", audit[0]["reasons"])

    def test_merged_cue_keeps_speech_like_quality(self):
        # real "这边好像" (nsp=0.04) + hallucinated "嗯" (nsp=0.83): the merged
        # cue must NOT inherit the neighbour's no-speech verdict
        a = {"start": 0.0, "end": 1.1, "text": "这边好像",
             "no_speech_prob": 0.04, "avg_logprob": -0.33}
        b = {"start": 1.1, "end": 3.1, "text": "嗯",
             "no_speech_prob": 0.83, "avg_logprob": -1.27}
        merged = _conservative_quality([a, b])
        self.assertLess(merged["no_speech_prob"], 0.8)
        self.assertGreater(merged["avg_logprob"], -1.0)
        # end-to-end: cleanup must keep the merged cue
        kept = cleanup([{"start": 0.0, "end": 3.1, "text": "这边好像嗯",
                         "no_speech_prob": 0.04, "avg_logprob": -0.33}])
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
