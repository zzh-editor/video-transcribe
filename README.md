# Video Transcribe · 视频转录字幕技能

适用于 AI 编程助手（OpenCode、Claude Code、Cursor 等）的视频/音频转录 skill。

输入视频或音频文件 → 提取音频 → FunASR 转写（Fun-ASR-Nano + VAD + 标点）→ 可选润色 → 可选翻译 → 输出 SRT 字幕。

无需烧录、无需下载、不依赖任何 API，全程本地运行。跨平台统一引擎（macOS / Windows / Linux 皆同）。

---

## Features

- **FunASR 转写** — 基于 Fun-ASR-Nano 模型（SenseVoice 编码器 + Qwen3-0.6B 解码器），支持 31 种语言/方言
- **VAD 语音活动检测** — fsmn-vad 自动按语音段切分，获取句子级时间戳
- **智能断句** — 文本后处理：标点二次分裂 → 超长段拆分 → 短碎片合并 → 时间重叠修正
- **标点恢复** — ct-punc 自动添加标点符号
- **可选润色** — 集成 [Srt-Enhancer](https://github.com/zzh-editor/Srt-Enhancer) 去口癖、ASR 纠错、中西文混排规范化
- **可选翻译** — 支持纯中文 / 中英双语（中上原下 / 原上中下）
- **跨平台统一** — macOS / Windows / Linux 同一套代码，无需区分引擎

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

=======

### npx

```bash
npx github:zzh-editor/video-transcribe
```

### 手动

```bash
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash install.sh
```

### 依赖

```bash
pip install funasr torch
```

需系统安装 ffmpeg。macOS Apple Silicon 可用 MPS 加速（`--device mps`）。

## 使用

### 触发词

```
转录视频 /path/to/video.mp4
转录音频 /path/to/audio.mp3
transcribe video /path/to/video.mp4
generate subtitles /path/to/video.mkv
```

### 工作流

1. **提取音频** — 用 ffmpeg 提取 16kHz 单声道 WAV
2. **转写** — FunASR AutoModel（Fun-ASR-Nano + VAD + 标点）
3. **[可选] 润色** — 如已安装 `srt-enhancer`，询问是否调用
4. **[可选] 翻译** — 非中文询问翻译模式（纯中文 / 中上原下 / 原上中下）
5. **输出** — 最终 SRT 到 `output_dir/data/`

### 指定语种
=======
1. 用户提供本地视频或音频文件路径
2. 提取音频（ffmpeg → 16kHz WAV）
3. FunASR 转写 → VAD 句子级时间戳 → 文本后处理断句
4. [可选] 调用 [Srt-Enhancer](https://github.com/zzh-editor/Srt-Enhancer) 润色（需已安装）
5. [可选] 翻译（纯中文 / 中上原下 / 原上中下）
6. 输出最终 SRT 到 `output_dir/data/`

### 指定设备

macOS Apple Silicon 可用 Metal GPU 加速：

```
转录视频 /path/to/video.mp4 使用 MPS
```

## 目录结构

```
video-transcribe/
├── SKILL.md                         # 技能定义（核心文件）
├── config.example.json              # 配置模板
├── install.sh                       # 安装脚本
├── scripts/
│   └── transcribe.py                # 转写脚本（FunASR AutoModel）
```

## 配置

编辑 `config.json` 设置：
=======
编辑 `config.json`：

```json
{
  "output_dir": "~/Documents/video-transcribe-output",
  "model": "FunAudioLLM/Fun-ASR-Nano-2512",
<<<<<<< HEAD
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
=======
  "vad": true
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| output_dir | `~/Documents/video-transcribe-output` | 转录产物输出目录 |
| model | `FunAudioLLM/Fun-ASR-Nano-2512` | FunASR 模型名称 |
| vad | `true` | 是否启用 VAD 切分 |
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)

## License

MIT
