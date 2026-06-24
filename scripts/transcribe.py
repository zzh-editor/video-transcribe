#!/usr/bin/env python3
"""
Transcribe audio to SRT using Whisper with optional VAD pre-splitting and
MMS_FA forced alignment post-processing.

Pipeline:
    VAD pre-split (Silero VAD for mlx, vad_filter for faster-whisper)
    → Whisper transcription per chunk
    → Optional MMS_FA CTC forced alignment (corrects segment boundaries)
    → refine_segments (semantic refinement)

Usage:
    python3 scripts/transcribe.py <audio.wav> [options]
"""

import argparse
import os
import platform
import sys
import time
from pathlib import Path


# ── Constants ──────────────────────────────────────────────────────

SENTENCE_END = frozenset(".?!。？！…")
SOFT_BREAK = frozenset(",;:，；：、—")
INITIAL_PROMPT_ZH = "以下是普通话的句子，使用简体中文书写。"
CONJUNCTIONS = [
    "然后", "所以", "但是", "不过", "而且", "另外", "还有", "接下来",
    "but", "and", "so", "however", "then", "because", "also",
]

PROTECTED_WORDS = frozenset({
    "好", "对", "嗯", "是", "不", "行", "哦", "啊", "呀", "喏",
    "嗯哼",
    "好的", "对的", "明白", "知道", "可以", "没错", "是的", "不行",
    "好吧", "对了", "对哦", "对啊", "嗯嗯", "好啦", "行了", "可以啊",
    "没问题", "没事", "知道了", "明白了", "没关系",
    "ok", "okay", "yes", "no", "right", "sure", "yeah", "yep",
    "nope", "nah", "alright", "indeed",
})


# ── Helpers ────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_pause_threshold(language: str | None) -> float:
    if language and language.startswith(("zh", "ja", "ko")):
        return 0.3
    return 0.5


def _strip_punct(text: str) -> str:
    return text.strip().lower().rstrip(",.?!。？！，、；:\"'").strip()


def is_protected(text: str) -> bool:
    return _strip_punct(text) in PROTECTED_WORDS


def get_model_path() -> str:
    skill_dir = Path(__file__).resolve().parent.parent
    models_dir = skill_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    return str(models_dir)


def get_audio_duration(path: str) -> float:
    """Quickly estimate audio duration via ffprobe (no torch dependency)."""
    import subprocess, json
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", path,
    ]
    try:
        data = json.loads(subprocess.check_output(cmd))
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return 0.0


# ── Word data ──────────────────────────────────────────────────────

class Word:
    __slots__ = ("start", "end", "word", "probability")
    def __init__(self, start: float, end: float, word: str, probability: float = 1.0):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


# ── Raw segment processing ─────────────────────────────────────────

def _normalize_segments(segments: list) -> list[dict]:
    out = []
    for seg in segments:
        if isinstance(seg, dict):
            out.append({
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", "").strip(),
                "words": list(seg.get("words", [])),
            })
        else:
            words = getattr(seg, "words", None) or []
            out.append({
                "start": getattr(seg, "start", 0.0),
                "end": getattr(seg, "end", 0.0),
                "text": getattr(seg, "text", "").strip(),
                "words": list(words),
            })
    return out


def _extract_word_list(words: list) -> list[Word]:
    flat = []
    for w in words:
        if isinstance(w, dict):
            flat.append(Word(
                w.get("start", 0.0), w.get("end", 0.0),
                w.get("word", ""), w.get("probability", 1.0),
            ))
        else:
            flat.append(Word(
                getattr(w, "start", 0.0),
                getattr(w, "end", 0.0),
                getattr(w, "word", ""),
                getattr(w, "probability", 1.0),
            ))
    return flat


def _find_cut(words: list[Word], max_chars: int) -> int | None:
    best = None
    run_chars = 0
    for i, w in enumerate(words):
        wlen = len(w.word.strip().replace(" ", ""))
        if run_chars + wlen > max_chars:
            break
        run_chars += wlen
        if w.word.strip() and w.word.strip()[-1] in SOFT_BREAK:
            best = i
    if best is not None:
        return best

    run_chars = 0
    for conj in CONJUNCTIONS:
        run_chars = 0
        for i, w in enumerate(words):
            wlen = len(w.word.strip().replace(" ", ""))
            run_chars += wlen
            if run_chars > max_chars:
                break
            if w.word.strip().startswith(conj) and i > 0:
                return i - 1

    best_gap, best_j = 0.15, None
    for j in range(len(words) - 1):
        gap = words[j + 1].start - words[j].end
        if gap > best_gap:
            best_gap, best_j = gap, j
    if best_j is not None:
        return best_j

    run_chars = 0
    for i, w in enumerate(words):
        wlen = len(w.word.strip().replace(" ", ""))
        if run_chars + wlen >= max_chars and i > 0:
            return i - 1
        run_chars += wlen

    return None


