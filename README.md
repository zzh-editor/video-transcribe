# video-transcribe

> **OpenCode / Claude Code** 技能：将本地视频/音频文件转录为高精度 SRT 字幕。

输入视频或音频文件 → 提取音频 → FunASR 转写（Fun-ASR-Nano + VAD + 标点）→ 可选润色 → 可选翻译 → 输出 SRT 字幕。

无需烧录、无需下载、不依赖任何 API，全程本地运行。跨平台统一引擎（macOS / Windows / Linux 皆同）。

---

## 架构变化 (v2.0)

v2.0 从 Whisper（mlx-whisper / faster-whisper）迁移到 **FunASR**（阿里达摩院开源语音识别框架）：

| 维度 | 旧版 (v1.x) | 新版 (v2.0) |
|------|-------------|-------------|
| 引擎 | Whisper（两套：mlx + faster） | FunASR AutoModel（一套跨平台） |
| 模型 | large-v3-turbo (1.5GB) | Fun-ASR-Nano + VAD + 标点 (~3GB) |
| 语言 | 99 种语言 | 31 种语言/方言（含 7 种中文方言） |
| 速度 | CPU 不可用 (macOS GPU / Windows CPU) | CPU 3.6x RT + GPU 加速 |
| 精度 | 通用 | 中文/方言显著提升 |
| 切分 | 词级时间戳 → 算法合并 | VAD 自动按语音停顿切分句子 |

---

## 安装

```bash
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash install.sh
```

### 依赖

- **ffmpeg** — 音频提取
- **Python 3.8+**
- **funasr** — `pip install funasr`（自动安装 PyTorch 等）
- 模型首次运行自动下载（约 3GB）

### npx

```bash
npx github:zzh-editor/video-transcribe
```

---

## 使用

装好后在 OpenCode 或 Claude Code 中说：

```
转录视频 /path/to/video.mp4
转录音频 /path/to/audio.mp3
```

或英文：

```
transcribe video /path/to/video.mp4
transcribe audio /path/to/audio.mp3
generate subtitles /path/to/video.mkv
output subtitles /path/to/audio.wav
```

技能会自动执行：

1. **提取音频** — 用 ffmpeg 提取 16kHz 单声道 WAV
2. **转写** — FunASR AutoModel（Fun-ASR-Nano + VAD + 标点）
3. **[可选] 润色** — 如已安装 `srt-enhancer`，询问是否调用
4. **[可选] 翻译** — 非中文询问翻译模式（纯中文 / 中上原下 / 原上中下）
5. **输出** — 最终 SRT 到 `output_dir/data/`

### 指定语种

```
转录视频 /path/to/video.mp4 英文
转录音频 /path/to/audio.mp3 日语
```

---

## 输出

只生成 `.srt` 字幕文件，不生成视频、不烧录、不加水印。

---

## 配置

编辑 `config.json` 设置：

```json
{
  "output_dir": "~/Documents/video-transcribe-output",
  "model": "FunAudioLLM/Fun-ASR-Nano-2512",
  "vad": {
    "max_single_segment_time": 60000
  }
}
```

- `output_dir` — 转录产物输出目录
- `model` — FunASR 模型名称（可换 SenseVoiceSmall / Paraformer 等）
- `vad` — VAD 参数配置

---

## 工作原理

核心转写脚本 `scripts/transcribe.py`：

- 使用 `funasr.AutoModel` 统一接口
- Fun-ASR-Nano 模型（SenseVoice 编码器 + Qwen3-0.6B 解码器）
- fsmn-vad 按语音停顿自动切分句子
- ct-punc 恢复标点符号
- 多级输出解析：`sentence_info` → `timestamp+text` → `text only`
- 31 种语言/方言自动检测

---

## License

MIT
