# Video Transcribe · 视频转录字幕技能

适用于 AI 编程助手（OpenCode、Claude Code、Cursor 等）的视频/音频转录 skill。

输入本地视频或音频文件 → ffmpeg 提取音频 → Whisper 转写（macOS 用 MLX 加速，其他用 faster-whisper）→ 可选调用 Srt-Enhancer 润色 → 可选翻译 → 输出高精度 SRT 字幕文件。

全程本地运行，无需任何 API，不烧录字幕，不输出视频。

## Features

- **Whisper 词级时间戳转写** — 基于 large-v3-turbo 模型，macOS arm64 用 mlx-whisper（Apple GPU 加速），其他平台用 faster-whisper
- **三层智能断句** — 强边界拆分（句末标点/自然停顿/应答词保护）→ 超长块软切（逗号/连词/最大间隙/等分兜底）→ 短碎片合并，辅以后处理（去重/去无效/修正重叠）
- **应答词保护** — 好/对/OK/yes/no 等 40+ 个单字/单词应答独立成块，不与其他合并
- **语言自适应** — 中文 300ms / 英文 500ms 停顿阈值自动切换
- **全平台统一** — macOS Apple Silicon 自动 MLX 加速；Windows / Linux / Intel Mac 自动 fallback 到 faster-whisper CPU/CUDA
- **可选润色** — 集成 [Srt-Enhancer](https://github.com/zzh-editor/Srt-Enhancer) 去口癖、ASR 纠错、的/得/地修正、中西文混排规范化
- **可选翻译** — 支持纯中文 / 中上原下 / 原上中下，含游戏美术/3D/绑定方向特化翻译规则
- **模型自动管理** — 首次运行自动创建 Python venv 并下载 Whisper 模型（约 1.6GB），缓存到技能目录

## 安装

### 方式一：npx skills（推荐）

```bash
npx skills add zzh-editor/video-transcribe
```

> `.skillignore` 已配置，`README.md` 不会被下载到技能目录中。

自动安装到当前 AI 编程助手。

### 方式二：手动克隆

```bash
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash install.sh
```

### 依赖

- **ffmpeg** — 音频提取（macOS: `brew install ffmpeg`，Ubuntu: `sudo apt install ffmpeg`，Windows: `winget install ffmpeg`）
- **Python 3.8+**
- **mlx-whisper**（macOS arm64）：`python3 -m venv venv && venv/bin/pip install mlx-whisper`
- **faster-whisper**（其他平台）：`python3 -m venv venv && venv/bin/pip install faster-whisper`
- **模型**：whisper-large-v3-turbo（约 1.6GB），首次运行自动下载

## 使用

### 触发词

```
转录视频 /path/to/video.mp4
转录音频 /path/to/audio.mp3
transcribe video /path/to/video.mkv
transcribe audio /path/to/audio.wav
生成字幕 /path/to/video.mov
```

### 工作流

1. 提供本地视频或音频文件路径（不接受 URL）
2. ffmpeg 提取 16kHz 单声道 WAV
3. Whisper 词级时间戳转写 → 三层智能断句 → 原始 SRT
4. **[可选]** 如已安装 Srt-Enhancer，询问是否调用润色
5. **[可选]** 非中文内容询问翻译模式（纯中文 / 中上原下 / 原上中下）
6. 输出最终 SRT 到输入文件所在目录，命名为 `<输入文件名>_<语言>.srt`

### 示例

**输入视频：**
```
/path/to/tutorial.mp4
```

**输出 SRT：**
```srt
1
00:00:01,200 --> 00:00:04,500
今天我们来看一下 Whisper 转写的效果

2
00:00:04,800 --> 00:00:08,200
在 macOS 上它会自动使用 MLX 加速
```

## 目录结构

```
video-transcribe/
├── .skillignore                     # 排除 README.md 不被 npx skills add 下载
├── SKILL.md                         # 技能定义（核心文件）
├── config.example.json              # 配置模板
├── install.sh                       # 安装脚本
├── scripts/
│   └── transcribe.py                # 转写脚本（Whisper 词级时间戳引擎）
└── docs/
    └── 游戏留学SRT翻译规则.md        # 游戏/3D/绑定方向翻译补充规则
```

## 配置

编辑 `config.json`：

```json
{
  "model": {
    "engine": "mlx",
    "name": "mlx-community/whisper-large-v3-turbo",
    "max_line_length": 40,
    "max_line_ms": 6000,
    "pause_ms_zh": 300,
    "pause_ms_en": 500
  },
  "tmp_dir": ".opencode/skills/video-transcribe/tmp"
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `model.engine` | `mlx` | 引擎（mlx / faster-whisper） |
| `model.name` | `mlx-community/whisper-large-v3-turbo` | Whisper 模型名称 |
| `model.max_line_length` | `40` | 每行最大字符数 |
| `model.max_line_ms` | `6000` | 每段最大时长（毫秒） |
| `model.pause_ms_zh` | `300` | 中文停顿阈值（毫秒） |
| `model.pause_ms_en` | `500` | 英文停顿阈值（毫秒） |
| `tmp_dir` | `.opencode/skills/video-transcribe/tmp` | 临时文件目录 |

## License

MIT
