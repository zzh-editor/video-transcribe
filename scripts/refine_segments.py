#!/usr/bin/env python3
"""
Refine SRT segments using multi-level cascading semantic split rules.

Pipeline:
  1. Clean empty / zero-duration / duplicate segments from upstream ASR
  2. For each segment, find candidate split positions across 6 confidence levels
  3. Recursively split at balanced positions, proportionally allocate time

No punctuation dependency — split purely on semantic/contextual cues.

Usage:
    python3 scripts/refine_segments.py <input.srt> [output.srt]
"""

import argparse
import re
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────

STRONG_CONJUNCTIONS = [
    "但是", "但", "所以", "不过", "然而", "可是", "因此", "因而",
    "而且", "并且", "那如果", "那这样", "那就",
    # 新增：然后 是教学口语最高频话题转接词
    "然后",
]

DISCOURSE_MARKERS = [
    "首先", "其次", "然后", "接着",
    "另外", "还有", "此外", "同样",
    "比如说", "举个例子", "比方说",
    "说白了", "也就是说", "所以说", "就是说",
    "那首先",
    "可以这么说", "换句话说", "反过来说",
    "总的来说", "具体来说", "严格来说",
    "简单来说", "一般来讲", "换句话说",
    # 新增：教学演示引入语
    "我们来", "我们再来", "来看一下",
]

TEMP_MARKERS = [
    "到时候", "有时候", "接下来",
    "那接下来", "那现在", "现在我们来",
    "我们一起来", "我们现在",
    # 新增：时序完成后引入新动作
    "之后", "完成之后", "做好之后",
]

TOPIC_SHIFT_FOLLOW = frozenset(
    "我们大家你们他们这个这些现在今天首先"
    "第二第三接下来到时候如果"
)

PROTECTED_WORDS = frozenset({
    "好", "对", "嗯", "是", "不", "行", "哦", "啊", "呀", "喏", "嗯哼",
    "好的", "对的", "明白", "知道", "可以", "没错", "是的", "不行",
    "好吧", "对了", "对哦", "对啊", "嗯嗯", "好啦", "行了", "可以啊",
    "没问题", "没事", "知道了", "明白了", "没关系",
    "ok", "okay", "okay", "yes", "no", "right", "sure", "yeah", "yep",
    "nope", "nah", "alright", "indeed",
})

SPLIT_BEFORE_TAGS = [
    "是吧", "对吧", "好吧", "没问题", "没错",
    "是的", "对啊", "对哦", "好啦", "行了",
    "可以啊", "就这样", "就是这样",
    "它是", "这是一个",
]

RESPONSE_TOPIC_PATTERNS = [
    (re.compile(r"好那"), 2),
    (re.compile(r"好现在"), 1),
    (re.compile(r"好那我们"), 2),
    (re.compile(r"好我们"), 1),
    (re.compile(r"好接下来"), 1),
    (re.compile(r"对那"), 2),
    (re.compile(r"行那"), 2),
]

# 对这些词触发的切点，要求左右各至少这么多字才切
# 防止 "然后" 把极短片段继续碎切
_MIN_LEN_BY_TRIGGER: dict[str, int] = {
    "然后": 6,
    "之后": 5,
    "完成之后": 5,
    "做好之后": 5,
    "我们来": 5,
    "我们再来": 5,
    "来看一下": 5,
}
_DEFAULT_MIN_LEN = 3


# ── SRT I/O ─────────────────────────────────────────────────────────

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
            idx = int(line)
            i += 1
            if i >= len(lines):
                break
            ts = lines[i].strip()
            i += 1
            text_lines = []
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
                    "idx": idx,
                    "start": start,
                    "end": end,
                    "text": text,
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


# ── Helpers ─────────────────────────────────────────────────────────

def _strip_punct(text: str) -> str:
    return text.strip().lower().rstrip(",.?!。？！，、；:\"'").strip()


def _is_protected(text: str) -> bool:
    return _strip_punct(text) in PROTECTED_WORDS


