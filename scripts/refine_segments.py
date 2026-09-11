#!/usr/bin/env python3
"""
Refine SRT segments using word-level timestamps for scoring-driven segmentation.

Scoring engine replaces the old 6-level cascading semantic split + character-ratio
time allocation. Each gap between adjacent words is scored based on:
  - Punctuation at gap boundary
  - Silence/pause duration
  - Line length/time driver
  - Bad break penalties (dangling conjunctions, orphaned prepositions)
  - Fragmentation penalty

Pipeline:
  1. Clean empty / zero-duration / duplicate segments from upstream ASR
  2. Score-based segmentation using word timestamps
  3. Output segments with accurate per-word timing

Usage:
    python3 scripts/refine_segments.py <input.srt> [output.srt]
"""

import argparse
import re
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────

STRONG_PUNCT = frozenset("。！？.?!…")
WEAK_PUNCT = frozenset("，、；：,:;")

# Words that should NOT appear at the start of a subtitle line
# (prepositions/subordinating conjunctions that make viewers feel
#  the line starts mid-sentence)
BAD_LINE_START = frozenset({
    "在", "对", "给", "为", "把", "被", "从", "向",
    "和", "与", "跟", "同", "及",
    "关于", "对于", "根据", "经过", "通过", "除了",
    "因为", "但是", "所以", "不过", "然而", "而且", "并且",
    "如果", "虽然", "尽管", "由于", "为了", "除非",
    "当", "随着", "作为",
    "那", "那么",
    "for", "to", "with", "about", "because", "but", "so",
    "and", "then", "however", "although",
})

# Words that should NOT appear at the end of a subtitle line
# (dangling conjunctions)
BAD_LINE_END = frozenset({
    "因为", "但是", "所以", "然后", "不过",
    "然而", "而且", "并且",
    "如果", "虽然", "尽管", "由于",
    "because", "but", "so", "and", "then", "however",
})

# Words that are GOOD to have at line start
# (topic markers / teaching discourse markers)
GOOD_LINE_START = frozenset({
    "首先", "其次", "然后", "接着",
    "另外", "还有", "此外", "同样",
    "比如说", "举个例子", "说白了",
    "也就是说", "所以说",
    "我们来", "我们再来", "来看一下",
    "到时候", "有时候", "接下来",
})

# Words that mark the start of a new semantic clause. Manual refinement
# (人工精校) frequently breaks lines right BEFORE these words, e.g.
# "因为大厂的 HR" | "优先是在暑期的实习生里面…" or "你应该成功能拿到" |
# "你特别喜欢的…". They behave opposite to BAD_LINE_START: once the line
# is long enough, a break here reads naturally.
SEMANTIC_START = frozenset({
    "你", "我", "我们", "你们", "它", "他们", "她们",
    "这个", "那个", "这种", "那种", "这样", "那样",
    "因为", "所以", "但是", "不过", "那", "那么", "然后",
    "其实", "最终", "最后", "首先", "就是", "可能", "应该",
    "如果说", "那我们", "所以说",
})

_DEFAULT_MAX_CHARS = 15
_DEFAULT_MAX_LINE_MS = 3000
_DEFAULT_MIN_LINE_CHARS = 8
_PREFERRED_MAX_CHARS = 15  # scoring preference, not a hard limit (manual ~12 chars)
_SEMANTIC_MIN_CHARS = 12   # line length before a SEMANTIC_START break is rewarded

# Left-side characters that make a following SEMANTIC_START word an
# attributive marker rather than a new clause. "…的[这个]" / "…着[我]"
# are determiner/prepositional structures (游戏美术的这个经验), NOT clause
# boundaries; the manual gold keeps them on one line.
_SEMANTIC_START_BLOCKED_PREFIX = frozenset("的得地着了过")


# ── Helpers ──────────────────────────────────────────────────────────

def _chars(words: list[dict]) -> int:
    """Count visual characters, excluding spaces and zero-width spaces."""
    return sum(
        len(w.get("word", "").replace(" ", "").replace("\u200b", ""))
        for w in words
    )


def _to_segment(words: list[dict]) -> dict:
    """Build a segment dict from a slice of word timestamps."""
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": "".join(w.get("word", "") for w in words).strip(),
        "words": words,
    }


def _detect_language(words: list[dict]) -> str:
    """Quick language detection from words. Returns 'zh' or 'en'."""
    for w in words:
        for ch in w.get("word", ""):
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                return "zh"
    return "en"


