# Video Transcribe

视频/音频转录为 SRT 字幕的工具。提取音频 → Whisper 转写 → 语义断句 → [可选润色](https://github.com/zzh-editor/Srt-Enhancer) → 可选翻译，输出高精度字幕。

平台自适应：macOS arm64 用 mlx-whisper（Apple GPU 加速），其他平台用 faster-whisper。VAD 智能分片 + ASR 降噪，长音频稳定转录。

在支持 Agent Skills 的 CLI 中，说「转录」+ 文件路径即可自动调用。

## 快速开始

```bash
npx skills@latest install https://github.com/zzh-editor/video-transcribe
```

基础转录：

```
用户：转录这个视频 lecture.mp4
Agent：提取音频 → Whisper 转写 → 语义断句 → 是否润色？→ 是否翻译？→ 输出 SRT
```

## 触发词

```
转录 / 转录音频 / 转录视频 / 转录字幕
transcribe / transcribe audio / transcribe video
generate subtitles / generate srt / convert to srt
```

## 处理流程

```
① 提取音频 (ffmpeg → 16kHz WAV)
② VAD 切割 (Silero VAD 精准分片)
③ Whisper 逐片转录 (mlx-whisper / faster-whisper)
④ refine_segments.py 预清洗 + 级联语义断句
⑤ cleanup_segments.py 后清洗 + 幻觉检测
⑥ raw.srt 输出
    │
    ├── [可选] srt-enhancer 润色 → 去口癖/ASR纠错/混排空格
    └── [可选] AI 翻译 (纯中文 / 中上原下 / 原上中下)
    │
    ▼
⑦ 最终 SRT 输出 (与输入文件同目录)
```

流程包含 2 个确认检查点：是否调用润色、是否翻译及模式选择。

## 功能特性

| 功能 | 说明 |
|------|------|
| 平台自适应 | macOS arm64 → mlx-whisper；其他 → faster-whisper |
| VAD 智能分片 | Silero VAD 切割静音段，过滤短爆音 |
| ASR 降噪 | logprob_threshold=-1.0 + no_speech_threshold=0.6 |
| 语义断句 | 6 级级联切割：连词/话题/话语/时间/回应/重复检测 |
| 幻觉检测 | 重复字符循环过滤，黑名单模式 |
| 应答词保护 | 50+ 应答词独立成块，不合并 |
| 可选润色 | 调用 srt-enhancer 去口癖/纠错/空格 |
| 可选翻译 | AI 逐段翻译，支持 3 种排版模式 |

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

**必需：** ffmpeg、Python 3.8+、mlx-whisper（macOS）/ faster-whisper（其他）、silero-vad-notorch、onnxruntime

**可选：** srt-enhancer（润色功能）

## License

[MIT](LICENSE)
