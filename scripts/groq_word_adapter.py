#!/usr/bin/env python3
"""
Groq word-level timestamp adapter for Chinese segmentation.

Takes Groq API top-level character-level words, aligns them to segment text,
uses jieba to aggregate into phrase-level timed units, then applies the
existing scoring engine only to abnormal (overlong/overduration) segments.
"""

import sys
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

# ── Silent-tail/head shrinkage ───────────────────────────────────────
# Groq wraps long silence INTO segments (5s window holding 2-4 chars).
# We shrink a segment's end/start back to its first/last word when silence
# dominates the window. Both edges are trimmed — the original tail-only
# shrink left leading silence bloat (e.g. a 13s segment starting 1.8s before
# speech).

_SILENT_PAD_S = 0.2            # keep a small pad beyond speech
_MIN_SILENT_SHRINK_S = 0.8     # only shrink when we save >= 0.8s
_SILENT_RATIO_THRESHOLD = 0.4  # speech span / segment duration below this = bloated

# ── Silence-triggered split ────────────────────────────────────────────
# Normal (non-abnormal) segments were previously kept as-is even when they
# internally span 2-16s of silence (19 cases in W6单元2, e.g. #87 2.3s,
# #167 5.9s, #394 6.9s). Those wrap two independent utterances into one
# double-length subtitle. If the largest internal word gap exceeds this
# threshold we force a scoring split even for otherwise-normal segments.
_SILENCE_SPLIT_GAP_S = 0.80   # word gap > this inside a normal segment → split

# ── Overlap repair ─────────────────────────────────────────────────────
# 4 overlapping segments were observed in Enhance output (279-500ms). Groq
# windows overlap; post-processing can leave start < prev_end. A final
# de-overlap trims prev end at cur start.
_OVERLAP_EPS_S = 0.02  # keep 20ms gap after trim

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

# Single-char CJK function words that naturally attach to previous tail
_CN_FUNCTION_WORDS = frozenset("的得地了着过吧吗呢啊呀哦")


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
    import sys
    tok = jieba.Tokenizer()
    dict_path = _get_dict_path()
    if dict_path.exists():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            tok.load_userdict(str(dict_path))
    else:
        print(f"warning: jieba domain dict not found at {dict_path}, using default dictionary",
              file=sys.stderr)
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
            # Keep a space between two alphabetic words (Rookie Awards) but
            # not for symbols (C + + -> C++).
            if prev_text.isalpha() and curr_text.isalpha():
                gap_text = prev["word"] + " " + u["word"]
            else:
                gap_text = prev["word"] + u["word"]
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


def _max_internal_gap(seg_words: list[dict]) -> float:
    """Largest gap between consecutive words inside a segment."""
    if len(seg_words) < 2:
        return 0.0
    return max(
        seg_words[i]["start"] - seg_words[i - 1]["end"]
        for i in range(1, len(seg_words))
    )


def _has_large_pause(seg_words: list[dict], threshold: float = _SILENCE_SPLIT_GAP_S) -> bool:
    return _max_internal_gap(seg_words) > threshold


def _shrink_silent_edges(seg: dict, seg_words: list[dict]) -> dict:
    """Shrink both leading and trailing silence. Returns original when not bloated."""
    if not seg_words:
        return seg
    seg_start = seg.get("start", 0)
    seg_end = seg.get("end", 0)
    seg_dur = seg_end - seg_start
    if seg_dur <= 0:
        return seg
    first_start = seg_words[0]["start"]
    last_end = seg_words[-1]["end"]
    # Clamp word times inside window
    if first_start < seg_start:
        first_start = seg_start
    if last_end > seg_end:
        last_end = seg_end
    span = last_end - first_start if len(seg_words) >= 2 else seg_dur
    # Not bloated → keep as-is (speech fills the window)
    if seg_dur > 0 and span / seg_dur > _SILENT_RATIO_THRESHOLD:
        return seg
    out = dict(seg)
    # Leading shrink
    leading_saved = first_start - seg_start
    if leading_saved >= _MIN_SILENT_SHRINK_S and first_start > seg_start:
        out["start"] = max(seg_start, first_start - _SILENT_PAD_S)
    # Trailing shrink
    trailing_saved = seg_end - last_end
    if trailing_saved >= _MIN_SILENT_SHRINK_S and last_end < seg_end:
        out["end"] = last_end + _SILENT_PAD_S
    # Guard against inversion
    if out["end"] <= out["start"]:
        return seg
    return out


