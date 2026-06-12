---
name: video-transcribe
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → Whisper 转写（macOS 用 MLX 加速，其他用 faster-whisper） → 可选调用 srt-enhancer 润色 → 可选翻译（纯中文/中上原下/原上中下）→ 输出高精度 SRT 字幕文件"
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
② Whisper 转写 → 词级时间戳断句 → 原始 SRT
      │   macOS arm64 → mlx-whisper (Apple GPU)
      │   其他平台   → faster-whisper (CPU/CUDA)
      ▼
③ [可选] 调用 srt-enhancer 润色
      │
      ▼
④ [可选] 翻译（纯中文 / 中上原下 / 原上中下）
      │
      ▼
⑤ 输出：最终 SRT（与输入文件同目录）
```

## 使用方式

用户提供一个本地视频或音频文件路径。此技能不处理 URL 下载，只处理本地文件。

输出 SRT 文件保存在**输入文件所在目录**，命名为 `<输入文件名>_<语言>.srt`。

## 环境与模型

技能完全自包含，所有依赖和模型缓存位于技能目录下：

```
.opencode/skills/video-transcribe/
├── venv/          ← Python 虚拟环境（mlx-whisper 等）
└── models/        ← Whisper 模型缓存（HF_HOME）
```

首次运行自动创建 venv 并下载模型（约 1.6GB）。

## 断句算法（三层优先级）

转写使用 Whisper 词级时间戳，经三层优先级断句 + 后处理：

```
Layer 1 — 强边界（立即拆分）
  ├── 句末标点（。？！.?!…）
  ├── 应答词保护（好/对/OK/yes/no 等，两端有显著停顿则独立成块）
  └── 自然停顿 ≥ 阈值（中文 300ms，英文 500ms）

Layer 2 — 超长块软切（>max_chars 或 >max_line_ms）
  ├── a. 逗号/连词处断
  ├── b. 最大词间隙处断
  └── c. 等分（兜底）

Layer 3 — 智能合并短碎片
  ├── 短块（<0.4s 且 <3 词）合并到前块
  └── 应答词不合并（保持独立）

后处理
  ├── 丢弃无效时间戳
  ├── 合并连续重复文本（Whisper 重复伪影）
  └── 修正相邻块时间重叠
```

应答词保护列表（独立成块，不与其他合并）：
好/对/嗯/是/不/行/哦/啊/OK/yes/no/yeah/sure/right 等 40+ 个

## 失败模式

| 触发条件 | 一线修复 | 仍失败兜底 |
|---------|---------|-----------|
| `ffmpeg` 未安装 | macOS → `brew install ffmpeg`，Ubuntu → `sudo apt install ffmpeg`，Windows → `winget install ffmpeg` | 告知用户手动安装后重试 |
| ffmpeg 提取失败 | 检查输入文件是否存在、格式是否支持 | 建议用户先用其他工具转码为 WAV |
| `import mlx_whisper` 失败（macOS） | `venv/bin/pip install mlx-whisper` | 检查 Python ≥ 3.8 及 Apple Silicon 芯片 |
| `from faster_whisper import WhisperModel` 失败（其他平台） | `venv/bin/pip install faster-whisper` | 检查 Python ≥ 3.8 |
| Whisper 模型下载失败 | 检查网络、重试 | 确保网络可访问 HuggingFace |
| 转写内存不足 | 关闭其他应用后重试 | 确保 8GB+ RAM |
| srt-enhancer 不存在或调用失败 | 确认 `~/.config/opencode/skills/srt-enhancer/` 完整 | 跳过润色，直接以 raw.srt 为基线 |

## 准备工作

1. 读取 `config.json` 获取配置
2. 确认技能目录下 `venv/` 和 `models/` 存在：
   - 如 `venv/` 缺失，运行 `python3 -m venv venv && venv/bin/pip install mlx-whisper socksio`
   - 如 `models/` 缺失，运行 `mkdir -p models`

## Step 1: 提取音频

创建临时目录：
```bash
mkdir -p "<output_dir>/tmp"
```

提取为 16kHz 单声道 WAV（Whisper 标准输入格式）：
```bash
ffmpeg -i "<input>" -vn -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

## Step 2: Whisper 转写

使用 `scripts/transcribe.py`，平台自适应引擎：

```bash
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --max-line-length 40 \
  --max-line-ms 6000
```

脚本特性：
- macOS (arm64) → 自动使用 mlx-whisper（Apple GPU MLX 加速）
- 其他平台 → 自动使用 faster-whisper（CPU 或 CUDA）
- Whisper large-v3-turbo 模型（高质量转写）
- **词级时间戳三层断句**：利用 Whisper 逐词时间戳精确断句
- **应答词保护**：好/对/OK/yes/no 等单字/单词应答保持独立字幕块
- 语言自适应停顿阈值（中文 300ms，英文 500ms）
- 首次运行自动下载模型（约 1.6GB）

## 🔴 CHECKPOINT: 润色确认

**暂停。** 检查 `~/.config/opencode/skills/srt-enhancer/` 是否存在。将结果告知用户，询问是否需要调用润色。

- 选「是」→ 进入 Step 3
- 选「否」→ 以 `raw.srt` 为基线，跳到 Step 4

## Step 3: [可选] 润色

srt-enhancer 位于 `~/.config/opencode/skills/srt-enhancer/`，提供去口癖、ASR 纠错、的/得/地修正、标点清理、中英文混排空格规范化。

调用方式：用 Skill 工具加载 srt-enhancer 技能，将 `raw.srt` 作为输入。

## 🔴 CHECKPOINT: 翻译确认

**暂停。** 从 Whisper 输出中获取检测语种。告知用户语种：

- 语种为 `zh` → 跳过翻译，进入 Step 5
- 非中文 → 展示翻译模式选项，等待用户选择

## Step 4: [可选] 翻译

翻译时遵循以下规则，优先级从高到低：

### 基线规则（始终适用）

1. 每行 ≤18 个中文字符，按语义断点拆分
2. 去标点
3. 中英文间加空格
4. 专有名词保留英文
5. 自然口语化
6. 严格保留时间戳

### 行业补充指南

游戏美术 / 3D 制作 / 绑定方向的非中文内容翻译，参见技能目录下的 `docs/游戏留学SRT翻译规则.md`。该文件作为基线规则的行业特化补充，包含术语处理、ASR 术语误识别修正等详细规则。当两者冲突时，以基线规则为准。

### 翻译模式

1. 纯中文字幕
2. 中上原下（中文在上，原文在下）
3. 原上中下（原文在上，中文在下）
4. 不需要翻译

## Step 5: 输出

复制到输入文件同目录：

```bash
cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<语言>.srt"
```

文件名规则：`<输入文件名>_<语言>.srt`

同时生成 diff 对比文件（如有润色）：

```bash
cp "<output_dir>/tmp/diff.md" "<输入文件目录>/<输入文件名>_<语言>.diff.md"
```

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
- **ffmpeg**：音频提取
- **Python 3.8+**
- **mlx-whisper**（macOS arm64）：`venv/bin/pip install mlx-whisper`
- **faster-whisper**（其他平台）：`venv/bin/pip install faster-whisper`
- **socksio**（如配置了 SOCKS 代理）：`venv/bin/pip install socksio`
- **模型**：首次运行自动下载 whisper-large-v3-turbo 约 1.6GB

### 可选
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

## 临时文件

- `output_dir/tmp/` — 中间产物（提取的 WAV、原始 SRT、润色后 SRT）
- `models/` — Whisper 模型缓存

AI 处理完成后可清理 `tmp/` 目录。
