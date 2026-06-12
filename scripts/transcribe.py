#!/usr/bin/env python3
"""
<<<<<<< HEAD
Transcribe audio to SRT using FunASR AutoModel (Fun-ASR-Nano + VAD + punctuation).

Usage:
    python3 scripts/transcribe.py <audio.wav> [--output <raw.srt>] [--language zh] [--device cpu]

Requires: pip install funasr
=======
Transcribe audio/video to SRT using FunASR AutoModel (Fun-ASR-Nano + VAD + punctuation).

Usage:
    python3 transcribe.py <audio/video_file> [--output <srt_path>] [--config <config.json>]

Arguments:
    audio/video_file     input media file (any ffmpeg-supported format)
    --output, -o         output SRT path (default: <input>.srt)
    --config, -c         path to config.json (for output_dir etc.)
    --model              FunASR model name (default: FunAudioLLM/Fun-ASR-Nano-2512)
    --device             device: cpu (default), mps, cuda
    --max-line-duration  max seconds per subtitle line (default: 8.0)
    --max-line-chars     max characters per subtitle line (default: 80)
    --min-line-duration  min seconds before merging short fragments (default: 1.5)
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)
"""

import argparse
import json
<<<<<<< HEAD
import sys
from pathlib import Path


=======
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_audio(input_path: str, output_wav: str):
    print(f"extracting audio: {input_path} -> {output_wav}", file=sys.stderr)
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", output_wav],
        check=True, capture_output=True, text=True
    )


>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)
def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


<<<<<<< HEAD
def extract_segments(result):
    """
    Extract timed segments from FunASR result, handling various output formats.

    Returns list of {"start": float, "end": float, "text": str}.
    """
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return []

    sentence_info = result.get("sentence_info", [])
    if sentence_info and isinstance(sentence_info, list):
        segments = []
        for s in sentence_info:
            text = s.get("text", "").strip()
            if text:
                segments.append({
                    "start": s.get("start", 0.0),
                    "end": s.get("end", 0.0),
                    "text": text,
                })
        if segments:
            return segments

    text = result.get("text", "").strip()
    timestamp = result.get("timestamp", []) or result.get("ts_list", [])
    if text and timestamp:
        return [
            {"start": ts[0], "end": ts[1], "text": w}
            for ts, w in zip(timestamp, text.split())
            if len(ts) >= 2
        ]

    if text:
        return [{"start": 0.0, "end": 0.0, "text": text}]

    return []