def _clean_words(words: list[dict]) -> list[dict]:
    """Remove malformed word entries (empty text, missing/zero timestamps)."""
    clean = []
    for w in words:
        text = w.get("word", "").strip()
        start = w.get("start")
        end = w.get("end")
        if not text or start is None or end is None:
            continue
        if end <= start:
            continue
        clean.append(w)
    return clean


# ── Scoring ─────────────────────────────────────────────────────────

def _score_gap(
    left: dict, right: dict,
    line_chars: int, line_dur: float,
    max_chars: int, max_dur: float,
    pause_threshold: float,
) -> float:
    """Score a gap between two words as a candidate split point.
    Higher score = better place to split.
    """
    score = 0.0
    left_word = left.get("word", "").strip()
    right_word = right.get("word", "").strip().lower()

    # 1. Punctuation bonus
    if left_word and left_word[-1] in STRONG_PUNCT:
        score += 5.0
    elif left_word and left_word[-1] in WEAK_PUNCT:
        score += 3.0

    # 2. Silence/pause bonus
    gap = right.get("start", 0) - left.get("end", 0)
    if gap >= pause_threshold:
        score += 4.0
    elif gap >= pause_threshold * 0.5:
        score += 1.0

    # 3. Length driver — line getting long, encourage a break.
    #    A soft preferred-length signal below the hard max: once a line
    #    passes ~15 chars, nudge toward a break (manual subtitles run ~12).
    if line_dur > max_dur or line_chars > max_chars:
        score += 2.0
    elif line_chars >= _PREFERRED_MAX_CHARS:
        score += 1.0

    # 4. Right-side word at line start.
    #    SEMANTIC_START words behave opposite to BAD_LINE_START: when the
    #    line is long enough, breaking right before them reads naturally
    #    (人工精校 breaks before 因为/那/你/这种 etc.). On short lines the
    #    break is still discouraged to avoid staccato fragments.
    left_tail = left_word[-1:] if left_word else ""
    if (right_word in SEMANTIC_START
            and left_tail not in _SEMANTIC_START_BLOCKED_PREFIX):
        if line_chars >= _SEMANTIC_MIN_CHARS:
            score += 3.0
        else:
            score -= 2.0
    elif right_word in BAD_LINE_START:
        score -= 4.0
    #    Right-side word is good at line start → bonus
    elif right_word in GOOD_LINE_START:
        score += 1.0

    # 5. Left-side word is bad at line end → penalty
    if left_word.lower() in BAD_LINE_END:
        score -= 4.0

    # 6. Fragmentation penalty — line too short without strong signal
    if line_chars < _DEFAULT_MIN_LINE_CHARS and score < 3:
        score -= 2.0

    return score


# ── Segmentation engine ─────────────────────────────────────────────