def _deoverlap(segments: list[dict]) -> list[dict]:
    """Ensure segments do not overlap in time. Trims previous end to next start."""
    if len(segments) < 2:
        return segments
    # Sort by start to be safe
    segments = sorted(segments, key=lambda s: s.get("start", 0))
    out = [dict(segments[0])]
    for seg in segments[1:]:
        prev = out[-1]
        cur = dict(seg)
        if cur.get("start", 0) < prev.get("end", 0):
            # Overlap detected; trim prev end
            prev["end"] = max(prev["start"] + 0.1, cur["start"] - _OVERLAP_EPS_S)
        out.append(cur)
    return out


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


def _split_at_silence(
    seg: dict,
    seg_words: list[dict],
    gap_threshold: float = _SILENCE_SPLIT_GAP_S,
) -> list[dict] | None:
    """Hard split a segment at word gaps > gap_threshold. Returns None if no
    large gap or if split would produce empty pieces. Each piece inherits
    quality metrics and is clamped to its word span + pad."""
    # Find gap indices
    gaps = []
    for i in range(1, len(seg_words)):
        gap = seg_words[i]["start"] - seg_words[i - 1]["end"]
        if gap > gap_threshold:
            gaps.append(i)
    if not gaps:
        return None
    # Need char-time map to slice text accurately
    char_times = _build_char_time_map(seg["text"], seg_words)
    if not char_times:
        return None
    coverage = _check_coverage(seg["text"], char_times)
    if coverage < 0.9:
        return None
    # Build word → char index mapping via tokenizer units
    phrased = _build_phrased_units(seg["text"], char_times)
    if not phrased:
        return None
    phrased = _protect_technical_tokens(phrased)
    # Phrased boundaries correspond to char-time units; but we need to map
    # gap positions in seg_words to phrased indices. Use time overlap:
    # assign each phrased unit to the nearest seg_word by start time.
    # Simpler: reuse seg_words word boundaries directly for split points:
    # map seg_words gap index → character position in seg["text"] via char_times keys
    # char_times keys are orig indices; we can approximate split at word boundary
    # by slicing seg["text"] at the phrased unit that starts at that word time.
    pieces: list[dict] = []
    prev_word_idx = 0
    for gap_idx in gaps + [len(seg_words)]:
        chunk_words = seg_words[prev_word_idx:gap_idx]
        if not chunk_words:
            prev_word_idx = gap_idx
            continue
        # Slice text via phrased units whose time falls inside chunk
        c_start = chunk_words[0]["start"]
        c_end = chunk_words[-1]["end"]
        chunk_phrased = [u for u in phrased if u["start"] >= c_start - 0.05 and u["end"] <= c_end + 0.05]
        if not chunk_phrased:
            # Fallback: use word strings directly
            text_piece = "".join(w.get("word", "") for w in chunk_words)
        else:
            text_piece = "".join(u.get("word", "") for u in chunk_phrased)
        text_piece = text_piece.strip()
        if not text_piece:
            prev_word_idx = gap_idx
            continue
        piece = {
            "start": chunk_words[0]["start"] - _SILENT_PAD_S * 0.5,
            "end": chunk_words[-1]["end"] + _SILENT_PAD_S,
            "text": text_piece,
            "words": list(chunk_words),
        }
        # Clamp inside original window
        piece["start"] = max(seg.get("start", 0), piece["start"])
        piece["end"] = min(seg.get("end", 0), piece["end"])
        if piece["end"] > piece["start"]:
            pieces.append(piece)
        prev_word_idx = gap_idx
    if len(pieces) < 2:
        return None
    # Verify text integrity
    combined = "".join(p.get("text", "") for p in pieces)
    if combined.replace(" ", "").replace("\u200b", "") != seg["text"].replace(" ", "").replace("\u200b", ""):
        return None
    return pieces