def transcribe(audio_path: str, output_path: str, language: str = None,
               device: str = "cpu", vad_config: dict = None):
    from funasr import AutoModel

    print(f"model: Fun-ASR-Nano", file=sys.stderr)
    print(f"device: {device}", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    kwargs = {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
        "device": device,
    }
    if vad_config:
        kwargs["vad_kwargs"] = vad_config
    if language:
        kwargs["language"] = language

    model = AutoModel(**kwargs)
    result = model.generate(input=audio_path)

    segments = extract_segments(result)
    if not segments:
        print("error: no transcription segments extracted", file=sys.stderr)
        print(f"raw result: {json.dumps(result, default=str, ensure_ascii=False)[:500]}", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")

    print(f"done! {len(segments)} segments -> {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio to SRT using FunASR")
    parser.add_argument("audio", help="audio file path (WAV 16kHz mono)")
    parser.add_argument("--output", "-o", help="output SRT path")
    parser.add_argument("--language", "-l", help="language code (default: auto-detect)")
    parser.add_argument("--device", "-d", default="cpu", choices=["cpu", "mps", "cuda"],
                        help="compute device (default: cpu)")
    parser.add_argument("--vad-config", type=json.loads, default=None,
                        help='VAD kwargs JSON, e.g. \'{"max_single_segment_time": 60000}\'')

    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: file not found {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    transcribe(
        str(audio_path),
        output_path,
        language=args.language,
        device=args.device,
        vad_config=args.vad_config,
    )
=======
SENTENCE_END = ".?!。？！…"
SOFT_BREAK = ",;:，；：、—"


class Segment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


def _is_sentence_end(char: str) -> bool:
    return char in SENTENCE_END


def _is_soft_break(char: str) -> bool:
    return char in SOFT_BREAK


def _approx_timestamp(char_index: int, total_chars: int, seg_start: float, seg_end: float) -> float:
    if total_chars <= 1:
        return seg_start
    ratio = char_index / total_chars
    return seg_start + ratio * (seg_end - seg_start)


def _split_by_punctuation(seg: Segment) -> list[Segment]:
    text = seg.text.strip()
    if not text:
        return []
    total = len(text)
    if total <= 1:
        return [Segment(seg.start, seg.end, text)]

    result = []
    last_cut = 0
    for i, ch in enumerate(text):
        if _is_sentence_end(ch):
            piece = text[last_cut:i + 1].strip()
            if piece:
                start_ts = _approx_timestamp(last_cut, total, seg.start, seg.end)
                end_ts = _approx_timestamp(i + 1, total, seg.start, seg.end)
                result.append(Segment(start_ts, end_ts, piece))
            last_cut = i + 1

    remaining = text[last_cut:].strip()
    if remaining:
        start_ts = _approx_timestamp(last_cut, total, seg.start, seg.end)
        result.append(Segment(start_ts, seg.end, remaining))

    if not result:
        result.append(Segment(seg.start, seg.end, text))

    return result


def _split_long_segment(seg: Segment, max_duration: float, max_chars: int) -> list[Segment]:
    text = seg.text.strip()
    if not text:
        return []
    duration = seg.end - seg.start
    total = len(text)

    if duration <= max_duration and total <= max_chars:
        return [seg]

    result = []
    last_cut = 0
    total_chars = len(text)

    for i, ch in enumerate(text):
        piece_len = i - last_cut
        if piece_len < max_chars // 2:
            continue
        if _is_soft_break(ch):
            piece = text[last_cut:i + 1].strip()
            if piece:
                start_ts = _approx_timestamp(last_cut, total, seg.start, seg.end)
                end_ts = _approx_timestamp(i + 1, total_chars, seg.start, seg.end)
                result.append(Segment(start_ts, end_ts, piece))
            last_cut = i + 1

    remaining = text[last_cut:].strip()
    if remaining:
        start_ts = _approx_timestamp(last_cut, total_chars, seg.start, seg.end)
        result.append(Segment(start_ts, seg.end, remaining))

    if not result:
        result.append(Segment(seg.start, seg.end, text))

    return result


def _merge_short_fragments(segments: list[Segment], min_duration: float, min_chars: int) -> list[Segment]:
    if not segments:
        return []
    result = [segments[0]]
    for seg in segments[1:]:
        prev = result[-1]
        prev_dur = prev.end - prev.start
        seg_dur = seg.end - seg.start
        if (prev_dur < min_duration or len(prev.text) < min_chars) and seg.start - prev.end < 1.0:
            merged_text = prev.text.rstrip(",;:，；：、—") + "，" + seg.text
            result[-1] = Segment(prev.start, seg.end, merged_text)
        elif seg_dur < min_duration and len(seg.text) < min_chars:
            merged_text = prev.text.rstrip(",;:，；：、—") + "，" + seg.text
            result[-1] = Segment(prev.start, seg.end, merged_text)
        else:
            result.append(seg)
    return result


def _fix_time_overlaps(segments: list[Segment]) -> list[Segment]:
    if not segments:
        return []
    result = [segments[0]]
    for seg in segments[1:]:
        prev = result[-1]
        if seg.start < prev.end:
            seg.start = prev.end
        if seg.end <= seg.start:
            seg.end = seg.start + 0.3
        result.append(seg)
    return result


def _postprocess_segments(
    segments: list[Segment],
    max_duration: float = 8.0,
    max_chars: int = 80,
    min_duration: float = 1.5,
    min_chars: int = 5,
) -> list[Segment]:
    if not segments:
        return []

    expanded = []
    for seg in segments:
        sub = _split_by_punctuation(seg)
        for s in sub:
            expanded.extend(_split_long_segment(s, max_duration, max_chars))

    merged = _merge_short_fragments(expanded, min_duration, min_chars)
    fixed = _fix_time_overlaps(merged)
    return fixed


def _parse_timestamp_funasr(ts_val) -> float:
    if isinstance(ts_val, (int, float)):
        return float(ts_val)
    if isinstance(ts_val, (list, tuple)) and len(ts_val) >= 2:
        return float(ts_val[0])
    return 0.0


def _parse_sentence_list(sentences: list, seg_start: float) -> list[Segment]:
    segments = []
    for sent in sentences:
        if isinstance(sent, dict):
            ts = sent.get("timestamp") or sent.get("ts") or sent.get("time")
            text = (sent.get("text") or sent.get("value") or sent.get("subtitle") or "").strip()
            if not text:
                continue
            if isinstance(ts, (list, tuple)) and len(ts) >= 2:
                s = _parse_timestamp_funasr(ts)
                e = _parse_timestamp_funasr(ts[1]) if isinstance(ts[1], (int, float)) else seg_start + 2.0
            else:
                s = seg_start
                e = s + 2.0
            segments.append(Segment(s, e, text))
        elif isinstance(sent, str):
            sent = sent.strip()
            if sent:
                segments.append(Segment(seg_start, seg_start + 2.0, sent))
                seg_start += 2.0
    return segments


def _try_extract_sentence_info(result, seg_id: int, default_start: float, default_dur: float) -> list[Segment] | None:
    if isinstance(result, dict):
        sent_info = result.get("sentence_info")
        if isinstance(sent_info, list) and sent_info:
            segments = _parse_sentence_list(sent_info, default_start)
            if segments:
                return segments
    return None


def _try_extract_sentence_info_flat(result, default_start: float, default_dur: float) -> list[Segment] | None:
    if isinstance(result, dict):
        info = result.get("sentence_info")
        if isinstance(info, list):
            return _parse_sentence_list(info, default_start)
    return None


def _try_extract_segments_direct(result) -> list[Segment] | None:
    if isinstance(result, list) and result:
        if isinstance(result[0], dict):
            segments = []
            for item in result:
                text = (item.get("text") or item.get("value") or "").strip()
                if not text:
                    continue
                ts = item.get("timestamp") or item.get("ts")
                if isinstance(ts, (list, tuple)) and len(ts) >= 2:
                    s = _parse_timestamp_funasr(ts)
                    e = _parse_timestamp_funasr(ts[1]) if isinstance(ts[1], (int, float)) else s + 2.0
                    segments.append(Segment(s, e, text))
            if segments:
                return segments
        elif isinstance(result[0], str):
            segments = []
            for i, text in enumerate(result):
                text = text.strip()
                if text:
                    segments.append(Segment(i * 2.0, i * 2.0 + 2.0, text))
            if segments:
                return segments
    return None


def _try_extract_text_only(result) -> list[Segment] | None:
    if isinstance(result, str):
        text = result.strip()
        if text:
            return [Segment(0.0, 2.0, text)]
    if isinstance(result, dict) and "text" in result:
        text = result["text"].strip()
        if text:
            return [Segment(0.0, 2.0, text)]
    return None


def parse_funasr_result(result, audio_duration: float | None = None) -> list[Segment]:
    default_dur = audio_duration if audio_duration and audio_duration > 0 else 30.0
    default_start = 0.0

    if result is None:
        raise ValueError("FunASR returned None")

    handlers = [
        lambda r: _try_extract_sentence_info(r, 0, 0.0, default_dur),
        lambda r: _try_extract_sentence_info_flat(r, 0.0, default_dur),
        lambda r: _try_extract_segments_direct(r),
        lambda r: _try_extract_text_only(r),
    ]

    for handler in handlers:
        segments = handler(result)
        if segments:
            return segments

    raise ValueError(f"unable to parse FunASR result: {type(result)}")


def transcribe(audio_path: str, model_name: str = "FunAudioLLM/Fun-ASR-Nano-2512",
               device: str = "cpu") -> list[Segment]:
    print(f"loading model: {model_name}", file=sys.stderr)
    print(f"device: {device}", file=sys.stderr)

    try:
        from funasr import AutoModel
    except ImportError:
        print("error: funasr not installed, run: pip install funasr", file=sys.stderr)
        sys.exit(1)

    model = AutoModel(
        model=model_name,
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device=device,
    )

    print(f"transcribing: {audio_path}", file=sys.stderr)
    result = model.generate(input=audio_path)

    audio_duration = None
    try:
        import soundfile as sf
        audio_data, sr = sf.read(audio_path)
        audio_duration = len(audio_data) / sr
    except Exception:
        pass

    segments = parse_funasr_result(result, audio_duration)
    print(f"raw segments: {len(segments)}", file=sys.stderr)
    return segments


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio/video to SRT using FunASR")
    parser.add_argument("input", help="input audio or video file")
    parser.add_argument("--output", "-o", help="output SRT path (default: <input>.srt)")
    parser.add_argument("--config", "-c", help="path to config.json")
    parser.add_argument("--model", default="FunAudioLLM/Fun-ASR-Nano-2512",
                        help="FunASR model name (default: FunAudioLLM/Fun-ASR-Nano-2512)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"],
                        help="device (default: cpu)")
    parser.add_argument("--max-line-duration", type=float, default=8.0,
                        help="max seconds per subtitle line (default: 8.0)")
    parser.add_argument("--max-line-chars", type=int, default=80,
                        help="max characters per subtitle line (default: 80)")
    parser.add_argument("--min-line-duration", type=float, default=1.5,
                        help="min seconds before merging short fragments (default: 1.5)")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: file not found {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(input_path.with_suffix(".srt"))

    audio_extensions = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
    is_audio = input_path.suffix.lower() in audio_extensions

    if is_audio:
        work_path = str(input_path)
    else:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav = tmp.name
        extract_audio(str(input_path), tmp_wav)
        work_path = tmp_wav

    try:
        segments = transcribe(work_path, model_name=args.model, device=args.device)

        cleaned = _postprocess_segments(
            segments,
            max_duration=args.max_line_duration,
            max_chars=args.max_line_chars,
            min_duration=args.min_line_duration,
        )

        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(cleaned, 1):
                f.write(f"{i}\n")
                f.write(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}\n")
                f.write(f"{seg.text}\n\n")

        print(f"done! {len(cleaned)} segments -> {output_path}", file=sys.stderr)
    finally:
        if not is_audio and 'tmp_wav' in locals():
            os.unlink(tmp_wav)
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)


if __name__ == "__main__":
    main()
