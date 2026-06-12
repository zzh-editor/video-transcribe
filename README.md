# video-transcribe

> **OpenCode / Claude Code** 技能：将本地视频/音频文件转录为高精度 SRT 字幕。

输入视频或音频文件 → 提取音频 → Whisper 转写（词级时间戳，macOS 用 mlx-whisper / Windows 用 faster-whisper）→ 可选润色 → 可选翻译 → 输出 SRT 字幕。

无需烧录、无需下载、不依赖任何 API，全程本地运行。

---

## 安装

```bash
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash install.sh
```

### 平台依赖

**macOS（Apple Silicon）**
- `pip install mlx-whisper` — MLX + Metal GPU 加速
- 模型首次运行自动从 HuggingFace 下载（约 1.5GB）

**Windows / Linux**
- `pip install faster-whisper` — CPU + int8 量化
- 模型首次运行自动从 HuggingFace 下载（约 1.5GB）
- 建议 16GB+ 内存

**通用**
- **ffmpeg** — 音频提取
- **Python 3.8+

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

1. **提取音频** — 用 ffmpeg 提取 MP3
2. **转写** — Whisper 生成带词级时间戳的原始 SRT（macOS → mlx-whisper，Windows → faster-whisper）
3. **[可选] 润色** — 如已安装 `srt-enhancer` 技能，询问是否调用
4. **[可选] 翻译** — 非中文内容询问翻译模式（纯中文 / 中上原下 / 原上中下）
5. **输出** — 最终 SRT 到 `output_dir/data/`

### 快速模式（小模型）

```
转录视频 /path/to/video.mp4 快速
```

换用 medium 或 small 模型，速度更快但精度略降。

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

编辑 `config.json` 设置 `output_dir`：

```json
{
  "output_dir": "~/Documents/video-transcribe-output"
}
```

---

## 工作原理

核心转写脚本 `scripts/transcribe_srt.py`：

- **词级时间戳** — Whisper 输出每个词的起止时间
- **智能切分** — `merge_words_to_segments()` 算法按句末标点 / 停顿时长 ≥500ms / 超 6s 兜底四级优先级切分字幕
- **后处理清洗** — 丢弃无效段、合并短碎片、消除时间重叠

---

## License

MIT
