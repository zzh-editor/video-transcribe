---
name: video-transcribe
<<<<<<< HEAD
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → FunASR 转写（Fun-ASR-Nano + VAD + 标点） → 可选调用 srt-enhancer 润色 → 可选翻译（纯中文/中上原下/原上中下）→ 输出高精度 SRT 字幕文件。当用户说「转录视频」「转录音频」「生成字幕」「输出字幕」「transcribe video」「transcribe audio」「generate subtitles」「output subtitles」时使用。"
=======
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → FunASR 转写（Fun-ASR-Nano + VAD 句子级时间戳 + 文本后处理断句）→ 可选调用 srt-enhancer 润色 → 可选翻译（纯中文/中上原下/原上中下）→ 输出高精度 SRT 字幕文件。当用户说「转录视频」「转录音频」「生成字幕」「输出字幕」「transcribe video」「transcribe audio」「generate subtitles」「output subtitles」时使用。"
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# video-transcribe

## Pipeline

```
本地视频/音频文件
      │
      ▼
① 提取音频 (ffmpeg → 16kHz WAV)
      │
      ▼
<<<<<<< HEAD
② FunASR 转写 → 原始 SRT（sentence-level 时间戳）
=======
② FunASR 转写 → VAD 句子级时间戳 → 文本后处理断句
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)
      │
      ▼
③ [可选] 调用 srt-enhancer 润色
      │
      ▼
④ [可选] 翻译（纯中文 / 中上原下 / 原上中下）
      │
      ▼
⑤ 输出：最终 SRT
```

## 使用方式

用户提供一个本地视频或音频文件路径。此技能不处理 URL 下载，只处理本地文件。

## 失败模式

| 触发条件 | 一线修复 | 仍失败兜底 |
|---------|---------|-----------|
| `ffmpeg` 未安装 | macOS → `brew install ffmpeg`，Ubuntu → `sudo apt install ffmpeg`，Windows → `winget install ffmpeg` | 告知用户手动安装后重试 |
| ffmpeg 提取失败 | 检查输入文件是否存在、格式是否支持 | 建议用户先用其他工具转码为 WAV |
| `from funasr import AutoModel` 失败 | `pip install funasr` | 检查 Python ≥ 3.8 及 PyTorch |
| FunASR 模型下载失败 | 检查网络、重试 | 告知用户需网络连接以下载模型 |
| 转写内存不足 | 关闭其他应用后重试 | 确保 8GB+ RAM |
| `output_dir` 不可写 | 手动 `mkdir -p` 配置路径 | 提示用户修改 `config.json` |
| srt-enhancer 不存在或调用失败 | 确认 `~/.config/opencode/skills/srt-enhancer/` 完整 | 跳过润色，直接以 raw.srt 为基线 |

## 准备工作

先读取 `config.json` 获取 `output_dir` 和 `model`/`vad` 配置。如缺失或不可写，提示用户配置。

## Step 1: 提取音频

输入可能是 MP4/MOV/MKV/MP3/WAV/M4A 等。如果已经是 16kHz 单声道 WAV，可跳过此步。

创建临时目录：
```bash
<<<<<<< HEAD
mkdir -p "<output_dir>/tmp"
```

提取为 16kHz 单声道 WAV（FunASR 标准输入格式）：
```bash
ffmpeg -i "<input>" -vn -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

## Step 2: FunASR 转写

使用 `scripts/transcribe.py`，通过 FunASR AutoModel 调用 Fun-ASR-Nano：

```bash
python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" --output "<output_dir>/tmp/raw.srt"
```

如需指定语种或设备：
```bash
python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" --output "<output_dir>/tmp/raw.srt" --language zh --device cpu
```

脚本特性：
- Fun-ASR-Nano 模型（31 种语言/方言）
- fsmn-vad 自动根据语音停顿切分句子
- ct-punc 恢复标点符号
- 首次运行自动下载模型（约 3GB）

## 🔴 CHECKPOINT: 润色确认

**暂停。** 检查 `~/.config/opencode/skills/srt-enhancer/` 是否存在。将结果告知用户，询问是否需要调用润色。

- 选「是」→ 进入 Step 3
- 选「否」→ 以 `raw.srt` 为基线，跳到 Step 4
=======
ffmpeg -i "<input>" -vn -acodec pcm_s16le -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

确保 `output_dir/tmp/` 存在。

## Step 2: FunASR 转写

转写使用 `scripts/transcribe.py`，基于 FunASR AutoModel + Fun-ASR-Nano 模型。

执行命令：

```bash
python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" --output "<output_dir>/tmp/raw.srt"
```

如需要指定设备（macOS Apple Silicon 可用 MPS 加速）：
```bash
python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" --output "<output_dir>/tmp/raw.srt" --device mps
```

脚本特性：
- Fun-ASR-Nano 模型（31 语言/方言，基于 SenseVoice 编码器 + Qwen3-0.6B 解码器）
- fsmn-vad 语音活动检测按句子切分
- ct-punc 标点恢复
- 文本后处理断句：标点二次分裂 → 超长段拆分 → 短碎片合并 → 时间重叠修正
- 默认输出参数：最大行 8 秒 / 80 字符，最小行 1.5 秒/5 字符
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)

## Step 3: [可选] 润色

srt-enhancer 位于 `~/.config/opencode/skills/srt-enhancer/`，提供去口癖、ASR 纠错、的/得/地修正、标点清理、中英文混排空格规范化。

调用方式：用 Skill 工具加载 srt-enhancer 技能，将 `raw.srt` 作为输入。

## 🔴 CHECKPOINT: 翻译确认

**暂停。** 从 FunASR 输出中获取检测语种。告知用户语种：

- 语种为 `zh` → 跳过翻译，进入 Step 5
- 非中文 → 展示翻译模式选项，等待用户选择

## Step 4: [可选] 翻译

<<<<<<< HEAD
翻译规则和 SRT 格式与之前保持一致。
=======
FunASR 无内置语种检测，默认输出文本由模型自动识别。询问用户是否需要翻译：

```
请选择翻译模式：
  1. 纯中文字幕（仅显示中文翻译）
  2. 中英双语（中文在上，原文在下）
  3. 英中双语（原文在上，中文在下）
  4. 不需要翻译，保持原文
