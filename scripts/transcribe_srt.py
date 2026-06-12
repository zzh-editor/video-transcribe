#!/usr/bin/env python3
"""
Generate precise-word-timestamp SRT subtitles using faster-whisper (default)
with automatic fallback to mlx-whisper on Apple Silicon if installed.

Usage:
    python3 transcribe_srt.py <audio_file> [--output <srt_path>] [--engine <auto|faster|mlx>] [--language <code>]

Arguments:
    audio_file       mp3/wav/m4a etc.
    --output         output SRT path (default: <audio>.srt)
    --engine         transcription engine: auto (default), faster, mlx
    --language       language code (default auto-detect, e.g. en/zh/ja)
    --max-line-ms    max milliseconds per subtitle line (default 6000)
    --model          faster-whisper model name (default: large-v3-turbo)
"""

import argparse
import platform
import sys
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


SENTENCE_END = ".?!。？！…"
SOFT_BREAK = ",;:，；：、"


class _PseudoWord:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.word = text


def _flush(words):
    text = "".join(w.word for w in words).strip()
    if not text:
        return None
    return {"start": words[0].start, "end": words[-1].end, "text": text}


def _find_soft_cut(words):
    for j in range(len(words) - 1, -1, -1):
        t = words[j].word.strip()
        if t and t[-1] in SOFT_BREAK:
            return j
    return None