def _min_len_for_pos(text: str, pos: int) -> int:
    """返回切点 pos 对应的最小左右长度要求。"""
    for trigger, min_len in _MIN_LEN_BY_TRIGGER.items():
        tlen = len(trigger)
        # 触发词出现在切点左侧紧邻（STRONG/TEMP 模式：切在词首）
        if text[max(0, pos - tlen):pos] == trigger:
            return min_len
        # 或切点右侧紧邻（DISCOURSE 模式：切在词尾之后）
        if text[pos:pos + tlen] == trigger:
            return min_len
    return _DEFAULT_MIN_LEN


# ── Split point detection ───────────────────────────────────────────

def _is_split_viable(text: str, pos: int) -> bool:
    if pos < 1 or pos >= len(text) - 1:
        return False
    right_start = text[pos]
    if right_start.isascii() and (right_start.isalpha() or right_start.isdigit()):
        return False
    return True


def _is_topic_shift_na(text: str, pos: int) -> bool:
    if pos + 1 >= len(text):
        return False
    if pos > 0 and text[pos - 1] == "好":
        return True
    nxt = text[pos + 1]
    if nxt in TOPIC_SHIFT_FOLLOW:
        return True
    nxt2 = text[pos + 1:pos + 3]
    if nxt2 in ("就是", "还是", "也是", "算是", "的话", "这么", "那么"):
        return False
    return nxt in "，"


def _find_response_topic_splits(text: str) -> list[int]:
    splits = []
    for pattern, split_offset in RESPONSE_TOPIC_PATTERNS:
        m = pattern.search(text)
        while m:
            p = m.start() + split_offset
            if _is_split_viable(text, p):
                splits.append(p)
            m = pattern.search(text, m.start() + 1)
    return splits


def _find_split_points(text: str) -> tuple[list[int], list[int]]:
    """Return (strong_split_positions, all_split_positions) sorted."""
    strong = set()
    all_pts = set()

    if not text:
        return [], []

    n = len(text)

    # Level 1: Strong conjunctions mid-text (STRONG)
    for conj in STRONG_CONJUNCTIONS:
        idx = text.find(conj, 1)
        while idx > 0:
            if _is_split_viable(text, idx):
                strong.add(idx)
                all_pts.add(idx)
            idx = text.find(conj, idx + 1)

    # Level 2: Topic shift 那 (STRONG)
    idx = text.find("那", 1)
    while idx > 0:
        if _is_split_viable(text, idx) and _is_topic_shift_na(text, idx):
            strong.add(idx)
            all_pts.add(idx)
        idx = text.find("那", idx + 1)

    # Level 3: Discourse markers (STRONG) — skip position 0 to avoid standalone 首先/然后
    for marker in DISCOURSE_MARKERS:
        idx = text.find(marker, 1)
        while idx >= 0:
            split_pos = idx + len(marker)
            if split_pos < n - 1 and _is_split_viable(text, split_pos):
                strong.add(split_pos)
                all_pts.add(split_pos)
            idx = text.find(marker, idx + 1)

    # Level 4: Temporal markers (STRONG)
    for marker in TEMP_MARKERS:
        idx = text.find(marker, 1)
        while idx > 0:
            if _is_split_viable(text, idx):
                strong.add(idx)
                all_pts.add(idx)
            idx = text.find(marker, idx + 1)

    # Level 5: Response + topic patterns (STRONG)
    for p in _find_response_topic_splits(text):
        strong.add(p)
        all_pts.add(p)

    # Level 6: OK isolation (MEDIUM)
    for ok_word in ("OK", "ok", "Okay", "Ok"):
        idx = text.find(ok_word)
        while idx >= 0:
            ok_end = idx + len(ok_word)
            ok_end_char = text[ok_end] if ok_end < n else " "
            if ok_end <= n and (ok_end >= n or ok_end_char in "，。？！\n "):
                if ok_end < n:
                    all_pts.add(ok_end)
                elif idx > 2:
                    all_pts.add(idx)
            idx = text.find(ok_word, idx + 1)

    # Level 7: Split BEFORE response tags (MEDIUM)
    for tag in SPLIT_BEFORE_TAGS:
        idx = text.find(tag, 1)
        while idx > 0:
            if _is_split_viable(text, idx):
                all_pts.add(idx)
            idx = text.find(tag, idx + 1)

    # Level 8: Repetition — leading CJK sequence repeats later in text (MEDIUM)
    leading_cjk = ""
    for ch in text:
        if not ch.isascii():
            leading_cjk += ch
            if len(leading_cjk) >= 2:
                break
    if len(leading_cjk) >= 2:
        cjk_positions = [(i, ch) for i, ch in enumerate(text) if not ch.isascii()]
        cjk_only = "".join(ch for _, ch in cjk_positions)
        if len(cjk_only) >= len(leading_cjk):
            second = cjk_only.find(leading_cjk, len(leading_cjk))
            if second > 0 and second < len(cjk_positions):
                orig_pos = cjk_positions[second][0]
                if _is_split_viable(text, orig_pos):
                    all_pts.add(orig_pos)

    min_left = 1
    min_right = 2
    strong_sorted = sorted(p for p in strong if min_left <= p <= n - min_right)
    all_sorted = sorted(p for p in all_pts if min_left <= p <= n - min_right)

    return strong_sorted, all_sorted


