import copy
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from english_segments import merge_english_fragments, refine_english_segments


class TestEnglishSegments(unittest.TestCase):
    def timed(self, text):
        words = [{"word": token, "start": i * 0.4, "end": (i + 1) * 0.4}
                 for i, token in enumerate(text.split())]
        return [{"text": text, "start": 0, "end": words[-1]["end"]}], words

    def test_punctuation_comes_from_text_when_words_omit_it(self):
        segments, words = self.timed("First, what do I want? This is the story.")
        for w in words:
            w["word"] = w["word"].strip(",.?!")
        original = copy.deepcopy((segments, words))
        out = refine_english_segments(segments, words)
        self.assertEqual([s["text"] for s in out],
                         ["First,", "what do I want?", "This is the story."])
        self.assertEqual((segments, words), original)

    def test_short_clause_and_source_boundaries_are_not_merged(self):
        segments = [{"start": 0, "end": 1, "text": "I think there are three"},
                    {"start": 1, "end": 2, "text": "important questions."}]
        out = refine_english_segments(segments, [])
        self.assertEqual([s["text"] for s in out], [s["text"] for s in segments])
        self.assertTrue(all(s["timing_review"] for s in out))

    def test_no_word_times_never_estimates_cuts(self):
        text = "First, what do I want? This is the story."
        out = refine_english_segments([{"start": 2, "end": 12, "text": text}], [], 20)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["start"], out[0]["end"], out[0]["text"]), (2, 12, text))

    def test_decimal_contraction_and_abbreviation_are_not_split(self):
        text = "Dr. Smith uses Python 3.9. It's fine."
        segments, words = self.timed(text)
        out = refine_english_segments(segments, words)
        self.assertEqual([s["text"] for s in out],
                         ["Dr. Smith uses Python 3.9.", "It's fine."])

    def test_length_split_keeps_sentence_punctuation_and_word_times(self):
        text = "I make the models and I build the space. Then I add lights."
        segments, words = self.timed(text)
        out = refine_english_segments(segments, words, max_chars=30)
        self.assertEqual(" ".join(s["text"] for s in out), text)
        self.assertTrue(all(len(s["text"]) <= 30 for s in out))
        self.assertTrue(any(s["text"].endswith("space.") for s in out))
        self.assertTrue(all(s["end"] in {w["end"] for w in words} for s in out))

    def test_bad_late_word_time_does_not_duplicate_source(self):
        segments, words = self.timed("First, this is fine. Then we finish.")
        words[-1]["start"], words[-1]["end"] = 20, 21
        out = refine_english_segments(segments, words)
        self.assertEqual(" ".join(s["text"] for s in out), segments[0]["text"])
        self.assertTrue(all(s["start"] < s["end"] for s in out))

    def test_words_crossing_windows_do_not_overlap_captions(self):
        segments = [{"start": 0, "end": 1, "text": "One sentence."},
                    {"start": 1, "end": 2, "text": "Another sentence."}]
        words = [{"word": "One", "start": 0, "end": .4},
                 {"word": "sentence.", "start": .4, "end": 1.2},
                 {"word": "Another", "start": .8, "end": 1.4},
                 {"word": "sentence.", "start": 1.4, "end": 2}]
        out = refine_english_segments(segments, words)
        self.assertEqual(out[0]["end"], out[1]["start"])
        self.assertEqual([s["text"] for s in out], [s["text"] for s in segments])

    def test_custom_pause_and_small_speech_gap(self):
        segments = [{"text": "Keep this together", "start": 0, "end": 3}]
        words = [{"word": "Keep", "start": 0, "end": .5},
                 {"word": "this", "start": .8, "end": 1},
                 {"word": "together", "start": 1, "end": 2}]
        self.assertEqual(len(refine_english_segments(segments, words)), 1)
        out = refine_english_segments(segments, words, pause_threshold=.2)
        self.assertEqual([s["text"] for s in out], ["Keep", "this together"])


