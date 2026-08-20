#!/usr/bin/env python3
"""
Groq word-level timestamp adapter for Chinese segmentation.

Takes Groq API top-level character-level words, aligns them to segment text,
uses jieba to aggregate into phrase-level timed units, then applies the
existing scoring engine only to abnormal (overlong/overduration) segments.
"""

import unicodedata
import warnings
from pathlib import Path

from refine_segments import (
    _segment_words,
    _fallback_split,
    _MERGE_GAP_S,
)

_PUNCT = frozenset("，、。？！；：,.;:!?…\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f"
                   "\u300a\u300b\u3008\u3009\uff08\uff09\uff3b\uff3d"
                   "\u3010\u3011\u300c\u300d" + "\u201c\u201d''\u300e\u300f()[]\u300a\u300b")

# ── Silent-tail shrinkage ────────────────────────────────────────────
# Groq wraps long silence INTO segments (5s window holding 2-4 chars).
# We shrink a segment's end back to its last word when trailing silence
# dominates the window.

_SILENT_PAD_S = 0.2            # keep a small tail after the last word
_MIN_SILENT_SHRINK_S = 1.0     # only shrink when we save >= 1s
_SILENT_RATIO_THRESHOLD = 0.4  # speech span / segment duration below this = bloated

# ── Short-fragment merging ───────────────────────────────────────────
_FRAGMENT_CHAR_LIMIT = 5       # candidate crumb segment: <= 5 visible chars
# Groq wraps speech in ~5s windows; crumbs from the same utterance can sit
# several seconds apart because each window's start is its own silence pad.
# Use a generous gap for crumb runs (word-level when available, window-level
# otherwise) so the sentence can be glued back together.
_MERGE_CRUMB_GAP_S = 5.0

# ── Isolated crumb absorption ────────────────────────────────────────
# A lone filler word (OK/然后/对, <=3 chars) can sit in a seconds-long silent
# window: Groq's word timestamps for these span the whole window (unreliable),
# so a 4-11s solo word is produced. When the next segment starts seamlessly
# (gap <= threshold) the word is absorbed into its head, restoring flow.
_ISOLATED_CHAR_LIMIT = 3   # <= 3 visible chars = isolated filler word
_ISOLATED_DUR_S = 3.0      # window longer than this = silence-bloated
_ABSORB_GAP_S = 0.5        # next segment starts within this = seamless

# ── Quality metrics ─────────────────────────────────────────────────
# Groq verbose_json returns per-segment quality signals used by cleanup to
# detect silence-window hallucinations. New segments produced by splitting or
# merging inherit these from their parent so cleanup sees them later.
_QUALITY_KEYS = ("no_speech_prob", "avg_logprob", "compression_ratio")


def _inherit_quality(parent: dict, children: list[dict]) -> list[dict]:
    """Copy Groq quality metrics from a parent segment onto children produced
    by splitting it. Leaves the originals untouched when not present."""
    inherited = {k: parent[k] for k in _QUALITY_KEYS if k in parent}
    if not inherited:
        return children
    return [{**c, **inherited} for c in children]


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    return ('\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf'
            or ch in "。，、！？；：…·—")


