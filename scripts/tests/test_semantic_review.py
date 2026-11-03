import unittest
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from semantic_review import _merge_candidates, apply


def _seg(index, start, end, text):
    return {"index": index, "start": start, "end": end, "text": text}


class TestMergeCandidates(unittest.TestCase):
    def test_continuation_is_proposed(self):
        candidates = _merge_candidates([
            _seg(26, 0.0, 1.2, "那个RVT的话它可以blend"),
            _seg(27, 1.2, 1.8, "这个底下"),
        ])
        self.assertEqual(candidates[0]["segments"], [26, 27])
        self.assertEqual(candidates[0]["text"], "那个RVT的话它可以blend这个底下")

    def test_new_idea_is_not_proposed(self):
        candidates = _merge_candidates([
            _seg(1, 0.0, 1.0, "还有另外一种方式"),
            _seg(2, 1.0, 2.0, "就是RVT的方式来做"),
        ])
        self.assertEqual(candidates, [])

    def test_large_pause_is_not_proposed(self):
        candidates = _merge_candidates([
            _seg(1, 0.0, 1.0, "它可以blend"),
            _seg(2, 1.5, 2.0, "这个底下"),
        ])
        self.assertEqual(candidates, [])

    def test_sentence_punctuation_blocks_continuation_merge(self):
        candidates = _merge_candidates([
            _seg(1, 0.0, 1.0, "这个做法已经验证。"),
            _seg(2, 1.0, 2.0, "这个底下还有一层"),
        ])
        self.assertEqual(candidates, [])

    def test_apply_selected_merge_preserves_outer_timestamp(self):
        srt = (
            "1\n00:00:00,000 --> 00:00:01,000\n它可以 blend\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\n这个底下\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "raw.srt"
            plan_path = Path(tmp) / "splits.json"
            srt_path.write_text(srt, encoding="utf-8")
            plan_path.write_text(json.dumps({"_selected_merges": [[1, 2]]}), encoding="utf-8")
            out = "\n".join(apply(str(srt_path), str(plan_path)))
        self.assertIn("00:00:00,000 --> 00:00:02,000", out)
        self.assertIn("它可以 blend 这个底下", out)
        self.assertEqual(out.count("\n2\n"), 0)

    def test_apply_rejects_nonconsecutive_segment_numbers(self):
        srt = (
            "1\n00:00:00,000 --> 00:00:01,000\n它可以 blend\n\n"
            "3\n00:00:01,000 --> 00:00:02,000\n这个底下\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "raw.srt"
            plan_path = Path(tmp) / "splits.json"
            srt_path.write_text(srt, encoding="utf-8")
            plan_path.write_text(json.dumps({"_selected_merges": [[1, 3]]}), encoding="utf-8")
            out = "\n".join(apply(str(srt_path), str(plan_path)))
        self.assertIn("它可以 blend", out)
        self.assertIn("这个底下", out)
        self.assertIn("\n2\n", out)


if __name__ == "__main__":
    unittest.main()
