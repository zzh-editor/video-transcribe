#!/usr/bin/env python3
"""
LLM 语义断句复核辅助脚本（Step 3.5）。

解决三批问题：
  1. 英文断点切词 —— modular/mouse 被从中间切开
  2. 中文断点切词 —— 断点落在 jieba 词内部
  3. 临时脚本重复编写 —— 每次手写 split_map / 应用脚本

核心思路：
  把「切分点安全性」和「时间戳计算」从 LLM 手里拿回来，交给确定性代码保证。
  LLM 只负责从 safe_cuts 里「选哪个语义最合适」，不再造切分点 + 算时间戳。

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


def _visible(s: str) -> str:
    return s.replace(" ", "").replace("\u200b", "")


def _is_cn(ch: str) -> bool:
    return '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf'


def _has_punct(text: str) -> bool:
    return any(ch in CN_PUNCT for ch in text)


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
        t = char_times[i] if i < len(char_times) else None
        tp = char_times[i - 1] if i - 1 < len(char_times) else None
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
    return plan


def apply(srt_path: str, plan_path: str) -> list[str]:
    plan = json.load(open(plan_path, encoding="utf-8"))
    srt_segs = _parse_srt(srt_path)
    index_map = {seg["index"]: seg for seg in srt_segs}
    word_map = {}

    for idx_str, info in plan.items():
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

    out = []
    counter = 0
    for seg in srt_segs:
        sub = seg.get("split")
        if sub:
            for piece in sub:
                counter += 1
                out.append(f"{counter}\n{piece['raw_start']} --> {piece['raw_end']}\n{piece['text']}\n")
        else:
            counter += 1
            out.append(f"{counter}\n{seg['raw_start']} --> {seg['raw_end']}\n{seg['text']}\n")
    return out


def main():
    ap = argparse.ArgumentParser(description="LLM 语义断句复核辅助")
    ap.add_argument("input", help="words.json（默认模式）或 splits.json（--apply 模式）")
    ap.add_argument("srt", nargs="?", help="raw.srt（默认模式）")
    ap.add_argument("-o", "--output", default="splits.json")
    ap.add_argument("--apply", action="store_true", help="应用 selected_cuts 写回")
    args = ap.parse_args()

    if args.apply:
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
    total = len(plan)
    reliable = sum(1 for v in plan.values() if v["time_reliable"])
    print(f"候选段 {total}（时间可靠 {reliable}）→ {args.output}")
    for k, v in plan.items():
        flag = "" if v["time_reliable"] else " [⏱不可靠]"
        print(f"  #{k}: {v['reason']} {len(v['text'])}字 safe_cuts={len(v['safe_cuts'])}{flag} | {v['text'][:26]}")


if __name__ == "__main__":
    main()