def _split_by_ratio(seg: dict, max_chars: int, max_line_ms: int) -> list[dict]:
    text = seg["text"]
    chars = len(text.replace(" ", ""))
    duration = seg["end"] - seg["start"]
    max_dur = max_line_ms / 1000

    n_pieces = max(
        (chars + max_chars - 1) // max_chars,
        int(duration / max_dur) + 1,
    )
    n_pieces = max(n_pieces, 1)

    if n_pieces <= 1:
        return [seg]

    result = []
    char_pos = 0
    start_time = seg["start"]
    target_chars = chars // n_pieces

    for i in range(n_pieces):
        if i == n_pieces - 1:
            piece_text = text[char_pos:].strip()
        else:
            end_char = min(char_pos + target_chars, len(text))
            for c in range(end_char, max(char_pos, end_char - 6), -1):
                c = min(c, len(text) - 1)
                if c > char_pos and text[c:c + 1] in "，、。？！；：":
                    end_char = c + 1
                    break
            piece_text = text[char_pos:end_char].strip()
            char_pos = end_char

        if piece_text:
            ratio = len(piece_text.replace(" ", "")) / max(chars, 1)
            end_time = seg["start"] + duration * ratio
            result.append({"start": start_time, "end": end_time, "text": piece_text,
                           "words": []})
            start_time = end_time

    return result