def _segment_words(
    words: list[dict],
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_dur: float = _DEFAULT_MAX_LINE_MS / 1000.0,
    pause_threshold: float = 0.3,
) -> list[dict]:
    """
    Score-driven subtitle segmentation with natural break detection.

    Two-phase approach:
      1. Pre-compute 'natural break' positions — gaps with strong
         punctuation or significant pause. These are preferred split points.
      2. Walk through words; split at the best available point when
         constraints are exceeded OR when a natural break provides a
         well-sized line.
    """
    if not words:
        return []

    n = len(words)

    # Phase 1: pre-compute natural break positions.
    #   strong_breaks — punctuation or pause: fire as soon as the line is
    #                   substantial (>= _DEFAULT_MIN_LINE_CHARS).
    #   semantic_breaks — before a new clause starter (因为/那/你/这种…):
    #                   fire only once the line is long enough
    #                   (>= _SEMANTIC_MIN_CHARS), matching 人工精校 which
    #                   breaks before these words on fairly long lines.
    strong_breaks: set[int] = set()
    semantic_breaks: set[int] = set()
    for i in range(1, n):
        gap = words[i]["start"] - words[i - 1]["end"]
        left_char = words[i - 1]["word"].strip()[-1:] if words[i - 1]["word"].strip() else ""
        right_word = words[i]["word"].strip().lower()
        if (left_char and left_char[0] in STRONG_PUNCT) or gap >= pause_threshold:
            strong_breaks.add(i)
        elif (right_word in SEMANTIC_START
                and left_char not in _SEMANTIC_START_BLOCKED_PREFIX):
            semantic_breaks.add(i)

    lines: list[dict] = []
    start = 0

    while start < n:
        best_score = -999.0
        best_end = start + 1
        pending_break = None      # earliest strong break; wait for line to grow
        pending_semantic = None   # earliest semantic break; needs longer line

        for end in range(start + 1, n + 1):
            chunk = words[start:end]
            chunk_chars = _chars(chunk)
            chunk_dur = chunk[-1]["end"] - chunk[0]["start"]

            # Record first strong break; semantic breaks update to the most
            # recent one so a too-early semantic point (line still short)
            # can be superseded by a later, now-long-enough candidate.
            if end in strong_breaks and pending_break is None:
                pending_break = end
            if end in semantic_breaks:
                pending_semantic = end

            # Semantic break fires once the line is long enough
            if pending_semantic is not None:
                pre_chars = _chars(words[start:pending_semantic])
                pre_dur = words[pending_semantic - 1]["end"] - words[start]["start"]
                if (_SEMANTIC_MIN_CHARS <= pre_chars <= max_chars
                        and pre_dur <= max_dur):
                    best_end = pending_semantic
                    break

            # Strong break fires once the line is substantial
            if pending_break is not None:
                pre_chars = _chars(words[start:pending_break])
                pre_dur = words[pending_break - 1]["end"] - words[start]["start"]
                if (pre_chars >= _DEFAULT_MIN_LINE_CHARS
                        and pre_chars <= max_chars and pre_dur <= max_dur):
                    best_end = pending_break
                    break

            # Still within limits — keep accumulating
            if chunk_chars <= max_chars and chunk_dur <= max_dur:
                best_end = end
                continue

            # Overflow: evaluate all possible cuts
            for cut in range(start + 1, end):
                pre = words[start:cut]
                pre_chars = _chars(pre)
                if pre_chars < 1:
                    continue
                s = _score_gap(
                    words[cut - 1], words[cut],
                    pre_chars,
                    pre[-1]["end"] - pre[0]["start"],
                    max_chars, max_dur, pause_threshold,
                )
                if s >= best_score:
                    best_score = s
                    best_end = cut

            # If overflow has a scored candidate, use it
            if best_score <= -999.0:
                if pending_semantic is not None:
                    best_end = pending_semantic
                elif pending_break is not None:
                    best_end = pending_break
            break

        # Safety: always advance at least one word
        if best_end <= start:
            best_end = start + 1

        lines.append(_to_segment(words[start:best_end]))
        start = best_end

    return lines


# ── ASR cleanup ──────────────────────────────────────────────────────

def _clean_segments(segments: list[dict]) -> list[dict]:
    """Filter out ASR noise: empty text, zero duration, exact duplicates."""
    seen: set[tuple[str, float]] = set()
    clean: list[dict] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        if seg.get("end", 0) <= seg.get("start", 0):
            continue
        key = (text, round(seg["start"], 3))
        if key in seen:
            continue
        seen.add(key)
        clean.append(seg)
    return clean


# ── Fallback (for CLI / segments without word timestamps) ───────────