def _split_seg_with_words(
    seg: dict,
    seg_words: list[dict],
    max_chars: int,
    max_line_ms: int,
    pause_threshold: float,
) -> list[dict]:
    """Try a scoring-based split using word timestamps; fall back to the
    ratio/punctuation splitter on any failure. Returns >=1 segments."""
    # Hard silence split takes priority: a 0.8s+ gap inside a segment must
    # produce separate subtitles even when the pre-gap chunk is short (e.g.
    # "OK" before 2s silence). Scoring engine enforces MIN_LINE_CHARS and
    # would otherwise keep them together.
    hard = _split_at_silence(seg, seg_words, _SILENCE_SPLIT_GAP_S)
    if hard is not None and len(hard) >= 2:
        # Recursively refine each hard piece with scoring for residual overlong
        refined: list[dict] = []
        for piece in hard:
            # Piece may still be overlong; run scoring split
            sub_words = piece.get("words", [])
            char_times = _build_char_time_map(piece["text"], sub_words)
            if char_times and _check_coverage(piece["text"], char_times) >= 0.9:
                phrased = _build_phrased_units(piece["text"], char_times)
                if phrased:
                    phrased = _protect_technical_tokens(phrased)
                    if len(phrased) >= 2:
                        max_dur = max_line_ms / 1000.0
                        lines = _segment_words(phrased, max_chars=max_chars, max_dur=max_dur, pause_threshold=pause_threshold)
                        if lines and "".join(l.get("text","") for l in lines).replace(" ", "").replace("\u200b","") == piece["text"].replace(" ", "").replace("\u200b",""):
                            # verify before accepting scored lines
                            if _verify_text(lines, piece["text"]) and _verify_times(lines, piece.get("start", 0), piece.get("end", 0)):
                                refined.extend(lines)
                            else:
                                print(f"warning: hard-split piece verify failed at {piece.get('start'):.2f}s",
                                      file=sys.stderr)
                                refined.append(piece)
                            continue
            refined.append(piece)
        if not _verify_segments_integrity(refined, "hard-split"):
            print("warning: hard-split integrity failed, fallback to original segment", file=sys.stderr)
            return _fallback_split(seg, max_chars, max_line_ms)
        return refined

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
    # Verify mutation integrity — fallback on failure
    if not _verify_text(lines, seg["text"]):
        print(f"warning: split text mismatch for segment {seg.get('start'):.2f}s, fallback",
              file=sys.stderr)
        return _fallback_split(seg, max_chars, max_line_ms)
    if not _verify_times(lines, seg.get("start", 0), seg.get("end", 0)):
        print(f"warning: split time invalid for segment {seg.get('start'):.2f}s, fallback",
              file=sys.stderr)
        return _fallback_split(seg, max_chars, max_line_ms)
    return lines


def _is_ascii_word(text: str) -> bool:
    t = text.strip()
    return bool(t) and all(c.isascii() and (c.isalnum() or c in "+-#_.&/") or c == " " for c in t) and any(c.isalpha() for c in t)


import re as _re_merge

_TRAILING_ASCII_RE = _re_merge.compile(r"[A-Za-z0-9][A-Za-z0-9+\-#_.&/]*$")
_LEADING_ASCII_RE = _re_merge.compile(r"^[A-Za-z0-9][A-Za-z0-9+\-#_.&/]*")


def _trailing_ascii(text: str) -> str | None:
    m = _TRAILING_ASCII_RE.search(text.strip())
    return m.group(0) if m else None


def _leading_ascii(text: str) -> str | None:
    m = _LEADING_ASCII_RE.search(text.strip())
    return m.group(0) if m else None


def _merge_english_phrase_segments(segments: list[dict]) -> list[dict]:
    """Merge adjacent English phrase segments split across Groq windows (e.g. Rookie / Awards).

    Groq often wraps a Chinese sentence with an embedded English phrase and the
    English words land on different windows: ``国际性的这个Rookie`` + ``Awards这个比赛``.
    The suffix/prefix extraction handles hybrid segments — the whole token check
    would miss because the text contains CJK.
    """
    if len(segments) < 2:
        return segments
    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        if i + 1 < n:
            nxt = segments[i + 1]
            prev_text = seg.get("text", "").strip()
            nxt_text = nxt.get("text", "").strip()
            gap = nxt.get("start", 0) - seg.get("end", 0)
            trailing = _trailing_ascii(prev_text)
            leading = _leading_ascii(nxt_text)
            if gap <= 0.6 and trailing and leading:
                # Check if merging keeps reasonable length and phrase is continuous speech
                merged_text = prev_text + " " + nxt_text
                if len(merged_text.replace(" ", "")) <= 60:
                    merged = {
                        "start": seg["start"],
                        "end": nxt["end"],
                        "text": merged_text,
                    }
                    for k in _QUALITY_KEYS:
                        if k in seg:
                            merged[k] = seg[k]
                        elif k in nxt:
                            merged[k] = nxt[k]
                    # merge word lists if present
                    w = []
                    if seg.get("words"):
                        w.extend(seg["words"])
                    if nxt.get("words"):
                        w.extend(nxt["words"])
                    if w:
                        merged["words"] = w
                    out.append(merged)
                    i += 2
                    continue
        out.append(seg)
        i += 1
    return out