class TestMergeEnglishFragments(unittest.TestCase):
    def cue(self, start, end, text):
        return {"start": start, "end": end, "text": text, "words": []}

    def test_fragment_at_next_boundary_is_joined(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "Use the menu"),
                                       self.cue(2.1, 2.12, "to"),
                                       self.cue(2.2, 3.0, "open settings.")])
        self.assertEqual([s["text"] for s in out], ["Use the menu to open settings."])
        self.assertEqual((out[0]["start"], out[0]["end"]), (0.0, 3.0))

    def test_adjacent_unpunctuated_fragments_merge_into_an_idea(self):
        out = merge_english_fragments([self.cue(0.0, 0.4, "Please send"),
                                       self.cue(0.4, 0.8, "the next"),
                                       self.cue(0.8, 1.4, "piece.")])
        self.assertEqual([s["text"] for s in out], ["Please send the next piece."])

    def test_lowercase_continuation_merges_when_the_previous_cue_is_unpunctuated(self):
        out = merge_english_fragments([self.cue(0.0, 1.0, "Please check the map"),
                                       self.cue(1.0, 2.0, "before export.")])
        self.assertEqual([s["text"] for s in out], ["Please check the map before export."])

    def test_complete_clause_stays_separate_from_a_following_prepositional_phrase(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "It's a CG contest"),
                                       self.cue(2.0, 3.0, "for the students.")])
        self.assertEqual([s["text"] for s in out],
                         ["It's a CG contest", "for the students."])

    def test_merge_does_not_exceed_the_line_length(self):
        out = merge_english_fragments([self.cue(0.0, 0.4, "Please send"),
                                       self.cue(0.4, 0.8, "the next piece.")],
                                      max_chars=15)
        self.assertEqual([s["text"] for s in out], ["Please send", "the next piece."])

    def test_sentence_punctuation_is_a_merge_barrier_even_for_a_flash_cue(self):
        out = merge_english_fragments([self.cue(0.0, 0.1, "Yes."),
                                       self.cue(0.1, 0.7, "Tell me more.")])
        self.assertEqual([s["text"] for s in out], ["Yes.", "Tell me more."])

        out = merge_english_fragments([self.cue(0.0, 0.4, "I think we can do this"),
                                       self.cue(0.4, 0.5, "Yeah.")])
        self.assertEqual([s["text"] for s in out], ["I think we can do this", "Yeah."])

        out = merge_english_fragments([self.cue(0.0, 0.1, "Please send,"),
                                       self.cue(0.1, 0.7, "the file.")])
        self.assertEqual([s["text"] for s in out], ["Please send,", "the file."])

    def test_merge_keeps_timing_review_from_either_fragment(self):
        first = self.cue(0.0, 0.4, "Please send")
        second = self.cue(0.4, 0.8, "the file.")
        second["timing_review"] = True
        out = merge_english_fragments([first, second])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["timing_review"])

    def test_fragment_at_previous_boundary_is_joined(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "We need to send"),
                                       self.cue(2.0, 2.1, "it")])
        self.assertEqual([s["text"] for s in out], ["We need to send it"])

    def test_fragment_across_a_long_pause_is_kept(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "first phrase"),
                                       self.cue(5.0, 5.05, "piece"),
                                       self.cue(9.0, 10.0, "last phrase")])
        self.assertEqual([s["text"] for s in out], ["first phrase", "piece", "last phrase"])

    def test_readable_cue_is_never_merged(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "First thought"),
                                       self.cue(2.0, 2.4, "Second idea")])
        self.assertEqual([s["text"] for s in out], ["First thought", "Second idea"])

    def test_single_frame_cue_is_stretched_into_the_gap(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "first phrase"),
                                       self.cue(2.5, 2.52, "Yeah."),
                                       self.cue(3.1, 3.5, "next phrase")])
        self.assertEqual([s["text"] for s in out], ["first phrase", "Yeah.", "next phrase"])
        self.assertGreaterEqual(out[1]["end"] - out[1]["start"], 0.4)
        self.assertLessEqual(out[1]["end"], out[2]["start"])

    def test_single_frame_cue_does_not_stretch_across_a_long_silence(self):
        out = merge_english_fragments([self.cue(0.0, 2.0, "first phrase"),
                                       self.cue(2.5, 2.52, "Okay,"),
                                       self.cue(35.0, 36.0, "later")])
        self.assertLess(out[1]["end"] - out[1]["start"], 1.5)