def _split_long_segment(seg: dict, max_chars: int, max_line_ms: int) -> list[dict]:
    word_list = _extract_word_list(seg.get("words", []))
    if not word_list:
        return _split_by_ratio(seg, max_chars, max_line_ms)

    result = []
    cur = []

    for w in word_list:
        wtext = w.word.strip()
        if not wtext:
            continue

        cur.append(w)

        cur_text = "".join(x.word for x in cur).replace("\u200b", "").strip()
        cur_chars = len(cur_text.replace(" ", ""))
        cur_dur = cur[-1].end - cur[0].start

        if cur_chars >= max_chars or cur_dur >= max_line_ms / 1000:
            cut = _find_cut(cur, max_chars)
            if cut is not None and cut < len(cur) - 1:
                head = cur[:cut + 1]
                cur = cur[cut + 1:]
            else:
                if cur_chars > max_chars and len(cur) > 2:
                    head = cur[:-1]
                    cur = [cur[-1]]
                else:
                    mid = max(1, len(cur) // 2)
                    head = cur[:mid]
                    cur = cur[mid:]

            ht = "".join(x.word for x in head).replace("\u200b", "").strip()
            if ht:
                result.append({"start": head[0].start, "end": head[-1].end,
                               "text": ht, "words": []})

    if cur:
        ct = "".join(x.word for x in cur).replace("\u200b", "").strip()
        if ct:
            result.append({"start": cur[0].start, "end": cur[-1].end,
                           "text": ct, "words": []})

    return result if result else [{
        "start": seg["start"], "end": seg["end"],
        "text": seg["text"], "words": [],
    }]


# ── Model cache migration ──────────────────────────────────────────

def _migrate_model_cache(models_dir: str):
    default_hub = Path.home() / ".cache/huggingface/hub"
    old_model_dir = default_hub / "models--mlx-community--whisper-large-v2-mlx"
    new_hub = Path(models_dir) / "hub"
    new_model_dir = new_hub / "models--mlx-community--whisper-large-v2-mlx"

    if old_model_dir.exists() and not new_model_dir.exists():
        new_hub.mkdir(parents=True, exist_ok=True)
        os.symlink(
            str(old_model_dir.resolve()),
            str(new_model_dir.resolve()),
            target_is_directory=True,
        )
        print(f"model cache: symlinked existing model → {new_model_dir}", file=sys.stderr)


# ── VAD (Silero VAD for mlx) ───────────────────────────────────────

def _vad_split_mlx(audio_path: str, min_segment_s: float = 0.5,
                   merge_gap_s: float = 1.0) -> list[dict]:
    """Split audio into VAD-based speech segments. Returns list of (start, end) in seconds."""
    import soundfile as sf
    import numpy as np

    audio_np, sr = sf.read(audio_path, dtype='float32')
    if audio_np.ndim > 1:
        audio_np = audio_np.mean(axis=1)
    audio_len = len(audio_np)

    try:
        from silero_vad_notorch import load_silero_vad, get_speech_timestamps
        vad_model = load_silero_vad(onnx=True)
        speech_segs = get_speech_timestamps(
            audio_np, vad_model,
            sampling_rate=sr,
            threshold=0.5,
            min_speech_duration_ms=int(min_segment_s * 1000),
            min_silence_duration_ms=500,
            return_seconds=True,
        )
    except Exception as e:
        print(f"warning: Silero VAD failed ({e}), falling back to full audio", file=sys.stderr)
        return [{"start": 0.0, "end": audio_len / sr}]

    if not speech_segs:
        return [{"start": 0.0, "end": audio_len / sr}]

    merged = [speech_segs[0]]
    for seg in speech_segs[1:]:
        if seg['start'] - merged[-1]['end'] < merge_gap_s:
            merged[-1]['end'] = seg['end']
        else:
            merged.append(seg)

    filtered = [s for s in merged if s['end'] - s['start'] >= min_segment_s]
    return filtered if filtered else [{"start": 0.0, "end": audio_len / sr}]


def _transcribe_vad_chunks(audio_path: str, model_name: str, language: str | None,
                            merge_gap_s: float = 1.0) -> list[dict]:
    """Transcribe audio by splitting into VAD chunks and transcribing each independently."""
    import mlx_whisper
    import numpy as np
    import soundfile as sf

    audio_np, sr = sf.read(audio_path, dtype='float32')

    speech_segs = _vad_split_mlx(audio_path, merge_gap_s=merge_gap_s)
    print(f"vad: {len(speech_segs)} speech segments", file=sys.stderr)

    all_segments = []
    for i, vs in enumerate(speech_segs):
        chunk_start = int(vs['start'] * 16000)
        chunk_end = int(vs['end'] * 16000)
        chunk = audio_np[chunk_start:chunk_end]

        if len(chunk) < 16000 * 0.3:
            continue

        try:
            result = mlx_whisper.transcribe(
                chunk,
                path_or_hf_repo=model_name,
                language=language,
                word_timestamps=True,
                initial_prompt=INITIAL_PROMPT_ZH if language and language.startswith("zh") else None,
            )
            for seg in result.get("segments", []):
                seg["start"] += vs['start']
                seg["end"] += vs['start']
                all_segments.append(seg)
            print(f"  chunk {i+1}/{len(speech_segs)} [{vs['start']:.1f}-{vs['end']:.1f}s] "
                  f"→ {len(result.get('segments', []))} segs", file=sys.stderr)
        except Exception as e:
            print(f"  chunk {i+1} [{vs['start']:.1f}-{vs['end']:.1f}s] failed: {e}",
                  file=sys.stderr)

    if not all_segments:
        print("vad: no segments produced, falling back to full audio", file=sys.stderr)
        result = mlx_whisper.transcribe(
            audio_np, path_or_hf_repo=model_name,
            language=language, word_timestamps=True,
            initial_prompt=INITIAL_PROMPT_ZH if language and language.startswith("zh") else None,
        )
        all_segments = result.get("segments", [])

    all_segments.sort(key=lambda s: s.get("start", 0))
    return all_segments


# ── Transcribe backends ────────────────────────────────────────────

def transcribe_mlx(audio_path: str, model_name: str, language: str | None,
                   max_line_length: int, pause_threshold: float,
                   max_line_ms: int, vad: bool = False) -> list[dict]:
    models_dir = get_model_path()
    _migrate_model_cache(models_dir)
    os.environ["HF_HOME"] = models_dir
    os.environ["XDG_CACHE_HOME"] = str(Path(models_dir) / "cache")

    import mlx_whisper as _mw

    print(f"engine: mlx-whisper", file=sys.stderr)
    print(f"model: {model_name}", file=sys.stderr)
    print(f"device: Apple Silicon (MLX)", file=sys.stderr)
    print(f"    transcribing: {audio_path}", file=sys.stderr)

    t0 = time.time()

    if vad:
        raw_segments = _transcribe_vad_chunks(audio_path, model_name, language)
    else:
        result = _mw.transcribe(
            audio_path,
            path_or_hf_repo=model_name,
            language=language,
            word_timestamps=True,
            initial_prompt=INITIAL_PROMPT_ZH if language and language.startswith("zh") else None,
        )
        raw_segments = result.get("segments", [])

    elapsed = time.time() - t0
    print(f"transcription took {elapsed:.1f}s", file=sys.stderr)

    if not raw_segments:
        print("error: no segments in mlx-whisper output", file=sys.stderr)
        sys.exit(1)

    raw_count = len(raw_segments)

    try:
        from refine_segments import refine as refine_segs
        before = len(raw_segments)
        out = refine_segs(raw_segments, max_chars=max_line_length)
        if len(out) != before:
            print(f"refine_segments: {before} → {len(out)} segments (semantic merge/split)",
                  file=sys.stderr)
    except ImportError:
        print("refine_segments not available, skipping", file=sys.stderr)
        out = raw_segments

    print(f"segments: {raw_count} raw segs → {len(out)} segments",
          file=sys.stderr)
    return out


def transcribe_faster(audio_path: str, model_name: str, language: str | None,
                      max_line_length: int, pause_threshold: float,
                      max_line_ms: int, vad: bool = False) -> list[dict]:
    models_dir = get_model_path()
    os.environ.setdefault("HF_HOME", models_dir)

    from faster_whisper import WhisperModel

    print(f"engine: faster-whisper", file=sys.stderr)
    print(f"model: {model_name}", file=sys.stderr)
    print(f"device: CPU", file=sys.stderr)
    print(f"vad_filter: {vad}", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    t0 = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         download_root=models_dir)
    seg_iter, info = model.transcribe(
        audio_path, language=language,
        word_timestamps=True,
        vad_filter=vad,
        initial_prompt=INITIAL_PROMPT_ZH if language and language.startswith("zh") else None,
    )
    elapsed = time.time() - t0
    print(f"transcription took {elapsed:.1f}s", file=sys.stderr)
    print(f"detected language: {info.language}", file=sys.stderr)

    raw_segments = list(seg_iter)
    raw_count = len(raw_segments)

    try:
        from refine_segments import refine as refine_segs
        before = len(raw_segments)
        out = refine_segs(raw_segments, max_chars=max_line_length)
        if len(out) != before:
            print(f"refine_segments: {before} → {len(out)} segments (semantic merge/split)",
                  file=sys.stderr)
    except ImportError:
        print("refine_segments not available, skipping", file=sys.stderr)
        out = raw_segments

    print(f"segments: {raw_count} raw segs → {len(out)} segments",
          file=sys.stderr)
    return out


# ── SRT writer ─────────────────────────────────────────────────────

def write_srt(segments: list[dict], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")


# ── Platform detection ─────────────────────────────────────────────

def detect_platform() -> dict:
    return {"is_macos": sys.platform == "darwin",
            "is_arm": platform.machine() == "arm64"}


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio to SRT using Whisper"
    )
    parser.add_argument("audio", help="audio file path (WAV 16kHz mono)")
    parser.add_argument("--output", "-o", help="output SRT path")
    parser.add_argument("--language", "-l", help="language code (e.g. zh, en)")
    parser.add_argument("--max-line-length", type=int, default=25,
                        help="max characters per subtitle line (default: 25)")
    parser.add_argument("--max-line-ms", type=int, default=6000,
                        help="max duration per subtitle block in ms (default: 6000)")
    parser.add_argument("--pause-ms", type=int, default=None,
                        help="pause threshold for sentence split in ms (default: 300 zh / 500 en)")
    parser.add_argument("--engine", choices=["auto", "mlx", "faster-whisper"],
                        default="auto", help="force a specific engine")
    parser.add_argument("--vad", action="store_true", default=None,
                        help="enable VAD pre-splitting (Silero VAD for mlx, vad_filter for faster)")
    parser.add_argument("--no-vad", action="store_true", default=None,
                        help="disable VAD pre-splitting")

    args = parser.parse_args()

    # ── Resolve boolean flags with auto-detection ──────────────
    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: file not found {audio_path}", file=sys.stderr)
        sys.exit(1)

    audio_dur = get_audio_duration(str(audio_path))
    is_long_audio = audio_dur > 600  # > 10 min

    vad_enabled = args.vad if args.vad is not None else (
        False if args.no_vad else is_long_audio
    )

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    lang = args.language
    pause_threshold = (args.pause_ms / 1000.0) if args.pause_ms is not None else get_pause_threshold(lang)

    plat = detect_platform()

    engine = args.engine
    if engine == "auto":
        if plat["is_macos"] and plat["is_arm"]:
            engine = "mlx"
        else:
            engine = "faster-whisper"

    if engine == "mlx":
        model_name = "mlx-community/whisper-large-v2-mlx"
    else:
        model_name = "large-v2"

    if engine == "mlx" and not (plat["is_macos"] and plat["is_arm"]):
        print("warning: mlx engine requires macOS arm64, falling back to faster-whisper",
              file=sys.stderr)
        engine = "faster-whisper"
        model_name = "large-v2"

    print(f"audio duration: {audio_dur:.0f}s", file=sys.stderr)
    print(f"vad: {'on' if vad_enabled else 'off'}", file=sys.stderr)

    seg_func = transcribe_mlx if engine == "mlx" else transcribe_faster
    segments = seg_func(
        str(audio_path), model_name, lang,
        args.max_line_length, pause_threshold, args.max_line_ms,
        vad=vad_enabled,
    )

    write_srt(segments, output_path)
    print(f"done! {len(segments)} segments -> {output_path}", file=sys.stderr)
    return output_path


if __name__ == "__main__":
    main()
