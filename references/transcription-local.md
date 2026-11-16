## 本地模型转录

### 提取音频

提取为 16kHz 单声道 WAV（Whisper 标准输入格式）：

```bash
ffmpeg -i "<input>" -vn -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

### 执行

使用 `scripts/transcribe.py`，平台自适应引擎：

- 英文示例使用 `--language en --max-line-length 60 --max-line-ms 6000`。
- 中文参数可按用户要求选择；未指定原语言时省略 `--language` 让模型自动识别。
- 标点优先的英文断句目前由 Groq 英文入口执行。

```bash
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --max-line-length 25 \
  --max-line-ms 6000
```

### 可选参数

```bash
# 强制启用 VAD（≥10 分钟长音频建议开启，<10 分钟自动关闭）
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" --language zh --vad

# 强制关闭 VAD
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" --language zh --no-vad

# 自定义断句停顿阈值（本地中文 300ms / 英文 500ms）
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" --language zh --pause-ms 500

# 覆盖默认 words JSON 输出位置
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" --language zh \
  --export-refined "<output_dir>/tmp/custom_words.json"
```

本地转录会保留 ASR 质量指标，并生成 `<输出srt>.words.json` 与 `<输出srt>.cleanup.json`。平台分流：macOS arm64 使用 mlx-whisper；其他平台使用 faster-whisper。首次使用时下载约 1.6GB Whisper 模型。