def _join_text(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if _is_cjk(a[-1]) and _is_cjk(b[0]):
        return a + b
    return a + " " + b


def _absorb_isolated_crumbs(segments: list[dict]) -> list[dict]:
    """Absorb lone filler words (<=3 chars) sitting in seconds-long silent
    windows into the seamless next segment. Their word timestamps are
    unreliable (they span the whole window), so keeping them as a multi-second
    solo word is wrong; merging restores sentence flow."""
    if len(segments) < 2:
        return segments
    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        text = seg.get("text", "").strip()
        n_chars = len(text.replace(" ", "").replace("\u200b", ""))
        dur = (seg.get("end", 0) or 0) - (seg.get("start", 0) or 0)
        is_isolated = n_chars <= _ISOLATED_CHAR_LIMIT and dur > _ISOLATED_DUR_S
        if is_isolated and i + 1 < n:
            nxt = segments[i + 1]
            gap = (nxt.get("start", 0) or 0) - (seg.get("end", 0) or 0)
            if gap <= _ABSORB_GAP_S:
                nxt_text = nxt.get("text", "").strip()
                nxt["text"] = _join_text(text, nxt_text)
                i += 1
                continue
        out.append(seg)
        i += 1
    return out


def _get_script_dir() -> Path:
    return Path(__file__).resolve().parent


def _get_dict_path() -> Path:
    return _get_script_dir().parent / "data" / "jieba_domain_dict.txt"


def _get_tokenizer():
    import jieba
    tok = jieba.Tokenizer()
    dict_path = _get_dict_path()
    if dict_path.exists():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            tok.load_userdict(str(dict_path))
    return tok


def _overlap(word_start: float, word_end: float, seg_start: float, seg_end: float) -> float:
    return max(0.0, min(word_end, seg_end) - max(word_start, seg_start))


def _assign_words_to_segments(words: list[dict], segments: list[dict]) -> list[list[dict]]:
    assigned = [[] for _ in segments]
    for w in words:
        ws, we = w["start"], w["end"]
        best_idx = -1
        best_ov = -1.0
        for i, seg in enumerate(segments):
            ov = _overlap(ws, we, seg["start"], seg["end"])
            if ov > best_ov:
                best_ov = ov
                best_idx = i
        if best_idx < 0 or best_ov <= 0:
            continue
        wp = (ws + we) / 2
        for i, seg in enumerate(segments):
            if i == best_idx:
                continue
            o2 = _overlap(ws, we, seg["start"], seg["end"])
            if abs(o2 - best_ov) < 1e-6:
                if wp >= seg["start"] and wp <= seg["end"]:
                    if abs(wp - (seg["start"] + seg["end"]) / 2) < abs(wp - (segments[best_idx]["start"] + segments[best_idx]["end"]) / 2):
                        best_idx = i
                    elif abs(wp - (seg["start"] + seg["end"]) / 2) == abs(wp - (segments[best_idx]["start"] + segments[best_idx]["end"]) / 2):
                        best_idx = min(i, best_idx)
        assigned[best_idx].append(w)
    return assigned


def _build_char_time_map(seg_text: str, groq_words: list[dict]) -> dict:
    if not groq_words:
        return {}
    groq_concat = ""
    groq_times = []
    for w in groq_words:
        for ch in w["word"]:
            groq_concat += ch
            groq_times.append((w["start"], w["end"]))
    text_no_ws = seg_text.replace(" ", "").replace("\u200b", "")
    norm_match = unicodedata.normalize("NFKC", text_no_ws.replace("", ""))
    norm_groq = unicodedata.normalize("NFKC", groq_concat)
    content_positions = []
    punct_positions = []
    for i, ch in enumerate(text_no_ws):
        if ch in _PUNCT:
            punct_positions.append(i)
        else:
            content_positions.append(i)
    content_text = "".join(text_no_ws[i] for i in content_positions)
    if unicodedata.normalize("NFKC", content_text) != norm_groq:
        return {}
    result = {}
    gi = 0
    text_idx = 0
    for orig_i, ch in enumerate(seg_text):
        if ch.isspace():
            continue
        if ch in _PUNCT:
            if gi > 0 and gi - 1 < len(groq_times):
                result[orig_i] = groq_times[gi - 1]
            elif gi < len(groq_times):
                result[orig_i] = groq_times[gi]
            elif groq_times:
                result[orig_i] = groq_times[-1]
            continue
        if gi < len(groq_times):
            result[orig_i] = groq_times[gi]
        gi += 1
    for orig_i, ch in enumerate(seg_text):
        if orig_i not in result and not ch.isspace() and groq_times:
            result[orig_i] = groq_times[-1]
    return result


def _check_coverage(seg_text: str, char_times: dict) -> float:
    total = 0
    covered = 0
    for i, ch in enumerate(seg_text):
        if ch.isspace():
            continue
        if ch in _PUNCT:
            continue
        total += 1
        if i in char_times:
            covered += 1
    if total == 0:
        return 1.0
    return covered / total


def _build_phrased_units(seg_text: str, char_times: dict) -> list[dict]:
    tok = _get_tokenizer()
    tokens = list(tok.tokenize(seg_text))
    units = []
    for word, start, end in tokens:
        if not word.strip():
            continue
        char_start_t = char_times.get(start)
        char_end_t = char_times.get(end - 1) if end - 1 >= start else char_times.get(start)
        if char_start_t is None or char_end_t is None:
            return []
        units.append({
            "word": word,
            "start": char_start_t[0],
            "end": char_end_t[1],
        })
    return units


def _is_ascii_ident(word: str) -> bool:
    for ch in word:
        if ch.isascii():
            if not (ch.isalnum() or ch in "+-#_.&/"):
                return False
        else:
            return False
    return True


def _protect_technical_tokens(units: list[dict]) -> list[dict]:
    if not units:
        return units
    merged = [units[0]]
    for u in units[1:]:
        prev = merged[-1]
        prev_text = prev["word"].strip()
        curr_text = u["word"].strip()
        if not prev_text or not curr_text:
            merged.append(u)
            continue
        is_prev_ascii = _is_ascii_ident(prev_text)
        is_curr_ascii = _is_ascii_ident(curr_text)
        if is_prev_ascii and is_curr_ascii:
            gap_text = prev["word"] + u["word"]  # no extra space, just concat adjacent units
            merged[-1] = {
                "word": gap_text,
                "start": prev["start"],
                "end": u["end"],
            }
        elif is_prev_ascii and not is_curr_ascii:
            merged.append(u)
        elif not is_prev_ascii and is_curr_ascii:
            merged.append(u)
        else:
            merged.append(u)
    return merged


def _is_abnormal(seg: dict, max_chars: int, max_line_ms: int) -> bool:
    text = seg.get("text", "").strip()
    stripped = text.replace(" ", "").replace("\u200b", "")
    char_count = len(stripped)
    duration = (seg.get("end", 0) or 0) - (seg.get("start", 0) or 0)
    duration_ms = duration * 1000
    if char_count > max_chars:
        return True
    if duration_ms > max_line_ms:
        return True
    return False


def _speech_span(seg_words: list[dict]) -> float:
    """Actual speech span (first word start → last word end), ignoring the
    silence padding Groq bakes into the segment window."""
    if len(seg_words) < 2:
        return 0.0
    return seg_words[-1]["end"] - seg_words[0]["start"]


def _shrink_silent_tail(seg: dict, seg_words: list[dict]) -> dict:
    """Shrink a segment's end back to its last word (+ small pad) when the
    segment window is bloated by trailing silence. Returns the original
    segment when there's nothing meaningful to trim (or no words at all)."""
    if not seg_words:
        return seg
    seg_start = seg.get("start", 0)
    seg_end = seg.get("end", 0)
    seg_dur = seg_end - seg_start
    if seg_dur <= 0:
        return seg
    last_end = seg_words[-1]["end"]
    if last_end < seg_start or last_end >= seg_end:
        return seg
    span = _speech_span(seg_words)
    saved = seg_end - last_end
    if saved < _MIN_SILENT_SHRINK_S:
        return seg
    if seg_dur > 0 and span / seg_dur > _SILENT_RATIO_THRESHOLD:
        return seg
    out = dict(seg)
    out["end"] = last_end + _SILENT_PAD_S
    return out


def _split_seg_with_words(
    seg: dict,
    seg_words: list[dict],
    max_chars: int,
    max_line_ms: int,
    pause_threshold: float,
) -> list[dict]:
    """Try a scoring-based split using word timestamps; fall back to the
    ratio/punctuation splitter on any failure. Returns >=1 segments."""
    char_times = _build_char_time_map(seg["text"], seg_words)
    if not char_times:
        return _fallback_split(seg, max_chars, max_line_ms)
    coverage = _check_coverage(seg["text"], char_times)
    if coverage < 0.9:
        return _fallback_split(seg, max_chars, max_line_ms)
    phrased = _build_phrased_units(seg["text"], char_times)
    if not phrased:
        return _fallback_split(seg, max_chars, max_line_ms)
    phrased = _protect_technical_tokens(phrased)
    if len(phrased) < 2:
        return [seg]
    max_dur = max_line_ms / 1000.0
    lines = _segment_words(
        phrased,
        max_chars=max_chars,
        max_dur=max_dur,
        pause_threshold=pause_threshold,
    )
    if not lines:
        return [seg]
    combined_text = "".join(l.get("text", "") for l in lines)
    if (combined_text.replace(" ", "").replace("\u200b", "")
            != seg["text"].replace(" ", "").replace("\u200b", "")):
        return [seg]
    return lines


def _merge_fragments_groq(segments: list[dict]) -> list[dict]:
    """Merge runs of adjacent tiny segments (Groq splits a sentence into
    2-4 char crumbs spread across ~5s silence-bloated windows)."""
    if len(segments) < 2:
        return segments

    def _crumb_gap(prev_seg: dict, nxt_seg: dict) -> float:
        """Distance between two candidate crumbs. Uses word timestamps when
        both sides carry them (speech-to-speech gap), otherwise falls back to
        the window boundary gap."""
        prev_words = prev_seg.get("words", [])
        nxt_words = nxt_seg.get("words", [])
        if prev_words and nxt_words:
            return nxt_words[0]["start"] - prev_words[-1]["end"]
        return nxt_seg["start"] - prev_seg["end"]

    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        text = seg.get("text", "").strip()
        if len(text.replace(" ", "").replace("\u200b", "")) > _FRAGMENT_CHAR_LIMIT:
            out.append(seg)
            i += 1
            continue
        buf_text = text
        buf_start = seg["start"]
        buf_end = seg["end"]
        buf_words = list(seg.get("words", []))
        last = seg
        j = i + 1
        while j < n:
            nxt = segments[j]
            nxt_text = nxt.get("text", "").strip()
            if len(nxt_text.replace(" ", "").replace("\u200b", "")) > _FRAGMENT_CHAR_LIMIT:
                break
            if _crumb_gap(last, nxt) > _MERGE_CRUMB_GAP_S:
                break
            buf_text += nxt_text
            buf_end = nxt["end"]
            buf_words.extend(nxt.get("words", []))
            last = nxt
            j += 1
        if j - i >= 2:
            merged = {
                "start": buf_start,
                "end": buf_end,
                "text": buf_text,
            }
            for k in _QUALITY_KEYS:
                if k in last:
                    merged[k] = last[k]
            if buf_words:
                merged["words"] = buf_words
                merged = _shrink_silent_tail(merged, buf_words)
            out.append(merged)
            i = j
            continue
        out.append(seg)
        i += 1
    return out


def _verify_text(units: list[dict], orig_text: str) -> bool:
    reconstructed = "".join(u["word"] for u in units)
    reco_stripped = reconstructed.replace(" ", "").replace("\u200b", "")
    orig_stripped = orig_text.replace(" ", "").replace("\u200b", "")
    return reco_stripped == orig_stripped


def _verify_times(units: list[dict], seg_start: float, seg_end: float) -> bool:
    for u in units:
        if u["start"] >= u["end"]:
            return False
        if u["start"] < seg_start - 0.05 or u["end"] > seg_end + 0.05:
            return False
    for i in range(1, len(units)):
        if units[i]["start"] < units[i - 1]["end"] - 0.01:
            return False
    return True


def refine_groq_segments(
    segments: list[dict],
    top_words: list[dict],
    max_chars: int = 25,
    max_line_ms: int = 4000,
    pause_threshold: float = 0.3,
    full_segment: bool = False,
) -> list[dict]:
    """Refine Groq segments.

    - Abnormal (overlong/overduration) segments are always re-segmented
      using the scoring engine (or the fallback splitter).
    - Normal segments keep their original boundary, but when the window is
      bloated by trailing silence they are shrunk to the last word.
    - When ``full_segment`` is True every segment WITH usable word timestamps
      is passed through the scoring engine (not just abnormal ones).
    - Finally, runs of adjacent tiny crumbs are merged back together.
    """
    assigned = _assign_words_to_segments(top_words, segments)
    out = []
    for seg_idx, seg in enumerate(segments):
        seg_words = assigned[seg_idx]
        has_content_words = bool(seg_words)
        if not has_content_words:
            out.append(seg)
            continue
        if not _is_abnormal(seg, max_chars, max_line_ms):
            if full_segment:
                out.extend(_inherit_quality(seg, _split_seg_with_words(
                    seg, seg_words, max_chars, max_line_ms, pause_threshold)))
            else:
                out.append(_shrink_silent_tail(seg, seg_words))
            continue
        out.extend(_inherit_quality(seg, _split_seg_with_words(
            seg, seg_words, max_chars, max_line_ms, pause_threshold)))
    out = _merge_fragments_groq(out)
    return _absorb_isolated_crumbs(out)
