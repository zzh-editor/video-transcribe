# Video Transcribe

视频/音频转录为 SRT 字幕的工具。提取音频 → Whisper 转写（本地模型或 Groq API）→ 语义断句 → [可选润色](https://github.com/zzh-editor/Srt-Enhancer) → 可选翻译，输出高精度字幕。

引擎自适应：macOS arm64 默认 mlx-whisper（Apple GPU 加速），其他平台用 faster-whisper；可选 Groq API（远端 Whisper，首次运行选引擎并保存配置）。VAD 智能分片 + ASR 降噪，长音频稳定转录。

在支持 Agent Skills 的 CLI 中，说「转录」+ 文件路径即可自动调用。

## 快速开始

```bash
npx skills@latest install https://github.com/zzh-editor/video-transcribe
```

基础转录：

```
用户：转录这个视频 lecture.mp4
Agent：选引擎 → 提取音频 → Whisper 转写 → 语义断句 → 是否润色？→ 是否翻译？→ 输出 SRT
```

## 触发词

```
转录 / 转录音频 / 转录视频 / 转录字幕
transcribe / transcribe audio / transcribe video
generate subtitles / generate srt / convert to srt
```

## 处理流程

```
① 首次运行：选择引擎（本地模型 / Groq API），配置持久化
② 提取音频 (ffmpeg → 16kHz WAV)
③ VAD 切割 (Silero VAD 精准分片)
④ Whisper 逐片转录 (本地模型或 Groq API)
⑤ refine_segments.py 预清洗 + 评分引擎断句
⑥ cleanup_segments.py 后清洗 + 幻觉检测
⑦ raw.srt 输出
    │
    ├── [可选] srt-enhancer 润色 → 去口癖/ASR纠错/混排空格
    └── [可选] AI 翻译 (纯中文 / 中上原下 / 原上中下)
    │
    ▼
⑧ 最终 SRT 输出 (与输入文件同目录)
```

流程包含 1 个首次配置检查点（选引擎）和 2 个确认检查点（是否调用润色、是否翻译及模式选择）。

## 功能特性

| 功能 | 说明 |
|------|------|
| 引擎可选 | 本地模型（mlx-whisper / faster-whisper）或 Groq API |
| VAD 智能分片 | Silero VAD 切割静音段，过滤短爆音 |
| ASR 降噪 | logprob_threshold=-1.0 + no_speech_threshold=0.6 |
| 评分引擎断句 | 基于 word timestamps + pause/标点评分的断句算法 |
| 幻觉检测 | 重复字符循环过滤，黑名单模式 |
| 应答词保护 | 50+ 应答词独立成块，不合并 |
| 可选润色 | 调用 srt-enhancer 去口癖/纠错/空格 |
| 可选翻译 | AI 逐段翻译，支持 3 种排版模式 |
| 配置持久化 | 引擎选择、API Key 首次运行保存至 config.json |

## 文件结构

```
video-transcribe/
├── scripts/          # 转写、断句、清洗、安装脚本
├── venv/             # Python 虚拟环境（自动创建）
├── models/           # Whisper 模型缓存（约 1.6GB）
├── docs/             # 行业翻译规则
├── SKILL.md          # Agent skill 定义
└── README.md
```

## 依赖

**必需：** ffmpeg、Python 3.8+
  - 本地模型：mlx-whisper（macOS）/ faster-whisper（其他）、silero-vad-notorch、onnxruntime
  - Groq API：requests、Groq API Key

**可选：** [srt-enhancer（润色功能）](https://github.com/zzh-editor/Srt-Enhancer)

## License

[MIT](LICENSE)
