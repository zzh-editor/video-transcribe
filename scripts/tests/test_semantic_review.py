import unittest
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from semantic_review import _merge_candidates, apply, compact_view


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


class TestCompactView(unittest.TestCase):
    def _plan(self):
        return {
            "5": {
                "text": "注意一下灯光的参数",
                "reason": ["long_dur"],
                "time_reliable": True,
                "seg_start": 12.3,
                "seg_end": 17.24,
                "visible_len": 9,
                "safe_cuts": [
                    {"char": 4, "boundary": "注意一下︱灯光", "start": 14.02, "end": 14.02},
                    {"char": 8, "boundary": "灯光的参︱数", "start": 16.34, "end": 16.34},
                ],
                "selected_cuts": [],
            },
            "_merge_candidates": [
                {"segments": [1, 2], "reason": "continuation",
                 "text": "它可以 blend 这个底下", "gap_ms": 0, "visible_len": 13},
            ],
            "_selected_merges": [],
        }

    def test_markers_carry_char_indexes(self):
        view = compact_view(self._plan())
        self.assertIn("注意一下[1@4]灯光的参[2@8]数", view)

    def test_floats_and_boundaries_are_dropped(self):
        view = compact_view(self._plan())
        for noisy in ("12.3", "17.24", "14.02", "boundary", '"start"'):
            self.assertNotIn(noisy, view)

    def test_merge_candidate_is_kept_compactly(self):
        view = compact_view(self._plan())
        self.assertIn("merge 1-2 continuation", view)

    def test_segment_without_cuts_is_skipped(self):
        plan = self._plan()
        plan["9"] = {"text": "短句", "reason": ["fragment"], "time_reliable": True,
                     "visible_len": 2, "safe_cuts": [], "selected_cuts": []}
        self.assertNotIn("#9", compact_view(plan))

    def test_markers_do_not_change_selected_cut_contract(self):
        plan = self._plan()
        plan["5"]["selected_cuts"] = [4]
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "raw.srt"
            plan_path = Path(tmp) / "splits.json"
            srt_path.write_text("5\n00:00:12,300 --> 00:00:17,240\n注意一下灯光的参数\n", encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            out = "\n".join(apply(str(srt_path), str(plan_path)))
        self.assertIn("注意一下", out)
        self.assertIn("灯光的参数", out)


if __name__ == "__main__":
    unittest.main()
