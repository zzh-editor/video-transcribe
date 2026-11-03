"""Punctuation-first English captions aligned to Groq's original words."""

import re
from difflib import SequenceMatcher


_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "e.g.", "i.e.", "vs."}
_DEPENDENT_ENDS = set("a an the to of for with in into on at by and or but my your his her its our their".split())
_FRAGMENT_ENDS = _DEPENDENT_ENDS | set(
    "me us them this these those that it we they you i he she".split()
)
_FRAGMENT_STARTS = set("a an the".split())
_PREPOSITIONS = set("to of for with in into on at by".split())
_CLAUSE_STARTS = set("and but because when while if so which who".split())
_COMPLETE_SHORT_CUES = {
    "yes", "no", "yeah", "yep", "nope", "ok", "okay", "right", "sure",
    "thanks", "thank you", "sorry", "exactly", "wow",
}
_FRAGMENT_GAP_S = 0.4    # adjacent fragments this close usually belong together


def _key(text):
    return "".join(ch.casefold() for ch in text if ch.isalnum())


def _punctuation_end(token):
    token = token.rstrip('\"\u201d\u2019)]}')
    if token.casefold() in _ABBREVIATIONS or re.fullmatch(r"(?:[A-Za-z]\.){2,}", token):
        return False
    return token.endswith((",", ".", ";", ":", "!", "?"))