def _find_split(text: str, char_pos: int, target_chars: int,
                 max_chars: int) -> int:
    """Find a good split position near char_pos + target_chars."""
    end = min(char_pos + target_chars, len(text))

    # 1. Try punctuation (strong boundary)
    for c in range(end, max(char_pos, end - 6), -1):
        if char_pos < c < len(text) and text[c] in "，、。？！；：":
            return c + 1

    # 2. Try space backward (English word boundary) — skip spaces inside an
    # English phrase (Rookie Awards) so the phrase stays together.
    for c in range(end, max(char_pos, end - 15), -1):
        if c > char_pos and text[c - 1] == " ":
            # Don't split a space that is sandwiched by English letters
            if c - 2 >= 0 and c < len(text) and _is_eng(text[c - 2]) and _is_eng(text[c]):
                continue
            return c

    # 3. Try space forward — same phrase protection
    for c in range(end, min(len(text), end + 10)):
        if text[c] == " ":
            if c - 1 >= 0 and c + 1 < len(text) and _is_eng(text[c - 1]) and _is_eng(text[c + 1]):
                continue
            return c + 1

    # 4. Protect English words — don't split mid-word; also don't split
    # inside an English phrase (Rookie Awards) — treat the whole phrase as one unit.
    if end > char_pos and end < len(text) and _is_eng(text[end - 1]) and _is_eng(text[end]):
        for c in range(end, min(len(text), end + 10)):
            if not _is_eng(text[c]):
                # Skip a space that is sandwiched by English letters (phrase-internal)
                if text[c] == " " and c + 1 < len(text) and _is_eng(text[c + 1]) and c - 1 >= 0 and _is_eng(text[c - 1]):
                    continue
                return c
    # Also handle case where end lands exactly on the phrase-internal space
    if end > char_pos and end < len(text) and text[end] == " " and end - 1 >= 0 and end + 1 < len(text) and _is_eng(text[end - 1]) and _is_eng(text[end + 1]):
        # end is the space between Rookie and Awards — jump over the whole phrase
        for c in range(end + 1, min(len(text), end + 16)):
            if not _is_eng(text[c]):
                if text[c] == " " and c + 1 < len(text) and _is_eng(text[c + 1]) and _is_eng(text[c - 1]):
                    continue
                return c

    # 4b. Protect Chinese words — don't split inside a jieba word (e.g. 比赛)
    if end > char_pos and end < len(text) and _is_cn_char(text[end - 1]) and _is_cn_char(text[end]):
        try:
            import jieba
            toks = list(jieba.tokenize(text))
            for word, s, e in toks:
                if s < end < e:
                    return e
        except Exception:
            pass

    # 5. Bad-line-start check: if candidate starts with BAD_LINE_START, push forward
    candidate = text[char_pos:end].strip()
    for w in BAD_LINE_START:
        if candidate.startswith(w) and (len(w) == len(candidate) or
                                        not _is_eng(candidate[len(w)])):
            for c in range(end, min(end + 6, len(text))):
                if text[c] in "，、。？！；：":
                    return c + 1
            break

    return end


def _fallback_split(
    seg: dict,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_line_ms: int = _DEFAULT_MAX_LINE_MS,
) -> list[dict]:
    """Punctuation + English word boundary + bad-line-start aware split
    for segments without word timestamps."""
    text = seg.get("text", "").strip()
    if not text:
        return []

    chars = len(text.replace(" ", ""))
    duration = seg["end"] - seg["start"]
    max_dur = max_line_ms / 1000.0

    if chars <= max_chars and duration <= max_dur:
        return [seg]

    n_pieces = max(
        (chars + max_chars - 1) // max_chars,
        int(duration / max_dur) + 1,
        1,
    )
    result = []
    char_pos = 0
    start_time = seg["start"]
    target_chars = chars // n_pieces

    for i in range(n_pieces):
        if i == n_pieces - 1:
            piece_text = text[char_pos:].strip()
        else:
            end_char = _find_split(text, char_pos, target_chars, max_chars)
            piece_text = text[char_pos:end_char].strip()
            char_pos = end_char

        if piece_text:
            ratio = len(piece_text.replace(" ", "")) / max(chars, 1)
            end_time = start_time + duration * ratio
            result.append({
                "start": start_time, "end": end_time,
                "text": piece_text, "words": [],
            })
            start_time = end_time

    return result


def _is_eng(ch: str) -> bool:
    return 'a' <= ch <= 'z' or 'A' <= ch <= 'Z'