def _find_pause_cut(words, min_gap=0.2):
    best_gap, best_j = min_gap, None
    start = max(1, len(words) // 3)
    for j in range(start, len(words) - 1):
        gap = words[j + 1].start - words[j].end
        if gap > best_gap:
            best_gap, best_j = gap, j
    return best_j


def _postprocess(result):
    cleaned = []
    for item in result:
        if item["end"] <= item["start"]:
            continue
        if cleaned and item["text"] == cleaned[-1]["text"]:
            cleaned[-1]["end"] = max(cleaned[-1]["end"], item["end"])
            continue
        cleaned.append(item)

    merged = []
    for item in cleaned:
        duration = item["end"] - item["start"]
        word_count = len(item["text"].split())
        if merged and duration < 0.4 and word_count < 3:
            merged[-1]["end"] = item["end"]
            merged[-1]["text"] += " " + item["text"]
        else:
            merged.append(item)

    for k in range(1, len(merged)):
        if merged[k]["start"] < merged[k - 1]["end"]:
            merged[k]["start"] = merged[k - 1]["end"]
        if merged[k]["end"] <= merged[k]["start"]:
            merged[k]["end"] = merged[k]["start"] + 0.3

    return merged


def merge_words_to_segments(segments, max_line_ms=6000, pause_ms=500, max_chars=80):
    flat = []
    for seg in segments:
        words = seg.words if seg.words else []
        if not words:
            flat.append(_PseudoWord(seg.start, seg.end, seg.text))
        else:
            flat.extend(words)

    if not flat:
        return []

    result = []
    cur = []
    n = len(flat)
    for i, w in enumerate(flat):
        cur.append(w)
        wtext = w.word.strip()
        cur_text = "".join(x.word for x in cur).strip()
        cur_dur_ms = (w.end - cur[0].start) * 1000

        gap_ms = ((flat[i + 1].start - w.end) * 1000) if i + 1 < n else 0

        end_sentence = bool(wtext) and wtext[-1] in SENTENCE_END
        big_pause = gap_ms >= pause_ms
        too_long = cur_dur_ms >= max_line_ms or len(cur_text) >= max_chars

        if end_sentence or big_pause:
            seg_obj = _flush(cur)
            if seg_obj:
                result.append(seg_obj)
            cur = []
        elif too_long:
            cut = _find_soft_cut(cur)
            if cut is None or cut >= len(cur) - 1:
                cut = _find_pause_cut(cur)
            if cut is not None and cut < len(cur) - 1:
                head, cur = cur[:cut + 1], cur[cut + 1:]
                seg_obj = _flush(head)
                if seg_obj:
                    result.append(seg_obj)
            else:
                seg_obj = _flush(cur)
                if seg_obj:
                    result.append(seg_obj)
                cur = []

    if cur:
        seg_obj = _flush(cur)
        if seg_obj:
            result.append(seg_obj)

    return _postprocess(result)


def _detect_engine():
    if platform.system() == "Darwin":
        try:
            import mlx_whisper
            return "mlx"
        except ImportError:
            pass
    return "faster"


def _transcribe_mlx(audio_path: str, language: str = None):
    try:
        import mlx_whisper
    except ImportError:
        print("error: mlx-whisper not installed, run: pip install mlx-whisper", file=sys.stderr)
        return None

    print("engine: MLX Whisper (Metal GPU)", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    kwargs = {
        "path_or_hf_repo": "mlx-community/whisper-large-v3-turbo",
        "word_timestamps": True,
    }
    if language:
        kwargs["language"] = language

    result = mlx_whisper.transcribe(audio_path, **kwargs)

    lang = result.get("language", "unknown")
    print(f"detected language: {lang}", file=sys.stderr)

    class Seg:
        def __init__(self, d):
            self.start = d["start"]
            self.end = d["end"]
            self.text = d.get("text", "")
            self.words = [Word(w) for w in d.get("words", [])]

    class Word:
        def __init__(self, d):
            self.start = d["start"]
            self.end = d["end"]
            self.word = d.get("word", "")

    return [Seg(s) for s in result.get("segments", [])]


def _transcribe_faster(audio_path: str, model_name: str, language: str = None):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("error: faster-whisper not installed, run: pip install faster-whisper", file=sys.stderr)
        return None

    print(f"engine: faster-whisper (CPU), model: {model_name}", file=sys.stderr)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    print(f"transcribing: {audio_path}", file=sys.stderr)
    transcribe_kwargs = {"word_timestamps": True}
    if language:
        transcribe_kwargs["language"] = language

    segments_iter, info = model.transcribe(audio_path, **transcribe_kwargs)
    print(f"detected language: {info.language} (confidence {info.language_probability:.0%})", file=sys.stderr)

    return list(segments_iter)


def transcribe_to_srt(audio_path: str, output_path: str, engine: str = "auto",
                       model_name: str = "large-v3-turbo",
                       language: str = None, max_line_ms: int = 6000,
                       pause_ms: int = 500):
    if engine == "auto":
        engine = _detect_engine()

    segments = None
    if engine == "mlx":
        segments = _transcribe_mlx(audio_path, language)
        if segments is None:
            print("fallback to faster-whisper...", file=sys.stderr)
            segments = _transcribe_faster(audio_path, model_name, language)
    else:
        segments = _transcribe_faster(audio_path, model_name, language)
        if segments is None:
            print("fallback to MLX Whisper...", file=sys.stderr)
            segments = _transcribe_mlx(audio_path, language)

    if segments is None:
        print("error: no available transcription engine", file=sys.stderr)
        sys.exit(1)

    srt_segments = merge_words_to_segments(segments, max_line_ms=max_line_ms, pause_ms=pause_ms)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(srt_segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")

    print(f"done! {len(srt_segments)} segments -> {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate precise-timestamp SRT subtitles")
    parser.add_argument("audio", help="audio file path")
    parser.add_argument("--output", "-o", help="output SRT path (default: <audio>.srt)")
    parser.add_argument("--engine", "-e", default="auto", choices=["auto", "mlx", "faster"],
                       help="transcription engine (default: auto)")
    parser.add_argument("--model", "-m", default="large-v3-turbo",
                       help="faster-whisper model (default: large-v3-turbo)")
    parser.add_argument("--language", "-l", default=None,
                       help="language code (default: auto-detect)")
    parser.add_argument("--max-line-ms", type=int, default=6000,
                       help="max ms per subtitle line (default: 6000)")
    parser.add_argument("--pause-ms", type=int, default=500,
                       help="pause threshold in ms for line break (default: 500)")

    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: file not found {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    transcribe_to_srt(
        str(audio_path),
        output_path,
        engine=args.engine,
        model_name=args.model,
        language=args.language,
        max_line_ms=args.max_line_ms,
        pause_ms=args.pause_ms,
    )


if __name__ == "__main__":
    main()
