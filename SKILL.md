---
name: video-transcribe
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → faster-whisper（词级时间戳）→ 可选调用 srt-enhancer 润色 → 可选翻译（纯中文/中上原下/原上中下）→ 输出高精度 SRT 字幕文件。当用户说「转录视频」「转录音频」「生成字幕」「输出字幕」「transcribe video」「transcribe audio」「generate subtitles」「output subtitles」时使用。"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
---

# video-transcribe

## Pipeline

```
本地视频/音频文件
      │
      ▼
① 提取音频 (ffmpeg)
      │
      ▼
② Whisper 转写 → 原始 SRT（词级时间戳）
      │
      ▼
③ [可选] 调用 srt-enhancer 润色（ASR纠错+断句+去标点）
      │
      ▼
④ [可选] 翻译（纯中文 / 中上原下 / 原上中下）
      │
      ▼
⑤ 输出：最终 SRT
```

## 使用方式

用户提供一个本地视频或音频文件路径。此技能不处理 URL 下载，只处理本地文件。

## 准备工作

先读取 `config.json` 获取 `output_dir`，作为所有产物的输出目录。如缺失或不可写，提示用户配置。

## Step 1: 提取音频

输入可能是 MP4/MOV/MKV/MP3/WAV/M4A 等。如果已经是音频文件，跳过此步。

```bash
ffmpeg -i "<input>" -vn -acodec libmp3lame -q:a 2 "<output_dir>/tmp/audio.mp3"
```

确保 `output_dir/tmp/` 存在。

## Step 2: Whisper 转写

转写使用 `scripts/transcribe_srt.py`，它自动检测平台选择合适的转写引擎：

- Windows → faster-whisper（CPU，int8 量化）
- macOS Apple Silicon → 如已装 `mlx-whisper` 则用 MLX + Metal GPU，否则 faster-whisper

执行命令：

```bash
python3 scripts/transcribe_srt.py "<output_dir>/tmp/audio.mp3" --output "<output_dir>/tmp/raw.srt"
```

如用户指定语种（如英文、日文），加 `--language en` / `--language ja`：
```bash
python3 scripts/transcribe_srt.py "<output_dir>/tmp/audio.mp3" --output "<output_dir>/tmp/raw.srt" --language en
```

如用户需要快速模式（换小模型），加 `--model medium` 或 `--model small`：
```bash
python3 scripts/transcribe_srt.py "<output_dir>/tmp/audio.mp3" --output "<output_dir>/tmp/raw.srt" --model medium
```

脚本特性：
- 词级时间戳 + `merge_words_to_segments` 算法按句子+停顿切分字幕
- 句末标点 / 停顿时长 ≥500ms / 超 6s 兜底的四级切分优先级
- 后处理清洗：丢弃无效段、合并短碎片、消除时间重叠
- `--language` 支持强制语种，不传则自动检测

## Step 3: [可选] 润色

**自动检查本地是否已安装 srt-enhancer 技能。如已安装，询问用户是否需要调用润色。**

srt-enhancer 位于 `~/.config/opencode/skills/srt-enhancer/`，是一个独立技能，提供：
- 去口癖（啊、哦、嗯、呃等语气词）
- 纠正 ASR 识别错误（同音字、专有名词）
- 修正 的/得/地
- 去除标点符号
- 中西文混排空格规范化
- 置信度评分与 Diff 审核表

调用方式：用 Skill 工具加载 srt-enhancer 技能，将 `raw.srt` 作为输入。

**如用户选择不润色**，直接以 `raw.srt` 作为基础进行下一步。

## Step 4: [可选] 翻译

**先判断原文语种。**

- 如果 Whisper 检测语种为 `zh`（中文）→ **跳过翻译，直接以润色后/原始 SRT 为最终输出**。
- 如果为非中文语种 → **询问用户是否需要翻译**：

```
请选择翻译模式：
  1. 纯中文字幕（仅显示中文翻译）
  2. 中英双语（中文在上，原文在下）
  3. 英中双语（原文在上，中文在下）
  4. 不需要翻译，保持原文
```

如用户选翻译，对 SRT 进行逐条翻译：

### 翻译规则

1. **每行 ≤18 个中文字符**，太长按语义断点拆成两条（时间戳按比例分配）
2. **去标点**：翻译文本中去掉全半角标点（逗号、句号、问号等）
3. **中英文间加空格**：如「这是 GPT 模型」
4. **专有名词保留英文**：人名、地名、公司名、产品名、技术术语保留原文
5. **自然口语化**：避免翻译腔，符合当代中文表达
6. **严格保留时间戳**：不合并、不提前 start time

### 翻译三模式格式

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

双语模式每条保留两行，**时间戳与原文 SRT 一句对一句完全对齐**。

## Step 5: 输出

将最终产物拷贝到 `output_dir/data/`：

```bash
cp "<output_dir>/tmp/final.srt" "<output_dir>/data/<文件名>.srt"
```

文件名规则：`<输入文件名>_<语言>.srt`

告知用户文件路径。

## 依赖

### 必需
- **ffmpeg**：音频提取
- **faster-whisper**：核心转写引擎（`pip install faster-whisper`）
  - 模型下载方式：首次运行自动从 HuggingFace 下载，约 3GB（large-v3-turbo）
  - **large-v3-turbo**（默认，推荐，精度与速度平衡）≈ 3GB
  - **medium**（快速模式，精度略降）≈ 1.5GB
  - **small**（最快模式）≈ 500MB
- **Python 3.8+**

### 可选
- **mlx-whisper**：macOS Apple Silicon 可用 Metal GPU 加速（`pip install mlx-whisper`）
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

### Windows 注意

- `pip install faster-whisper`（无需 `--break-system-packages`）
- 默认用 CPU + int8 量化，1 小时音频约 15-30 分钟（取决于 CPU 核心数）
- 内存建议 16GB+

### macOS Apple Silicon 注意

- 如已装 mlx-whisper，脚本自动使用 MLX + Metal GPU，约 5-8 分钟完成 140 分钟音频
- 如未装 mlx-whisper，自动降级到 faster-whisper

## 临时文件

- `output_dir/tmp/` — 存放中间产物（提取的音频、原始 SRT、润色后 SRT）
- `output_dir/data/` — 存放最终输出 SRT

AI 处理完成后可清理 `tmp/` 目录。