def _is_cn_char(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff'


_MERGE_HARD_LIMIT = 60   # merged length cap; word repair wins over max_chars
_FRAGMENT_MAX_CHARS = 3  # remnant like "er" from "offer" is at most 3 letters
_FRAGMENT_MAX_DUR_S = 0.5  # remnants are very short audio fragments
_MERGE_GAP_S = 0.3       # near-continuous speech: gap below this = same word

# Single-char function words that attach naturally to a verb/noun ending.
# Treating them as word-tail remnants avoids false jieba rejections for
# real cases like "比较艰难" + "的" → "比较艰难的".
_CN_FUNCTION_WORDS = frozenset("的得地了着过吧吗呢啊呀哦")


def _is_eng_fragment(seg: dict) -> bool:
    """True if the segment is a short pure-ASCII English remnant
    (e.g. "er" split off from "offer")."""
    text = seg.get("text", "").strip()
    if not text or len(text) > _FRAGMENT_MAX_CHARS:
        return False
    if not all(_is_eng(c) for c in text):
        return False
    dur = seg.get("end", 0) - seg.get("start", 0)
    return dur < _FRAGMENT_MAX_DUR_S


def _is_cn_fragment(seg: dict) -> bool:
    """True if the segment is a short pure-Chinese remnant
    (e.g. "集" split off from "作品集", or "西" from "东西").
    Duration gate is relaxed to 0.8s for Chinese (3 chars at natural speed
    can be ~0.6s, e.g. "一块的" 0.56s); English remnants stay at 0.5s."""
    text = seg.get("text", "").strip()
    if not text or len(text) > _FRAGMENT_MAX_CHARS:
        return False
    if not all(_is_cn_char(c) for c in text):
        return False
    dur = seg.get("end", 0) - seg.get("start", 0)
    return dur < 0.8


def _cn_fragment_fits(prev_text: str, frag_text: str) -> bool:
    """Check whether appending a Chinese remnant to the previous segment's
    tail forms a natural word/attachment. Uses jieba when available;
    without jieba, falls back to accepting the merge."""
    if not prev_text:
        return False
    if not _is_cn_char(prev_text[-1]):
        return False
    # Function-word remnant (的/了/吧…) — attaches to a verb/noun ending.
    # Reject stacking on an existing 的 to avoid "的的".
    if len(frag_text) == 1 and frag_text in _CN_FUNCTION_WORDS:
        return prev_text[-1] != frag_text
    try:
        import jieba
    except ImportError:
        return True
    probe = prev_text[-3:] + frag_text
    # 2-3 char CJK tail like “块的” after “加在一” — probe “加在一块的”
    # ends with the fragment and is longer; that's a natural continuation
    # even when jieba splits it into multiple tokens (e.g. “一块”+“的”).
    if len(frag_text) > 1 and probe.endswith(frag_text) and len(probe) > len(frag_text):
        # limit to when the suffix boundary is inside a single word scope
        # (last 3 chars + frag overlapping) to avoid over-eager merges
        if len(frag_text) <= 3:
            return True
    tokens = jieba.lcut(probe)
    last = tokens[-1] if tokens else ""
    return last.endswith(frag_text) and len(last) > len(frag_text)


def _gap_s(prev: dict, seg: dict) -> float:
    return seg.get("start", 0) - prev.get("end", 0)


def _merge_fragments(segments: list[dict], max_chars: int) -> list[dict]:
    """Merge adjacent segments where an English word is broken across them.

    Two patterns:
      1. Broken word: previous segment ends with a letter and the current
         segment starts with a letter, with near-continuous speech between
         them. Word integrity wins over max_chars (capped by _MERGE_HARD_LIMIT).
      2. English remnant (e.g. "er" from "offer"): a very short ASCII-letter
         segment that can't attach to the previous text gets attached to the
         next segment when that segment starts with a letter.
    """
    if not segments:
        return []
    out: list[dict] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        if not out:
            out.append(seg)
            i += 1
            continue
        prev = out[-1]
        prev_text = prev.get("text", "").strip()
        curr_text = seg.get("text", "").strip()

        # Pattern 1: broken English word across the segment boundary
        if (prev_text and curr_text
                and _is_eng(prev_text[-1]) and _is_eng(curr_text[0])
                and _gap_s(prev, seg) < _MERGE_GAP_S):
            # Two separate English words (e.g. Rookie / Awards) vs a single
            # broken word (e.g. posit / ion). Use a space when both sides look
            # like complete words (>3 chars or capitalised new word).
            need_space = False
            if curr_text and curr_text[0].isupper() and prev_text[-1].islower():
                need_space = True
            elif len(prev_text) > 3 and len(curr_text) > 3:
                # both substantial -> likely two words (Rookie Awards)
                need_space = True
            combined = (prev_text + " " + curr_text) if need_space else (prev_text + curr_text)
            if len(combined.replace(" ", "")) <= _MERGE_HARD_LIMIT:
                out[-1] = {
                    "start": prev["start"],
                    "end": seg["end"],
                    "text": combined,
                }
                i += 1
                continue

        # Pattern 2: English remnant attaches forward to the next segment
        if (_is_eng_fragment(seg) and i + 1 < n
                and _gap_s(seg, segments[i + 1]) < _MERGE_GAP_S):
            nxt = segments[i + 1]
            nxt_text = nxt.get("text", "").strip()
            if nxt_text and _is_eng(nxt_text[0]):
                combined = curr_text + nxt_text
                if len(combined.replace(" ", "")) <= _MERGE_HARD_LIMIT:
                    out.append({
                        "start": seg["start"],
                        "end": nxt["end"],
                        "text": combined,
                    })
                    i += 2
                    continue

        # Pattern 3: Chinese remnant attaches backward to the previous
        # segment ("作品"+"集" → "作品集", "东"+"西" → "东西")
        if (_is_cn_fragment(seg)
                and _gap_s(prev, seg) < _MERGE_GAP_S
                and _cn_fragment_fits(prev_text, curr_text)):
            combined = prev_text + curr_text
            if len(combined.replace(" ", "")) <= _MERGE_HARD_LIMIT:
                out[-1] = {
                    "start": prev["start"],
                    "end": seg["end"],
                    "text": combined,
                }
                i += 1
                continue

        out.append(seg)
        i += 1
    return out


# ── Main refine pipeline ────────────────────────────────────────────

def refine(
    segments: list[dict],
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_line_ms: int = _DEFAULT_MAX_LINE_MS,
    pause_threshold: float | None = None,
) -> list[dict]:
    """
    Refine ASR segments into well-timed subtitles using scoring-driven
    segmentation with word-level timestamps.

    Args:
        segments: List of segment dicts with 'start', 'end', 'text', 'words'
        max_chars: Max characters per subtitle line
        max_line_ms: Max duration per subtitle line in milliseconds
        pause_threshold: Pause threshold in seconds for split detection.
                         None = auto-detect from language (0.3s zh / 0.5s en).

    Returns:
        Refined segment list with accurate per-line timing
    """
    if not segments:
        return []

    segments = _clean_segments(segments)
    max_dur = max_line_ms / 1000.0

    # Pre-merge: only for segments without word timestamps (Groq path)
    # to fix English word fragments broken across segments (e.g. "posit"+"ion")
    has_word_timestamps = any(seg.get("words") for seg in segments)
    if not has_word_timestamps and len(segments) > 1:
        segments = _merge_fragments(segments, max_chars * 2)

    out: list[dict] = []
    for seg in segments:
        words = seg.get("words")
        if not words:
            # No word timestamps — keep raw segment boundaries (e.g. Groq),
            # only merge English word fragments broken across segments
            out.append(seg)
        else:
            words = _clean_words(words)
            if len(words) < 2:
                out.append(_to_segment(words) if words else seg)
                continue
            lang = _detect_language(words)
            if pause_threshold is not None:
                pause_th = pause_threshold
            else:
                pause_th = 0.3 if lang == "zh" else 0.5
            out.extend(_segment_words(words, max_chars, max_dur, pause_th))

    out = _merge_fragments(out, max_chars)
    return out


# ── SRT I/O (CLI only) ──────────────────────────────────────────────

def parse_srt(path: str) -> list[dict]:
    segments: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            ts = lines[i].strip()
            i += 1
            text_lines: list[str] = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            text = " ".join(text_lines)
            m = re.match(
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
                ts,
            )
            if m:
                start = (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                         + int(m[4]) / 1000)
                end = (int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7])
                       + int(m[8]) / 1000)
                segments.append({
                    "start": start, "end": end, "text": text, "words": [],
                })
            i += 1
        else:
            i += 1
    return segments


