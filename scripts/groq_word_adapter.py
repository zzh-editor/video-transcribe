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

from refine_segments import _segment_words, _fallback_split

_PUNCT = frozenset("，、。？！；：,.;:!?…\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f"
                   "\u300a\u300b\u3008\u3009\uff08\uff09\uff3b\uff3d"
                   "\u3010\u3011\u300c\u300d" + "\u201c\u201d''\u300e\u300f()[]\u300a\u300b")


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
) -> list[dict]:
    assigned = _assign_words_to_segments(top_words, segments)
    out = []
    for seg_idx, seg in enumerate(segments):
        seg_words = assigned[seg_idx]
        has_content_words = bool(seg_words)
        if not _is_abnormal(seg, max_chars, max_line_ms):
            entry = dict(seg)
            out.append(entry)
            continue
        if not has_content_words:
            out.append(seg)
            continue
        char_times = _build_char_time_map(seg["text"], seg_words)
        if not char_times:
            fallback = _fallback_split(seg, max_chars, max_line_ms)
            out.extend(fallback)
            continue
        coverage = _check_coverage(seg["text"], char_times)
        if coverage < 0.9:
            fallback = _fallback_split(seg, max_chars, max_line_ms)
            out.extend(fallback)
            continue
        phrased = _build_phrased_units(seg["text"], char_times)
        if not phrased:
            fallback = _fallback_split(seg, max_chars, max_line_ms)
            out.extend(fallback)
            continue
        phrased = _protect_technical_tokens(phrased)
        if len(phrased) < 2:
            entry = dict(seg)
            out.append(entry)
            continue
        max_dur = max_line_ms / 1000.0
        lines = _segment_words(
            phrased,
            max_chars=max_chars,
            max_dur=max_dur,
            pause_threshold=pause_threshold,
        )
        if not lines:
            out.append(seg)
            continue
        combined_text = "".join(l.get("text", "") for l in lines)
        if combined_text.replace(" ", "").replace("\u200b", "") != seg["text"].replace(" ", "").replace("\u200b", ""):
            out.append(seg)
            continue
        out.extend(lines)
    return out
