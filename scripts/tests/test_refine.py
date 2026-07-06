import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from refine_segments import (
    refine, _segment_words, _clean_words, _chars,
    STRONG_PUNCT, BAD_LINE_START, GOOD_LINE_START,
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
        self.assertGreater(len(result), 1)

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


if __name__ == "__main__":
    unittest.main()