def _attach_cn_particle(segments: list[dict]) -> list[dict]:
    """Attach a lone CJK function-word fragment (``的``/``了`` etc.) that sits
    between two longer utterances back onto the previous segment. Groq can
    split ``在一线在职的`` into ``在一线在职`` + ``的`` + next sentence, leaving
    a 1-char orphan that ``_merge_fragments_groq`` does not catch because its
    neighbours are long."""
    if len(segments) < 2:
        return segments
    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        if out and i < n:
            prev = out[-1]
            cur = segments[i]
            cur_text = cur.get("text", "").strip()
            # single-char CJK function word orphan
            if len(cur_text) == 1 and cur_text in _CN_FUNCTION_WORDS:
                prev_text = prev.get("text", "").strip()
                gap = cur.get("start", 0) - prev.get("end", 0)
                if prev_text and prev_text[-1] not in _PUNCT and gap < _MERGE_GAP_S + 0.2:
                    # avoid ``的的`` stacking
                    if not (prev_text[-1] == cur_text):
                        combined = prev_text + cur_text
                        if len(combined.replace(" ", "")) <= 60:
                            out[-1] = {
                                "start": prev["start"],
                                "end": cur["end"],
                                "text": combined,
                            }
                            # carry words if present
                            w = []
                            if prev.get("words"):
                                w.extend(prev["words"])
                            if cur.get("words"):
                                w.extend(cur["words"])
                            if w:
                                out[-1]["words"] = w
                            for k in _QUALITY_KEYS:
                                if k in prev:
                                    out[-1][k] = prev[k]
                            i += 1
                            continue
        out.append(segments[i])
        i += 1
    return out


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
    reconstructed = "".join(u.get("word", u.get("text", "")) for u in units)
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


def _verify_segments_integrity(segments: list[dict], context: str) -> bool:
    """Runtime invariant: segments must be monotonic, non-overlapping, non-empty text."""
    for idx, seg in enumerate(segments):
        if seg.get("end", 0) <= seg.get("start", 0):
            print(f"warning: groq_word_adapter [{context}] segment {idx} bad time "
                  f"{seg.get('start')} -> {seg.get('end')}", file=sys.stderr)
            return False
        if not seg.get("text", "").strip():
            print(f"warning: groq_word_adapter [{context}] segment {idx} empty text",
                  file=sys.stderr)
            return False
        if idx > 0 and seg.get("start", 0) < segments[idx - 1].get("end", 0) - 0.01:
            print(f"warning: groq_word_adapter [{context}] overlap at {idx} "
                  f"{segments[idx-1].get('end')} -> {seg.get('start')}", file=sys.stderr)
            return False
    return True


def _should_force_split(seg: dict, seg_words: list[dict]) -> bool:
    """Normal segment that internally spans a large silence gap should still be split."""
    if len(seg_words) < 2:
        return False
    # Only care if gap is large enough to be a paragraph pause
    return _has_large_pause(seg_words, _SILENCE_SPLIT_GAP_S)


