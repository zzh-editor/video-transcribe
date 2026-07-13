# Video Transcribe

视频/音频转录为 SRT 字幕的工具。提取音频 → Whisper 转写（本地模型或 Groq API）→ 断句/合并 → 可选润色 → 可选翻译，输出高精度字幕。

引擎自适应：macOS arm64 默认 mlx-whisper（Apple GPU 加速），其他平台用 faster-whisper；可选 Groq API（远端 whisper-large-v3，首次运行选引擎并保存配置）。长音频自动 VAD 分片，Groq 超 25MB 自动压缩。

在支持 Agent Skills 的 CLI 中，说「转录」+ 文件路径即可自动调用。

## 快速开始

```bash
npx skills@latest install https://github.com/zzh-editor/video-transcribe
```

基础转录：

```
用户：转录这个视频 lecture.mp4
Agent：选引擎 → 提取音频 → Whisper 转写 → 断句/合并 → 是否润色？→ 是否翻译？→ 输出 SRT
```

## 触发词

```
转录 / 转录音频 / 转录视频 / 转录字幕
transcribe / transcribe audio / transcribe video
generate subtitles / generate srt / convert to srt
```

## 处理流程

```
输入视频/音频
     │
     ▼
① 提取音频 (ffmpeg → 16kHz WAV)
     │
     ├── 本地模型 ──────────  Groq API ──────
     │  macOS → mlx-whisper               API 服务端转录
     │  其他 → faster-whisper             超 25MB 自动压缩
     │  可选 VAD 分片 (长音频)
     ▼
② refine_segments.py
     ├── 去空/零时长/重复
     ├── 本地模型 → 评分引擎按标点/停顿断句
     └── Groq API  → 合并英文碎片 (保留原始边界)
     │
     ▼
③ cleanup_segments.py (去重 + 幻觉检测)
     │
     ▼
④ raw.srt
     │
     ├── [可选] srt-enhancer 润色 → 去口癖/ASR纠错/混排空格
     └── [可选] AI 翻译 (纯中文 / 中上原下 / 原上中下)
     │
     ▼
⑤ 最终 SRT 输出 (与输入文件同目录)
```

## 功能特性

| 功能 | 说明 |
|------|------|
| 引擎可选 | 本地模型（mlx-whisper / faster-whisper）或 Groq API |
| VAD 长音频分片 | Silero VAD 自动切割静音段，>10min 默认开启 |
| ASR 降噪 | logprob_threshold=-1.0 + no_speech_threshold=0.6 |
| 评分引擎断句 | 基于 word timestamps + pause/标点评分的断句算法（本地模型） |
| Groq 英文碎片合并 | 自动合并 "posit"+"ion" 等跨段英文碎片 |
| 幻觉检测 | 重复字符循环过滤，黑名单模式 |
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

**转录引擎（至少选一个）：**
- mlx-whisper（macOS arm64 本地模型）
- faster-whisper（其他平台本地模型）
- requests + API Key（Groq API）

**本地模型优化（可选，失败自动降级）：**
- silero-vad-notorch + onnxruntime（macOS 长音频 VAD 预分片）
- soundfile（macOS 音频加载）

**可选：** [srt-enhancer（润色功能）](https://github.com/zzh-editor/Srt-Enhancer)

## License

[MIT](LICENSE)
