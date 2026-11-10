#!/usr/bin/env python3
"""
LLM 语义断句复核辅助脚本（Step 3.5）。

解决四批问题：
  1. 英文断点切词 —— modular/mouse 被从中间切开
  2. 中文断点切词 —— 断点落在 jieba 词内部
  3. 相邻残句 —— ASR 把一句话切成「它可以 blend」/「这个底下」
  4. 临时脚本重复编写 —— 每次手写 split_map / 应用脚本

核心思路：
  把「切分点安全性」和「时间戳计算」从 LLM 手里拿回来，交给确定性代码保证。
  LLM 只负责从 safe_cuts 里「选哪个语义最合适」，并从 merge_candidates 里选
  可安全合并的原始相邻条目；不再造切分点 + 算时间戳。

输入的 words.json 是 transcribe.py 每次转录默认产出（<srt>.words.json）。
Groq 的 words 为字符级/词组级，可能与 text 不完全逐字对齐（错序、错切，
如 smooth→s+moot h、感觉好吧→感+好吧+觉）。脚本先做 text↔words 字符对齐，
覆盖率不足或时序错乱时把该段标记 time_reliable=false，不强行切。

用法：
  # 生成候选 + 安全断点（默认输出 splits.json）
  venv/bin/python3 scripts/semantic_review.py <words.json> <raw.srt> -o splits.json

  # LLM 确认断点后写回（读 splits.json 的 selected_cuts）
  venv/bin/python3 scripts/semantic_review.py <raw.srt> --apply splits.json -o refined.srt

splits.json 结构：
  {
    "<seg_index>": {
      "text": "整段文本",
      "reason": ["no_punct_long"|"long_dur"|"fragment"|"newline_break", ...],
      "time_reliable": true/false,
      "time_mode": "words"|"proportional",  # 缺省 = words
      "seg_start": 0.0, "seg_end": 5.0,
      "visible_len": 18,
      "safe_cuts": [
        {"char": 8, "boundary": "词︱词", "start": 3.42, "end": 5.01}
      ],
       "selected_cuts": [8, 15]
     }
   },
   "_merge_candidates": [
     {"segments": [26, 27], "reason": "continuation", "text": "..."}
   ],
   "_selected_merges": [[26, 27]]
  }

  words 缺失但含换行（\\n）的段：Groq 返回的多行 text 在 transcribe 阶段因无
  words 无法评分重切，raw.srt 里保留为多行。此时 safe_cuts 由 _newline_cuts
  生成（每个换行处一个断点），time_mode="proportional"，时间戳按字符位置
  在 seg_start..seg_end 内等比分，标记 time_reliable=true。
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import jieba
except ImportError:
    jieba = None

# ── 常量 ──────────────────────────────────────────────────────────

CANDIDATE_MIN_CHARS = 16   # 去空白 ≥ 16 字且无标点 → 候选
CANDIDATE_MAX_DUR_S = 4.0  # 时长 > 4s → 候选
FRAGMENT_MAX_CHARS = 4     # 纯英极短残片 → 候选
COVERAGE_OK = 0.9          # 字符时间戳覆盖率 ≥ 90% 才认为 time_reliable

CN_PUNCT = frozenset("，。！？；：、…—～·,.!?;:…\"'“”‘’（）()《》〈〉【】[]「」『』")
STRONG_PUNCT = frozenset("。！？.!?…")
WEAK_PUNCT = frozenset("，、；：,;:")

# 英文 token：连续的字母/数字/常见符号视为一个不可切整体
EN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-#&/]*")

# These starts are usually syntactic continuations, not a fresh subtitle idea.
# They are only used to propose a merge to the LLM; applying a merge still
# requires an explicit selection in splits.json.
CONTINUATION_START_RE = re.compile(
    r"^(?:的|地|得|了|着|过|吧|吗|呢|啊|呀|去|来|到|在|把|被|"
    r"这个|那个|这样|怎么|什么|可以|会|要|就|还|再|也|而|并|加|掉)"
)
MERGE_MAX_CHARS = 30
MERGE_MAX_DUR_S = 5.5
MERGE_MAX_GAP_S = 0.35


def _visible(s: str) -> str:
    return s.replace(" ", "").replace("\u200b", "")


def _is_cn(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf'


def _has_punct(text: str) -> bool:
    return any(ch in CN_PUNCT for ch in text)


def _merge_candidates(segs: list[dict]) -> list[dict]:
    """Offer adjacent continuation groups for review without auto-merging.

    The ASR boundary is weak evidence for Chinese speech. A short next cue that
    starts with a particle, demonstrative, or verb often continues the prior
    cue. Keep candidates bounded so the reviewer never turns a paragraph into
    one subtitle.
    """
    candidates = []
    for start in range(len(segs) - 1):
        first = segs[start]
        first_text = first["text"].strip()
        if not first_text or first_text[-1:] in STRONG_PUNCT:
            continue

        merged = first_text
        end = start
        for nxt in segs[start + 1:start + 4]:
            nxt_text = nxt["text"].strip()
            # 「就是」通常是在开启一个解释，而不是补全前一条；把它和
            # continuation 的单字「就」区分开，避免误合并独立教学句。
            if (not nxt_text or nxt_text.startswith("就是")
                    or not CONTINUATION_START_RE.match(nxt_text)):
                break
            previous = segs[end]
            previous_text = previous["text"].strip()
            if previous_text[-1:] in STRONG_PUNCT:
                break
            gap = nxt["start"] - previous["end"]
            proposed = merged + nxt_text
            if (gap > MERGE_MAX_GAP_S or len(_visible(proposed)) > MERGE_MAX_CHARS
                    or nxt["end"] - first["start"] > MERGE_MAX_DUR_S):
                break
            merged = proposed
            end += 1
            candidates.append({
                "segments": [segs[start]["index"], segs[end]["index"]],
                "reason": "continuation",
                "text": merged,
                "gap_ms": round(gap * 1000),
                "visible_len": len(_visible(merged)),
            })
    return candidates


# ── 候选筛选 ──────────────────────────────────────────────────────

def _classify_candidate(text: str, dur: float) -> list[str]:
    """返回候选原因列表；空列表 = 非候选。"""
    reasons = []
    vis = _visible(text)
    if len(vis) >= CANDIDATE_MIN_CHARS and not _has_punct(text):
        reasons.append("no_punct_long")
    if dur > CANDIDATE_MAX_DUR_S:
        reasons.append("long_dur")
    if re.fullmatch(r"[A-Za-z]+", vis) and len(vis) <= FRAGMENT_MAX_CHARS:
        reasons.append("fragment")
    # 含换行的段：Groq 自带 \n 是按停顿自然切好的多行语义单元。
    # 即使 words 缺失，也能按换行位置等比分配时间切分（用户指出的
    # 「其实有空格能切」—— 空格/\n 就是最可靠的断点）。
    if "\n" in vis and len(vis.replace("\n", "")) >= CANDIDATE_MIN_CHARS:
        reasons.append("newline_break")
    return reasons


# ── 字符对齐（text ↔ words）──────────────────────────────────────

def _align_chars(text: str, words: list[dict]):
    """把 text 的每个可视字符映射到 words 提供的时间。

    Groq 的 words 可能与 text 不一致（多字聚词、错序、错切如 smooth→s+moot h）。
    用贪心：按 text 顺序游走，每次在当前 words 缓冲里拼出与 text 前缀匹配的字串。

    返回 (char_times, reliable)：
      char_times: 与 text 等长的数组，每项 {start,end} 或 None
      reliable:   覆盖率 ≥ COVERAGE_OK 且时序单调
    """
    n = len(text)
    times: list[dict | None] = [None] * n
    buf_chars: list[str] = []      # 已取出待匹配的 word 字符
    buf_idx: list[tuple] = []      # 每个缓冲字符对应的 (start,end)
    wi = 0
    matched = 0

    def refill():
        nonlocal wi
        while len(buf_idx) < 96 and wi < len(words):
            w = words[wi]
            wtxt = w.get("word", "")
            st = w.get("start")
            en = w.get("end")
            wi += 1
            if st is None or en is None or not wtxt:
                continue
            for ch in wtxt:
                buf_idx.append((st, en))
                buf_chars.append(ch)

    refill()
    i = 0
    while i < n:
        ch = text[i]
        if not ch.strip() or ch == "\u200b":
            i += 1
            continue
        # 缓冲头部直接匹配
        if buf_chars and buf_chars[0] == ch:
            times[i] = {"start": buf_idx[0][0], "end": buf_idx[0][1]}
            buf_chars.pop(0)
            buf_idx.pop(0)
            matched += 1
            i += 1
            refill()
            continue
        # 缓冲头部不匹配：前向搜索（容忍错序/错切）
        found = -1
        for k in range(min(len(buf_chars), 32)):
            if buf_chars[k] == ch:
                found = k
                break
        if found >= 0:
            times[i] = {"start": buf_idx[found][0], "end": buf_idx[found][1]}
            del buf_chars[:found + 1]
            del buf_idx[:found + 1]
            matched += 1
            i += 1
            refill()
            continue
        # 缓冲耗尽仍不匹配 → 该字符无时间（Groq 未转出）
        refill()
        if not buf_chars:
            i += 1
            continue
        # 缓冲非空仍不匹配 → 顺序彻底错乱，标记后丢一个缓冲字符继续（防死循环）
        times[i] = None
        i += 1

    vis_count = sum(1 for c in text if c.strip() and c != "\u200b")
    cov = matched / vis_count if vis_count else 1.0
    reliable = cov >= COVERAGE_OK and _timing_monotonic(times)
    return times, reliable


def _timing_monotonic(times: list) -> bool:
    """时间戳应大致单调不减（仅比较相邻 start，容忍小抖动）。

    注意：多字词（如「然后」）的每个字符共享同一 [start,end] 区间，所以
    只保证 start 序列单调即可；用 end 严格递增会把所有含多字词的合法段
    误判为错序。
    """
    prev = -1.0
    for t in times:
        if t is None:
            continue
        if t["start"] + 1e-6 < prev:
            return False
        prev = t["start"]
    return True


# ── 安全断点计算 ─────────────────────────────────────────────────

def _safe_boundaries(text: str) -> set[int]:
    """返回 text 中「安全断点」的字符索引集合（断在此处不会切词）。

    - 中文：断点须落在 jieba 词边界
    - 英文：断点不得落在 EN token 内部
    - 组合：中-英 / 英-中 / 标点边 → 天然安全
    """
    safe: set[int] = set()

    # jieba 中文词边界（仅当文本含中文）
    cn_boundary: set[int] = set(range(1, len(text)))
    if jieba is not None and any(_is_cn(c) for c in text):
        try:
            toks = list(jieba.tokenize(text, mode='search'))
            if toks:
                cb = set()
                for word, s, e in toks:
                    if any(_is_cn(c) for c in word):
                        cb.add(s)
                        cb.add(e)
                cn_boundary = cb
        except Exception:
            pass

    # 英文 token 区间（不可内部切）
    en_ranges: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in EN_RE.finditer(text)
    ]

    for i in range(1, len(text)):
        left = text[i - 1]
        right = text[i]
        if left.isspace() or right.isspace() or left in CN_PUNCT or right in CN_PUNCT:
            safe.add(i)
            continue
        left_cn = _is_cn(left)
        right_cn = _is_cn(right)
        if left_cn and right_cn:
            if i in cn_boundary:
                safe.add(i)
        elif not left_cn and not right_cn:
            inside = any(s < i < e for s, e in en_ranges)
            if not inside:
                safe.add(i)
        else:
            safe.add(i)
    return safe


def _nearest_time(char_times: list, i: int, forward: bool) -> dict | None:
    """最近的非空字符时间。英文断点落在空格上，而空格没有时间戳 ——
    直接取 char_times[i] 会得到 None，apply 时把分段压成零时长。"""
    step = 1 if forward else -1
    j = i
    while 0 <= j < len(char_times):
        if char_times[j]:
            return char_times[j]
        j += step
    return None


def _pick_break_times(text: str, safe: set[int], char_times: list) -> list[dict]:
    """把安全断点转成带时间戳的 cuts。断点 i 切在 text[i] 起点。

    覆盖 text 首尾附近的标点/空白无效断点，且跳过纯成对的括号内部孤独断点。
    """
    cuts = []
    for i in sorted(safe):
        if i <= 0 or i >= len(text):
            continue
        if text[i - 1] in CN_PUNCT or text[i - 1].isspace():
            continue
        left = text[max(0, i - 6):i]
        right = text[i:i + 6]
        entry = {
            "char": i,
            "boundary": f"{left[-4:]}︱{right[:4]}",
            "start": None,
            "end": None,
        }
        t = _nearest_time(char_times, i, forward=True)
        tp = _nearest_time(char_times, i - 1, forward=False)
        if t and tp:
            entry["start"] = round(t["start"], 3)
            entry["end"] = round(tp["end"], 3)
        cuts.append(entry)
    return cuts


def _newline_cuts(text: str, seg_start: float, seg_end: float) -> list[dict]:
    """含 \n 的段：把每个换行处视为语义断点，时间按可视字符位置等比分。

    适用 Groq words 缺失的段（transcribe 阶段无法评分重切，raw.srt 里以多行
    形式保留）。断点 i 是换行后的首个字符位置；start/end 直接算好绝对时间，
    apply 无需额外逻辑即可按比例切分。
    """
    vis_idx = [i for i, c in enumerate(text)
               if not (c.isspace() or c == "\u200b")]
    if not vis_idx:
        return []
    leaves = [0]
    for i, ch in enumerate(text):
        if ch != "\n":
            continue
        j = i + 1
        while j < len(text) and (text[j].isspace() or text[j] == "\u200b"):
            j += 1
        if j < len(text):
            leaves.append(j)
    leaves = sorted(set(leaves))
    if len(leaves) <= 1:
        return []
    duration = seg_end - seg_start
    denom = max(len(vis_idx) - 1, 1)
    cuts = []
    for pos in leaves[1:]:
        if pos not in vis_idx:
            continue
        ratio = vis_idx.index(pos) / denom
        t = seg_start + duration * ratio
        cuts.append({
            "char": pos,
            "boundary": f"{text[max(0, pos - 6):pos][-4:]}︱{text[pos:pos + 4]}",
            "start": round(t, 3),
            "end": round(t, 3),
            "newline": True,
        })
    return cuts


# ── SRT 读写 ─────────────────────────────────────────────────────

def _ts(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt(path: str) -> list[dict]:
    segs = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if not lines[i].strip().isdigit():
            i += 1
            continue
        if i + 1 >= len(lines):
            break
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)",
            lines[i + 1],
        )
        if not m:
            i += 1
            continue
        start = _ts(m[1], m[2], m[3], m[4])
        end = _ts(m[5], m[6], m[7], m[8])
        text = ""
        j = i + 2
        while j < len(lines) and lines[j].strip():
            text += ("\n" if text else "") + lines[j]
            j += 1
        segs.append({
            "index": int(lines[i].strip()),
            "start": start, "end": end, "text": text,
            "raw_start": lines[i + 1].split("-->")[0].strip(),
            "raw_end": lines[i + 1].split("-->")[1].strip(),
            "line_start": i, "line_end": j,
        })
        i = j
    return segs


# ── 主流程 ───────────────────────────────────────────────────────

def generate(words_path: str, srt_path: str) -> dict:
    srt_segs = _parse_srt(srt_path)
    words = json.load(open(words_path, encoding="utf-8"))
    if isinstance(words, dict):
        words = words.get("segments", [])
    by_start = {round(w.get("start", -1), 3): w for w in words}

    plan = {}
    for seg in srt_segs:
        if seg["text"].startswith("http"):  # 跳过脚本 CLI 使用时的无关行兜底
            pass
        key = round(seg["start"], 3)
        ws = by_start.get(key)
        if ws is None:
            cand = min(by_start, key=lambda k: abs(k - key), default=None)
            if cand is not None and abs(cand - key) < 0.5:
                ws = by_start[cand]
        if ws is None:
            continue
        text = seg["text"]
        vis_len = len(_visible(text))
        dur = seg["end"] - seg["start"]
        reasons = _classify_candidate(text, dur)
        if not reasons:
            continue
        char_times, reliable = _align_chars(text, ws.get("words", []))
        safe = _safe_boundaries(text)
        cuts = _pick_break_times(text, safe, char_times)
        # words 缺失但含 \n：换行是 Groq 自带的自然停顿（等价于用户在 raw.srt
        # 里看到的空格分界），按字符比例分时即可切。时间戳为近似值。
        newline_prop = False
        if "\n" in text and not reliable:
            ncuts = _newline_cuts(text, seg["start"], seg["end"])
            if ncuts:
                cuts = ncuts
                newline_prop = True
                reliable = True
                # 覆盖 _pick_break_times 基于 words 生成的 cuts：
                # 该段 words 不可靠，_safe_boundaries/_pick_break_times 的
                # 断点时间不可信，全部换成按比例时间的换行断点。
        plan[str(seg["index"])] = {
            "text": text,
            "reason": reasons,
            "time_reliable": reliable,
            "seg_start": round(seg["start"], 3),
            "seg_end": round(seg["end"], 3),
            "visible_len": vis_len,
            "safe_cuts": cuts,
            "selected_cuts": [],
            **({"time_mode": "proportional"} if newline_prop else {}),
        }
    plan["_merge_candidates"] = _merge_candidates(srt_segs)
    plan["_selected_merges"] = []
    return plan


def _join_caption_text(left: str, right: str) -> str:
    """Join two cue texts without introducing a CJK/Latin spacing regression."""
    left, right = left.strip(), right.strip()
    if (left and right and (
            (left[-1].isascii() and left[-1].isalnum()
             and (right[0].isascii() and right[0].isalnum() or _is_cn(right[0])))
            or (_is_cn(left[-1]) and right[0].isascii() and right[0].isalnum()))):
        return left + " " + right
    return left + right


def _selected_merge_ranges(plan: dict, segs: list[dict]) -> dict[int, int]:
    """Validate explicit merge choices and return {first_index: last_index}."""
    positions = {seg["index"]: pos for pos, seg in enumerate(segs)}
    ranges = {}
    claimed = set()
    for chosen in plan.get("_selected_merges", []):
        if not isinstance(chosen, list) or len(chosen) != 2:
            continue
        first, last = chosen
        if not isinstance(first, int) or not isinstance(last, int) or first >= last:
            continue
        members = set(range(first, last + 1))
        if first not in positions or last not in positions or members & claimed:
            continue
        pos = positions[first]
        if [seg["index"] for seg in segs[pos:pos + len(members)]] != list(range(first, last + 1)):
            continue
        ranges[first] = last
        claimed |= members
    return ranges


def compact_view(plan: dict) -> str:
    """Render a token-lean view of splits.json for LLM review.

    只保留候选段文字、段内切点标号和合并候选；丢弃 words 级浮点时间戳、
    boundary 回显和 start/end 区间。标记写成 [k@char]：k 是段内标号，
    char 是回填 selected_cuts 时要用的字符下标。
    """
    lines = [
        "# splits.json 紧凑视图。标记 [k@char]：k=段内切点标号，char=回填 selected_cuts 的字符下标。",
        "# 选点：把挑中的 char 填进对应段的 selected_cuts；合并：把相邻段号写进 _selected_merges，如 [[1,2]]。",
        "# 标记不属于字幕文字。",
    ]
    for key, info in plan.items():
        if key.startswith("_"):
            continue
        marks = {}
        for number, cut in enumerate(info.get("safe_cuts", []), 1):
            if cut.get("char") is not None:
                marks[cut["char"]] = number
        if not marks:
            continue
        text = info.get("text", "")
        rendered = []
        for pos, char in enumerate(text):
            if pos in marks:
                rendered.append(f"[{marks[pos]}@{pos}]")
            rendered.append(char)
        flags = ",".join(info.get("reason", []))
        reliability = "" if info.get("time_reliable") else " 时间不可靠"
        lines.append(
            f"#{key} {flags} {info.get('visible_len', 0)}字 "
            f"cuts={len(marks)}{reliability}"
        )
        lines.append("".join(rendered).replace("\n", "⏎"))
    candidates = plan.get("_merge_candidates") or []
    if candidates:
        lines.append("# 相邻合并候选")
        for item in candidates:
            segments = item.get("segments") or []
            if len(segments) != 2:
                continue
            lines.append(
                f"merge {segments[0]}-{segments[1]} {item.get('reason', '')} "
                f"gap={item.get('gap_ms', 0)}ms {item.get('visible_len', 0)}字"
            )
    return "\n".join(lines) + "\n"


def apply(srt_path: str, plan_path: str) -> list[str]:
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    srt_segs = _parse_srt(srt_path)
    index_map = {seg["index"]: seg for seg in srt_segs}
    word_map = {}

    for idx_str, info in plan.items():
        if idx_str.startswith("_"):
            continue
        idx = int(idx_str)
        cuts = info.get("selected_cuts") or []
        if not cuts or not info.get("time_reliable"):
            continue
        seg = index_map.get(idx)
        if seg is None:
            continue
        text = seg["text"]
        safe_chars = {c["char"] for c in info.get("safe_cuts", [])}
        # 只保留在安全断点里的 cuts，且单调递增
        chosen = sorted({c for c in cuts if c in safe_chars and 0 < c < len(text)})
        if len(chosen) < 1:
            continue
        # 用字符时间换算：需要每段的 char_times。从 words.json 重建。
        # 为简化 apply，splits.json 里 safe_cuts 已带 start(char[i])/end(char[i-1])，
        # 直接据此定位分段边界时间。
        char_start = {c["char"]: c["start"] for c in info["safe_cuts"] if c["start"] is not None}
        char_end = {c["char"]: c["end"] for c in info["safe_cuts"] if c["end"] is not None}
        seg_begin = seg["start"]
        seg_end = seg["end"]

        prev = 0
        pieces = []   # (text_piece, t_start, t_end)
        for c in chosen:
            t_start = char_start.get(c, seg_begin)
            t_end = char_end.get(c, seg_begin)
            pieces.append((text[prev:c].strip(), seg_begin, t_end))
            seg_begin = t_start
            prev = c
        pieces.append((text[prev:].strip(), seg_begin, seg_end))
        pieces = [p for p in pieces if p[0]]
        if len(pieces) <= 1:
            continue
        seg["split"] = [
            {"raw_start": _fmt(t0), "raw_end": _fmt(t1), "text": txt}
            for txt, t0, t1 in pieces
        ]

    merge_ranges = _selected_merge_ranges(plan, srt_segs)
    by_index = {seg["index"]: seg for seg in srt_segs}
    out = []
    counter = 0
    i = 0
    while i < len(srt_segs):
        seg = srt_segs[i]
        merge_end = merge_ranges.get(seg["index"])
        if merge_end is not None:
            group = [by_index[idx] for idx in range(seg["index"], merge_end + 1)]
            # A selected merge must operate on original cues. Splitting a cue
            # and then merging it would discard the reviewed split boundary.
            if not any(member.get("split") for member in group):
                text = group[0]["text"]
                for member in group[1:]:
                    text = _join_caption_text(text, member["text"])
                counter += 1
                out.append(
                    f"{counter}\n{group[0]['raw_start']} --> {group[-1]['raw_end']}\n{text}\n"
                )
                i += len(group)
                continue
        sub = seg.get("split")
        if sub:
            for piece in sub:
                counter += 1
                out.append(f"{counter}\n{piece['raw_start']} --> {piece['raw_end']}\n{piece['text']}\n")
        else:
            counter += 1
            out.append(f"{counter}\n{seg['raw_start']} --> {seg['raw_end']}\n{seg['text']}\n")
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="LLM 语义断句复核辅助")
    ap.add_argument("input", help="words.json（默认模式）或 splits.json（--apply 模式）")
    ap.add_argument("srt", nargs="?", help="raw.srt（默认模式）")
    ap.add_argument("-o", "--output", default="splits.json")
    ap.add_argument("--apply", action="store_true", help="应用 selected_cuts 写回")
    ap.add_argument("--compact-llm-view", action="store_true", help="仅保留带 [1]、[2] 切点标记的紧凑文本与必要索引，剔除冗余词级浮点时间戳以节省 Token")
    args = ap.parse_args()

    if args.apply:
        if args.compact_llm_view:
            print("错误：--compact-llm-view 只用于生成模式，不能和 --apply 同用", file=sys.stderr)
            sys.exit(2)
        if not args.srt:
            print("错误：--apply 需要一个 .srt 路径作为第二个参数", file=sys.stderr)
            sys.exit(2)
        out = apply(args.srt, args.input)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        print(f"已写回 {len(out)} 条字幕 → {args.output}")
        return

    if not args.srt:
        print("错误：默认模式需要 words.json 和 raw.srt 两个参数", file=sys.stderr)
        sys.exit(2)
    plan = generate(args.input, args.srt)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    segment_plan = {k: v for k, v in plan.items() if not k.startswith("_")}
    total = len(segment_plan)
    reliable = sum(1 for v in segment_plan.values() if v["time_reliable"])
    print(f"候选段 {total}（时间可靠 {reliable}）→ {args.output}")
    for k, v in segment_plan.items():
        flag = "" if v["time_reliable"] else " [时间不可靠]"
        print(f"  #{k}: {v['reason']} {len(v['text'])}字 safe_cuts={len(v['safe_cuts'])}{flag} | {v['text'][:26]}")
    candidates = plan["_merge_candidates"]
    if candidates:
        print(f"相邻合并候选 {len(candidates)} 条（需复核后填入 _selected_merges）")
    if args.compact_llm_view:
        compact_path = Path(args.output).with_suffix(".llm.txt")
        view = compact_view(plan)
        compact_path.write_text(view, encoding="utf-8")
        json_size = Path(args.output).stat().st_size
        saved = 0 if not json_size else round((1 - compact_path.stat().st_size / json_size) * 100)
        print(f"紧凑 LLM 视图 → {compact_path}（较 splits.json 少 {saved}% 字节）")


if __name__ == "__main__":
    main()