def write_srt(segments: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = seg["start"]
            end = seg["end"]
            sh = int(start // 3600)
            sm = int((start % 3600) // 60)
            ss = int(start % 60)
            sms = int((start - int(start)) * 1000)
            eh = int(end // 3600)
            em = int((end % 3600) // 60)
            es = int(end % 60)
            ems = int((end - int(end)) * 1000)
            f.write(f"{i}\n")
            f.write(f"{sh:02d}:{sm:02d}:{ss:02d},{sms:03d} --> "
                    f"{eh:02d}:{em:02d}:{es:02d},{ems:03d}\n")
            f.write(f"{seg['text']}\n\n")


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refine SRT segments using word-level timestamps"
    )
    parser.add_argument("input", help="input SRT file path")
    parser.add_argument("output", nargs="?",
                        help="output SRT file path (default: overwrite input)")
    parser.add_argument("--max-chars", type=int, default=_DEFAULT_MAX_CHARS,
                        help=f"max characters per line (default: {_DEFAULT_MAX_CHARS})")
    parser.add_argument("--max-line-ms", type=int, default=_DEFAULT_MAX_LINE_MS,
                        help=f"max duration per line in ms (default: {_DEFAULT_MAX_LINE_MS})")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    segs = parse_srt(str(input_path))
    if not segs:
        print(f"error: no valid segments in {input_path}", file=sys.stderr)
        sys.exit(1)

    original_count = len(segs)
    out = refine(segs, max_chars=args.max_chars, max_line_ms=args.max_line_ms)
    output_path = args.output or str(input_path)
    write_srt(out, output_path)
    print(
        f"refined {original_count} → {len(out)} segments "
        f"(max_chars={args.max_chars}) → {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