def refine_english_segments(segments, words, max_chars=60, max_line_ms=6000,
                            pause_threshold=0.8):
    """Keep source text/order and segment boundaries; never estimate internal times.

    Align lexically before looking at windows: Groq's segment windows can assign
    a boundary word to the wrong segment, even though its word time is correct.
    """
    tokens_by_segment = []
    for seg in segments:
        tokens = []
        for token in seg.get("text", "").split():
            if tokens and not _key(token):
                tokens[-1] += token
            else:
                tokens.append(token)
        tokens_by_segment.append(tokens)
    flat = [token for tokens in tokens_by_segment for token in tokens]
    valid_words = [w for w in words if _key(w.get("word", ""))
                   and isinstance(w.get("start"), (int, float))
                   and isinstance(w.get("end"), (int, float))
                   and w["end"] > w["start"]]
    aligned = {}
    matcher = SequenceMatcher(None, [_key(t) for t in flat],
                              [_key(w["word"]) for w in valid_words], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            aligned[a + offset] = valid_words[b + offset]

    out = []
    offset = 0
    for seg, tokens in zip(segments, tokens_by_segment):
        timed = [aligned.get(offset + i) for i in range(len(tokens))]
        offset += len(tokens)
        if not tokens:
            continue
        # A repeated phrase aligned far outside its source window is not evidence.
        timed = [w if w and w["end"] >= seg["start"] - 2
                 and w["start"] <= seg["end"] + 2 else None for w in timed]
        safe = {i for i in range(1, len(tokens))
                if timed[i - 1] and timed[i]
                and seg["start"] < timed[i - 1]["end"] <= timed[i]["start"] < seg["end"]}
        boundaries = [i for i in sorted(safe)
                      if _punctuation_end(tokens[i - 1])
                      or timed[i]["start"] - timed[i - 1]["end"] > pause_threshold]
        boundaries.append(len(tokens))
        start = 0
        pieces = []
        for stop in boundaries:
            while start < stop:
                end = stop
                text = " ".join(tokens[start:stop])
                duration = (timed[stop - 1]["end"] - timed[start]["start"]
                            if timed[start] and timed[stop - 1] else 0)
                if len(text) > max_chars or duration * 1000 > max_line_ms:
                    candidates = [i for i in safe if start < i < stop
                                  and len(" ".join(tokens[start:i])) <= max_chars]
                    if candidates:
                        def score(i):
                            length = len(" ".join(tokens[start:i]))
                            value = length / max_chars
                            if _key(tokens[i - 1]) in _DEPENDENT_ENDS:
                                value -= 3
                            if _key(tokens[i]) in _CLAUSE_STARTS:
                                value += 2
                            if length < max_chars / 3:
                                value -= 2
                            if len(" ".join(tokens[i:stop])) < 12:
                                value -= 2
                            value += min(timed[i]["start"] - timed[i - 1]["end"], 0.8)
                            return value
                        end = max(candidates, key=score)
                chunk = " ".join(tokens[start:end])
                begin_time = max(seg["start"], timed[start]["start"]) if timed[start] else seg["start"]
                end_time = min(seg["end"], timed[end - 1]["end"]) if timed[end - 1] else seg["end"]
                if end_time <= begin_time:
                    # Preserve the whole source window on inconsistent alignment.
                    pieces = [{**seg, "words": [], "timing_review": True}]
                    break
                child = {**seg, "start": begin_time, "end": end_time, "text": chunk,
                         "words": [{**w, "word": token} for token, w in
                                   zip(tokens[start:end], timed[start:end]) if w]}
                if any(w is None for w in timed[start:end]):
                    child["timing_review"] = True
                pieces.append(child)
                start = end
            else:
                continue
            break
        out.extend(pieces)
    return out


def _word_tokens(text):
    return [token for token in text.split() if _key(token)]


def _complete_short_cue(text):
    return " ".join(_key(token) for token in _word_tokens(text)) in _COMPLETE_SHORT_CUES


def _mergeable_fragments(left, right, max_chars):
    left_text = left.get("text", "").strip()
    right_text = right.get("text", "").strip()
    if not left_text or not right_text or _complete_short_cue(right_text):
        return False
    if _punctuation_end(left_text.split()[-1]):
        return False
    gap = right["start"] - left["end"]
    merged = f"{left_text} {right_text}"
    if gap < -0.2 or gap > _FRAGMENT_GAP_S or len(merged) > max_chars:
        return False

    left_words = _word_tokens(left_text)
    right_words = _word_tokens(right_text)
    if not left_words or not right_words:
        return False
    left_end = _key(left_words[-1])
    right_start = _key(right_words[0])
    right_raw = right_words[0].lstrip('"\u201c\u2018(')
    starts_lowercase = next((char for char in right_raw if char.isalpha()), "").islower()
    left_is_fragment = left_end in _FRAGMENT_ENDS
    right_is_fragment = right_start in _FRAGMENT_STARTS
    right_is_short = len(right_words) == 1
    lowercase_continuation = starts_lowercase and right_start not in _PREPOSITIONS
    return left_is_fragment or right_is_fragment or right_is_short or lowercase_continuation


def merge_english_fragments(segments: list[dict], max_chars: int = 60) -> list[dict]:
    """Join adjacent unpunctuated fragments; keep clause and sentence breaks."""
    out: list[dict] = []
    for segment in segments:
        current = dict(segment)
        if out and _mergeable_fragments(out[-1], current, max_chars):
            previous = out[-1]
            previous["text"] = f"{previous['text'].strip()} {current['text'].strip()}"
            previous["start"] = min(previous["start"], current["start"])
            previous["end"] = max(previous["end"], current["end"])
            previous["words"] = (previous.get("words") or []) + (current.get("words") or [])
            if previous.get("timing_review") or current.get("timing_review"):
                previous["timing_review"] = True
        else:
            out.append(current)
    return _ensure_readable(out)


def _ensure_readable(segments: list[dict], min_display_s: float = 0.4,
                     max_stretch_s: float = 1.0) -> list[dict]:
    """Give every cue enough screen time to be read.

    A caption that lives for 0.02s is a single frame: the word is in the file
    but never seen. The cue is stretched into the room its neighbours leave,
    but never across a long silence — a gap of half a minute is not screen
    time, it is a missing cue.
    """
    for i, seg in enumerate(segments):
        if seg["end"] - seg["start"] >= min_display_s:
            continue
        next_start = segments[i + 1]["start"] if i + 1 < len(segments) else None
        prev_end = segments[i - 1]["end"] if i > 0 else None
        if next_start is not None and next_start - seg["start"] >= min_display_s:
            seg["end"] = min(next_start, seg["start"] + max_stretch_s)
        elif prev_end is not None and seg["end"] - prev_end >= min_display_s:
            seg["start"] = max(prev_end, seg["end"] - max_stretch_s)
    return segments
