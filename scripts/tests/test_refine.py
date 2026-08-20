import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from refine_segments import (
    refine, _segment_words, _clean_words, _chars, _merge_fragments,
    STRONG_PUNCT, BAD_LINE_START, GOOD_LINE_START, SEMANTIC_START,
    _score_gap, _PREFERRED_MAX_CHARS, _SEMANTIC_MIN_CHARS,
)


def _ws(word: str, start: float, end: float) -> dict:
    return {"word": word, "start": start, "end": end}


class TestRefineSegments(unittest.TestCase):

    def test_chinese_natural_breaks(self):
        words = [
            _ws("大家", 0.0, 0.3), _ws("好", 0.3, 0.5),
            _ws("今天", 0.5, 0.8), _ws("我们", 0.8, 1.1),
            _ws("来", 1.1, 1.3), _ws("学习", 1.3, 1.6),
            _ws("一下", 1.6, 1.8), _ws("这个", 1.8, 2.0),
            _ws("新的", 2.0, 2.3), _ws("功能", 2.3, 2.5),
            _ws("首先", 3.0, 3.3), _ws("我们", 3.3, 3.5),
            _ws("先", 3.5, 3.7), _ws("来", 3.7, 3.9),
            _ws("看", 3.9, 4.1), _ws("一下", 4.1, 4.3),
            _ws("这个", 4.3, 4.5), _ws("界面", 4.5, 4.8),
            _ws("然后", 5.2, 5.4), _ws("我们", 5.4, 5.6),
            _ws("再", 5.6, 5.8), _ws("一步", 5.8, 6.0),
            _ws("一步", 6.0, 6.2), _ws("去", 6.2, 6.4),
            _ws("操作", 6.4, 6.7),
        ]
        full = "大家好今天我们来学习一下这个新的功能首先我们先来看一下这个界面然后我们再一步一步去操作"
        segs = [{"start": 0.0, "end": 8.5, "text": full, "words": words}]
        result = refine(segs, max_chars=20, max_line_ms=4000)
        self.assertEqual(len(result), 3)
        for s in result:
            t = s["text"].replace(" ", "")
            self.assertLessEqual(len(t), 20)
            self.assertLessEqual(s["end"] - s["start"], 4.0)

    def test_tight_chars_limit_15(self):
        words = [
            _ws("大家", 0.0, 0.3), _ws("好", 0.3, 0.5),
            _ws("今天", 0.5, 0.8), _ws("我们", 0.8, 1.1),
            _ws("来", 1.1, 1.3), _ws("学习", 1.3, 1.6),
            _ws("一下", 1.6, 1.8), _ws("这个", 1.8, 2.0),
            _ws("新的", 2.0, 2.3), _ws("功能", 2.3, 2.5),
            _ws("首先", 3.0, 3.3), _ws("我们", 3.3, 3.5),
            _ws("先", 3.5, 3.7), _ws("来", 3.7, 3.9),
            _ws("看", 3.9, 4.1), _ws("一下", 4.1, 4.3),
            _ws("这个", 4.3, 4.5), _ws("界面", 4.5, 4.8),
            _ws("然后", 5.2, 5.4), _ws("我们", 5.4, 5.6),
            _ws("再", 5.6, 5.8), _ws("一步", 5.8, 6.0),
            _ws("一步", 6.0, 6.2), _ws("去", 6.2, 6.4),
            _ws("操作", 6.4, 6.7),
        ]
        segs = [{"start": 0.0, "end": 8.5, "text": "", "words": words}]
        result = refine(segs, max_chars=15, max_line_ms=4000)
        for s in result:
            t = s["text"].replace(" ", "")
            self.assertLessEqual(len(t), 15)
            self.assertLessEqual(s["end"] - s["start"], 4.0)

    def test_single_word(self):
        words = [_ws("Hello", 0.0, 0.5)]
        segs = [{"start": 0.0, "end": 0.5, "text": "Hello", "words": words}]
        result = refine(segs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Hello")

    def test_empty_input(self):
        self.assertEqual(refine([]), [])

    def test_malformed_words_cleaning(self):
        words = [
            _ws("good", 0.0, 0.3),
            {"word": "", "start": 0.3, "end": 0.5},
            {"word": "bad", "start": None, "end": 0.5},
            {"word": "bad2", "start": 0.5, "end": 0.3},
            _ws("ok", 0.6, 0.9),
        ]
        cleaned = _clean_words(words)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0]["word"], "good")
        self.assertEqual(cleaned[1]["word"], "ok")

    def test_duration_overflow(self):
        words = [_ws(f"w{i}", i*1.0, i*1.0+0.5) for i in range(10)]
        segs = [{"start": 0.0, "end": 10.0, "text": "", "words": words}]
        result = refine(segs, max_chars=50, max_line_ms=3000)
        for s in result:
            self.assertLessEqual(s["end"] - s["start"], 3.0)

    def test_duplicate_cleanup(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "hello",
             "words": [_ws("hello", 0.0, 1.0)]},
            {"start": 0.0, "end": 1.0, "text": "hello",
             "words": [_ws("hello", 0.0, 1.0)]},
            {"start": 2.0, "end": 3.0, "text": "world",
             "words": [_ws("world", 2.0, 3.0)]},
        ]
        result = refine(segs)
        self.assertEqual(len(result), 2)

    def test_chars_count(self):
        words = [
            {"word": "  hello  ", "start": 0, "end": 1},
            {"word": "\u200bworld\u200b", "start": 1, "end": 2},
        ]
        self.assertEqual(_chars(words), 10)

    def test_fallback_no_words(self):
        text = "今天天气真不错，我们一起去公园散步吧。"
        segs = [{"start": 0.0, "end": 8.0, "text": text, "words": []}]
        result = refine(segs, max_chars=11, max_line_ms=5000)
        self.assertEqual(len(result), 1)

    def test_local_words_characterization(self):
        words = [
            {"word": "大家", "start": 0.0, "end": 0.3},
            {"word": "好", "start": 0.3, "end": 0.5},
            {"word": "今天", "start": 0.5, "end": 0.8},
            {"word": "我们", "start": 0.8, "end": 1.1},
            {"word": "来", "start": 1.1, "end": 1.3},
            {"word": "学习", "start": 1.3, "end": 1.6},
            {"word": "一下", "start": 1.6, "end": 1.8},
            {"word": "这个", "start": 1.8, "end": 2.0},
            {"word": "新的", "start": 2.0, "end": 2.3},
            {"word": "功能", "start": 2.3, "end": 2.5},
            {"word": "首先", "start": 3.0, "end": 3.3},
            {"word": "我们", "start": 3.3, "end": 3.5},
            {"word": "先", "start": 3.5, "end": 3.7},
            {"word": "来", "start": 3.7, "end": 3.9},
            {"word": "看", "start": 3.9, "end": 4.1},
            {"word": "一下", "start": 4.1, "end": 4.3},
            {"word": "这个", "start": 4.3, "end": 4.5},
            {"word": "界面", "start": 4.5, "end": 4.8},
            {"word": "然后", "start": 5.2, "end": 5.4},
            {"word": "我们", "start": 5.4, "end": 5.6},
            {"word": "再", "start": 5.6, "end": 5.8},
            {"word": "一步", "start": 5.8, "end": 6.0},
            {"word": "一步", "start": 6.0, "end": 6.2},
            {"word": "去", "start": 6.2, "end": 6.4},
            {"word": "操作", "start": 6.4, "end": 6.7},
        ]
        text = "大家好今天我们来学习一下这个新的功能首先我们先来看一下这个界面然后我们再一步一步去操作"
        segs = [{"start": 0.0, "end": 8.5, "text": text, "words": words}]
        result = refine(segs, max_chars=20, max_line_ms=4000)
        self.assertEqual(len(result), 3)
        for s in result:
            t = s["text"].replace(" ", "")
            self.assertLessEqual(len(t), 20)
        reconstructed = "".join(s["text"] for s in result).replace(" ", "")
        self.assertEqual(reconstructed, text)

    def test_bad_line_start_penalty(self):
        self.assertIn("和", BAD_LINE_START)
        self.assertIn("for", BAD_LINE_START)

    def test_good_line_start_bonus(self):
        self.assertIn("首先", GOOD_LINE_START)
        self.assertIn("then", BAD_LINE_START)

    def test_strong_punct_set(self):
        self.assertIn("。", STRONG_PUNCT)
        self.assertIn("!", STRONG_PUNCT)
        self.assertNotIn("，", STRONG_PUNCT)

    def test_merge_fragments_offer_ignores_max_chars(self):
        # "off" + "er" rejoin into "offer" even when combined length
        # exceeds max_chars=25 (real case: 横.mp3 / 竖.mp3)
        segs = [
            {"start": 0.0, "end": 3.6, "text": "应该是在大三的暑假拿到一个大厂实习的工作的off"},
            {"start": 3.6, "end": 3.66, "text": "er"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "应该是在大三的暑假拿到一个大厂实习的工作的offer")
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 3.66)

    def test_merge_fragments_forward_remnant(self):
        # remnant "re" attaches forward to "ference"
        segs = [
            {"start": 0.0, "end": 1.0, "text": "这个reference的"},
            {"start": 1.0, "end": 1.05, "text": "re"},
            {"start": 1.05, "end": 2.0, "text": "ference很重要"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "这个reference的")
        self.assertEqual(out[1]["text"], "reference很重要")

    def test_merge_fragments_continuation_only(self):
        # letters joined only when gap is small (near-continuous speech)
        segs = [
            {"start": 0.0, "end": 1.0, "text": "A"},
            {"start": 2.0, "end": 3.0, "text": "B"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "A")
        self.assertEqual(out[1]["text"], "B")

    def test_merge_fragments_chinese_boundary_no_merge(self):
        # Chinese chars at boundary → no merge
        segs = [
            {"start": 0.0, "end": 1.0, "text": "工作的"},
            {"start": 1.0, "end": 2.0, "text": "offer"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)

    def test_merge_fragments_too_long_no_merge(self):
        # combined beyond hard limit → keep separate
        long_prev = "A" * 30
        long_curr = "B" * 40
        segs = [
            {"start": 0.0, "end": 1.0, "text": long_prev},
            {"start": 1.001, "end": 2.0, "text": long_curr},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)

    # ── L1: Chinese remnant merge ─────────────────────────────────────

    def test_merge_cn_remnant_word(self):
        # "作品" + "集" rejoin into "作品集" (real case: 横.mp3)
        segs = [
            {"start": 0.0, "end": 3.0, "text": "第二份的求职作品"},
            {"start": 3.05, "end": 3.2, "text": "集"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "第二份的求职作品集")
        self.assertEqual(out[0]["end"], 3.2)

    def test_merge_cn_remnant_two_chars(self):
        # "东" + "西" (real case: 横.mp3 "…作品集里的东西" split)
        segs = [
            {"start": 0.0, "end": 2.0, "text": "去优化你的求职作品集里的东"},
            {"start": 2.05, "end": 2.2, "text": "西"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "去优化你的求职作品集里的东西")

    def test_merge_cn_remnant_suffix(self):
        # "竞争" + "者" (real case: 横.mp3 "…这一波竞争" + "者")
        segs = [
            {"start": 0.0, "end": 3.0, "text": "工作经验的那一波竞争"},
            {"start": 3.05, "end": 3.2, "text": "者"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "工作经验的那一波竞争者")

    def test_merge_cn_remnant_function_word(self):
        # "的" is a function word — attaches to "比较艰难" without jieba
        # agreeing (jieba splits it standalone). Real case: 横.mp3 #144-145.
        segs = [
            {"start": 0.0, "end": 2.0, "text": "从中场往大厂跳的比较艰难"},
            {"start": 2.05, "end": 2.2, "text": "的"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "从中场往大厂跳的比较艰难的")

    def test_merge_cn_no_merge_word_invalid(self):
        # jieba rejects "来"+"嗯" — not a word, would be a false merge
        segs = [
            {"start": 0.0, "end": 1.0, "text": "那你就是来"},
            {"start": 1.05, "end": 1.2, "text": "嗯"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)

    def test_merge_cn_no_merge_large_gap(self):
        # Chinese remnants join only when gap is small
        segs = [
            {"start": 0.0, "end": 1.0, "text": "作品"},
            {"start": 3.0, "end": 3.2, "text": "集"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)

    def test_merge_cn_no_merge_after_punct(self):
        # prev ends with 句号 → don't attach remnant
        segs = [
            {"start": 0.0, "end": 1.0, "text": "我们走了。"},
            {"start": 1.05, "end": 1.2, "text": "好"},
        ]
        out = _merge_fragments(segs, 25)
        self.assertEqual(len(out), 2)

    # ── L2: semantic-start break scoring ─────────────────────────────

    def test_semantic_start_long_line_rewarded(self):
        # Long line (> _SEMANTIC_MIN_CHARS): break before 因为 should be
        # rewarded (人工精校 breaks before it).
        left = {"word": "工作", "start": 0.0, "end": 0.5}
        right = {"word": "因为", "start": 0.5, "end": 0.9}
        long_score = _score_gap(left, right, 16, 2.0, 25, 4.0, 0.5)
        self.assertGreater(long_score, 0.0)

    def test_semantic_start_short_line_penalized(self):
        # Short line: break before 因为 should still be discouraged
        left = {"word": "工作", "start": 0.0, "end": 0.5}
        right = {"word": "因为", "start": 0.5, "end": 0.9}
        short_score = _score_gap(left, right, 5, 2.0, 25, 4.0, 0.5)
        self.assertLess(short_score, 0.0)

    def test_semantic_start_membership(self):
        self.assertIn("因为", SEMANTIC_START)
        self.assertIn("那", SEMANTIC_START)
        self.assertIn("你", SEMANTIC_START)
        self.assertIn("这种", SEMANTIC_START)

    def test_preferred_max_chars_constant(self):
        self.assertGreater(_PREFERRED_MAX_CHARS, 0)
        self.assertLess(_PREFERRED_MAX_CHARS, 25)
        self.assertGreater(_SEMANTIC_MIN_CHARS, _PREFERRED_MAX_CHARS - 5)

    def test_semantic_break_pending_requires_long_line(self):
        # Segment walk: a long line should break before 因为/那,
        # a short line must not.
        words = [
            _ws("我们", 0.0, 0.3), _ws("现在", 0.3, 0.6),
            _ws("要", 0.6, 0.8), _ws("开始", 0.8, 1.1),
            _ws("准备", 1.1, 1.4), _ws("这个", 1.4, 1.7),
            _ws("项目", 1.7, 2.0), _ws("的", 2.0, 2.2),
            _ws("工作", 2.2, 2.5), _ws("因为", 2.5, 2.8),
            _ws("时间", 2.8, 3.1), _ws("很紧", 3.1, 3.4),
            _ws("了", 3.4, 3.6),
        ]
        out = _segment_words(words, max_chars=25, max_dur=4.0,
                             pause_threshold=0.5)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "我们现在要开始准备这个项目的工作")
        self.assertEqual(out[1]["text"], "因为时间很紧了")

    def test_semantic_break_short_line_no_break(self):
        # Short content with a 因为: must stay one line (no early split)
        words = [
            _ws("我们", 0.0, 0.3), _ws("现在", 0.3, 0.6),
            _ws("因为", 0.6, 0.9), _ws("时间", 0.9, 1.2),
            _ws("很紧", 1.2, 1.5),
        ]
        out = _segment_words(words, max_chars=25, max_dur=4.0,
                             pause_threshold=0.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "我们现在因为时间很紧")


if __name__ == "__main__":
    unittest.main()