def refine_groq_segments(
    segments: list[dict],
    top_words: list[dict],
    max_chars: int = 15,
    max_line_ms: int = 3000,
    pause_threshold: float = 0.3,
    full_segment: bool = False,
) -> list[dict]:
    """Refine Groq segments.

    - Abnormal (overlong/overduration) segments are always re-segmented
      using the scoring engine (or the fallback splitter).
    - Normal segments keep their original boundary, but when the window is
      bloated by silence they are shrunk on both edges to the speech span.
    - Normal segments that internally contain a large word gap (>0.8s) are
      force-split via the scoring engine — these are the 19 silence-spanning
      cases (2-16s gaps) that previously became double-length subtitles.
    - When ``full_segment`` is True every segment WITH usable word timestamps
      is passed through the scoring engine (not just abnormal ones).
    - Finally, runs of adjacent tiny crumbs are merged back together and
      overlaps are repaired.
    """
    # Pre-merge English phrase split across Groq windows (e.g. Rookie / Awards)
    # before per-segment abnormal handling, so the phrase is kept together and
    # the combined window is split with proper Chinese breaks.
    # Only when we have word timestamps can we safely re-split the merged
    # window with the scoring engine; for the no-word SRT fallback keep
    # original boundaries to avoid creating an overlong merged line.
    if len(segments) > 1 and top_words:
        pre = _merge_english_phrase_segments(segments)
        if len(pre) != len(segments):
            segments = pre
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
            elif _should_force_split(seg, seg_words):
                # Large internal silence → must split even though char/dur are normal
                out.extend(_inherit_quality(seg, _split_seg_with_words(
                    seg, seg_words, max_chars, max_line_ms, pause_threshold)))
            else:
                shrunk_seg = _shrink_silent_edges(seg, seg_words)
                # Preserve word timestamps for final post-shrink/deoverlap
                if seg_words:
                    shrunk_seg["words"] = list(seg_words)
                out.append(shrunk_seg)
            continue
        pieces = _split_seg_with_words(seg, seg_words, max_chars, max_line_ms, pause_threshold)
        # Verify split integrity before extending; fallback to original on failure
        if not _verify_segments_integrity(pieces, f"split-seg-{seg_idx}"):
            print(f"warning: split verification failed for seg {seg_idx}, keeping original",
                  file=sys.stderr)
            pieces = [seg]
            if seg_words:
                pieces[0] = dict(pieces[0])
                pieces[0]["words"] = list(seg_words)
        # If split returned original segment (no words), attach words for final shrink
        if len(pieces) == 1 and not pieces[0].get("words"):
            pieces[0]["words"] = list(seg_words)
            pieces[0] = _shrink_silent_edges(pieces[0], seg_words)
        out.extend(_inherit_quality(seg, pieces))
    # Mutation exit 1: after merge fragments
    _merge_before = len(out)
    out = _merge_fragments_groq(out)
    # Only merge English phrases when we have word timestamps to re-split
    # otherwise a no-word SRT merge (e.g. 10+21=31 chars) becomes super-long with no fallback
    if top_words:
        out = _merge_english_phrase_segments(out)
    out = _attach_cn_particle(out)
    # Re-split any still-abnormal segments created by merging (e.g. Rookie + Awards -> 31ch)
    # Allow a small tolerance (+3 chars) for phrase integrity (Rookie Awards = 18)
    # to avoid re-splitting a phrase-preserving segment and breaking the word.
    resplit: list[dict] = []
    for seg in out:
        if _is_abnormal(seg, max_chars + 3, max_line_ms):
            ws = seg.get("words", [])
            if ws:
                pieces = _split_seg_with_words(seg, ws, max_chars, max_line_ms, pause_threshold)
                if pieces and len(pieces) > 1:
                    resplit.extend(_inherit_quality(seg, pieces))
                else:
                    fb = _fallback_split(seg, max_chars, max_line_ms)
                    resplit.extend(_inherit_quality(seg, fb if fb else [seg]))
            else:
                resplit.append(seg)
        else:
            resplit.append(seg)
    out = resplit
    if not _verify_segments_integrity(out, "merge-fragments"):
        print("warning: merge-fragments integrity check failed", file=sys.stderr)
    # Mutation exit 2: after absorb + deoverlap
    out = _absorb_isolated_crumbs(out)
    out = _deoverlap(out)
    if not _verify_segments_integrity(out, "deoverlap-1"):
        print("warning: deoverlap-1 integrity check failed", file=sys.stderr)
    # Final edge shrink after merging/absorbing (merged windows can become bloated)
    # Re-assign words for the merged segments is lossy, so only shrink when we have words
    shrunk = []
    for seg in out:
        ws = seg.get("words", [])
        if ws:
            shrunk.append(_shrink_silent_edges(seg, ws))
        else:
            shrunk.append(seg)
    # De-overlap again after shrink — final mutation exit
    final = _deoverlap(shrunk)
    if not _verify_segments_integrity(final, "final"):
        print("warning: final deoverlap integrity check failed", file=sys.stderr)
    return final