# ── Recursive splitting ─────────────────────────────────────────────

def _score_split(text: str, pos: int, text_len: int) -> float:
    """Score a split position — lower is better."""
    ratio = pos / text_len if text_len > 0 else 0.5
    if ratio < 0.3:
        return 0.3 - ratio
    elif ratio > 0.7:
        return ratio - 0.7
    return 0


def _split_recursive(seg: dict) -> list[dict]:
    """Recursively split segment at semantic split points."""
    text = seg["text"].strip()
    if not text:
        return [seg]

    text_len = len(text)
    strong_pts, all_pts = _find_split_points(text)

    candidates = list(strong_pts) if strong_pts else list(all_pts)
    if not candidates:
        return [seg]

    viable = []
    for p in candidates:
        right_text = text[p:].strip()
        left_text = text[:p].strip()
        # 根据触发词类型动态决定最小长度要求
        min_len = _min_len_for_pos(text, p)
        left_ok = len(left_text) >= min_len or _is_protected(left_text)
        right_ok = len(right_text) >= min_len or _is_protected(right_text)
        if left_ok and right_ok:
            viable.append(p)

    if not viable:
        return [seg]

    best = min(viable, key=lambda p: _score_split(text, p, text_len))

    left_text = text[:best].strip()
    right_text = text[best:].strip()
    if not left_text or not right_text:
        return [seg]

    ratio = len(left_text) / text_len if text_len > 0 else 0.5
    duration = seg["end"] - seg["start"]
    split_time = seg["start"] + duration * ratio

    left_seg = {"text": left_text, "start": seg["start"], "end": split_time}
    right_seg = {"text": right_text, "start": split_time, "end": seg["end"]}

    result = []
    result.extend(_split_recursive(left_seg))
    result.extend(_split_recursive(right_seg))
    return result


# ── ASR cleanup ─────────────────────────────────────────────────────

def _clean_segments(segments: list[dict]) -> list[dict]:
    """
    过滤上游 ASR 产生的噪声 segment：
    - 空文本
    - 零时长（start == end）
    - (文本, 起始时间) 完全重复的条目
    """
    seen: set[tuple[str, float]] = set()
    clean: list[dict] = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if seg["end"] <= seg["start"]:
            continue
        key = (text, round(seg["start"], 3))
        if key in seen:
            continue
        seen.add(key)
        clean.append(seg)
    return clean


# ── Main refine pipeline ────────────────────────────────────────────

def refine(segments: list[dict]) -> list[dict]:
    if not segments:
        return []

    segments = _clean_segments(segments)

    split_segs = []
    for seg in segments:
        sub = _split_recursive(seg)
        split_segs.extend(sub)

    return split_segs


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refine SRT segments using content-based semantic analysis"
    )
    parser.add_argument("input", help="input SRT file path")
    parser.add_argument("output", nargs="?",
                        help="output SRT file path (default: overwrite input)")
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
    out = refine(segs)
    output_path = args.output or str(input_path)
    write_srt(out, output_path)

    cleaned_count = len([s for s in segs])  # after _clean_segments inside refine
    print(
        f"refined {original_count} → {len(out)} segments → {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