```

如用户选翻译，对 SRT 进行逐条翻译：
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)

### 翻译规则

1. 每行 ≤18 个中文字符，按语义断点拆分
2. 去标点
3. 中英文间加空格
4. 专有名词保留英文
5. 自然口语化
6. 严格保留时间戳

### 翻译模式

<<<<<<< HEAD
1. 纯中文字幕
2. 中上原下（中文在上，原文在下）
3. 原上中下（原文在上，中文在下）
4. 不需要翻译
=======
**纯中文模式**：每条 SRT 只有中文翻译
```
1
00:00:03,660 --> 00:00:06,360
这是中文翻译
```

**中上原下模式**：中文在上，原文在下
```
1
00:00:03,660 --> 00:00:06,360
这是中文翻译
This is the original English
```

**原上中下模式**：原文在上，中文在下
```
1
00:00:03,660 --> 00:00:06,360
This is the original English
这是中文翻译
```
>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)

## Step 5: 输出

```bash
mkdir -p "<output_dir>/data"
cp "<output_dir>/tmp/final.srt" "<output_dir>/data/<文件名>.srt"
```

文件名规则：`<输入文件名>_<语言>.srt`

告知用户文件路径。

## 禁止做的事

| # | 反模式 | 原因 |
|---|-------|------|
| 1 | 接受 URL 或网络下载输入 | 此技能只处理本地文件 |
| 2 | 输出烧录字幕（硬字幕）或视频文件 | 此技能只输出独立 SRT 字幕文件 |
| 3 | 输出 Markdown / TXT / JSON 格式 | 此技能仅输出 SRT 格式 |
| 4 | 合并润色和翻译到一步 | 执行步骤之间必须经过检查点确认 |
| 5 | 修改原始时间戳或合并 SRT 条目 | 翻译时严格对齐原文时间戳 |

## 依赖

### 必需
<<<<<<< HEAD
- **ffmpeg**：音频提取
- **Python 3.8+**
- **funasr**：`pip install funasr`（自动安装 PyTorch 等依赖）
- **模型**：首次运行自动从 HuggingFace 下载 Fun-ASR-Nano + fsmn-vad + ct-punc，约 3GB

### 可选
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

=======
- **ffmpeg**：音频提取（`pcm_s16le` 16kHz WAV）
- **Python 3.8+**
- **funasr**：核心转写引擎（`pip install funasr`）
  - 模型首次运行自动从 ModelScope 下载
  - Fun-ASR-Nano ≈ 3.1GB
  - fsmn-vad（VAD 模型，<1MB）
  - ct-punc（标点恢复模型，≈ 290MB）
- **torch**：PyTorch 后端（`pip install torch`）

### 可选
- **soundfile**：音频时长检测（`pip install soundfile`）
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

### 平台注意

FunASR 跨平台统一：macOS / Windows / Linux 使用同一套代码。macOS Apple Silicon 可加 `--device mps` 用 Metal GPU 加速。

### 模型说明

| 参数 | 值 |
|------|-----|
| ASR 模型 | FunAudioLLM/Fun-ASR-Nano-2512（31 语言含中文方言，LLM 驱动） |
| VAD 模型 | fsmn-vad（自动语音活动检测，按语音段切分） |
| 标点模型 | ct-punc（标点恢复后处理） |
| CPU 速度 | 3.6x 实时 |
| 内存推荐 | 8GB+ |

>>>>>>> b6fa199 (refactor: migrate Whisper to FunASR with enhanced sentence segmentation)
## 临时文件

- `output_dir/tmp/` — 中间产物（提取的 WAV、原始 SRT、润色后 SRT）
- `output_dir/data/` — 最终输出 SRT

AI 处理完成后可清理 `tmp/` 目录。
