# Video Transcribe

视频/音频转录为 SRT 字幕的工具。提取音频 → Whisper 转写（本地模型或 Groq API）→ 断句/合并 → 可选润色 → 可选翻译（关键词触发），输出高精度字幕。

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

翻译为可选步骤，仅在请求包含「翻译 / 重写」关键词时触发，不弹窗询问。

## 处理流程

```
输入视频/音频
     │
     ▼
① 提取音频 (ffmpeg → 16kHz WAV)
     │
     ├── 本地模型 ──────────  Groq API ──────────
     │  macOS → mlx-whisper               API 服务端转录
     │  其他 → faster-whisper             超 25MB 自动压缩
     │  可选 VAD 分片 (长音频)             返回顶层 segments + words
     ▼
② refine_segments.py / groq_word_adapter.py
     ├── 去空/零时长/重复
     ├── 本地模型 → 评分引擎按 word timestamps 断句
     └── Groq API  →
         ├── 正常 segment → 保留原始边界
         └── 异常 segment → jieba 聚词 + 评分引擎重拆分
         (缺失 words / 对齐失败 → 标点/比率兜底)
     │
     ▼
③ cleanup_segments.py (去重 + 幻觉检测)
     │
     ▼
④ raw.srt
     │
     ├── [可选] 语义断句复核 → 断句质量接近人工精校 (每段约 8-18 字)
     │
     ├── [可选] srt-enhancer 润色 → 去口癖/ASR纠错/混排空格
     │
     ├── [可选] 翻译 (关键词"翻译"/"重写"触发；输出纯中文/英文)
     │
     ▼
⑤ 最终 SRT 输出 (与输入文件同目录)
```

## 功能特性

| 功能 | 说明 |
|------|------|
| 引擎可选 | 本地模型（mlx-whisper / faster-whisper）或 Groq API |
| VAD 长音频分片 | Silero VAD 自动切割静音段，>10min 默认开启 |
| ASR 解码抑制参数 | 两路径统一显式配置 logprob_threshold=-1.0 + no_speech_threshold=0.6 |
| 评分引擎断句 | 基于 word timestamps + pause/标点评分的断句算法（本地模型） |
| 语义断句复核（L3） | 可选 LLM 复核：脚本从安全断点中分离语义切分，断句接近人工精校（每段 8-18 字、小句边界切分） |
| Groq 顶层 words + jieba | Groq 请求字符级顶层 words，仅对超限 segment 用 jieba 聚词+评分重拆分 |
| Groq 静默硬切 | 正常段含 ≥0.80s 词隙强制切，修复跨 1.5-16s 静默的误合并 |
| Groq 英文碎片合并 | 自动合并 "posit"+"ion" 等跨段英文碎片 |
| Groq 静默治理 | 段首尾静默收缩（缩至 word 边界 +0.2s）、时间重叠修正（279-500ms）、孤立短词吸收 |
| Groq 局部回退 | words 缺失/对齐失败时按 segment 回退，不影响整份字幕 |
| 幻觉检测 | 四规则并联：重复循环/黑名单/密度(≥15字/s 且 <1s)/质量指标(no_speech_prob≥0.8 + avg_logprob≤-1.0) |
| 可选润色 | 调用 srt-enhancer 去口癖/纠错/空格 |
| 可选翻译 | 关键词触发（翻译/重写），按源语言自动判断方向；输出纯中文或纯英文，双语仅按明确要求 |
| 竖屏字幕输出 | 清理临时文件前可选调用 srt-enhancer 竖屏管线，输出 9:16 竖版断句字幕（每行 4-12 字、按语义边界断句、时间轴按字数比例重排） |
| 配置持久化 | 引擎选择、API Key 首次运行保存至 config.json |

## 文件结构

```
video-transcribe/
├── scripts/          # 转写、断句、语义复核、清洗、Groq 适配、安装脚本
├── data/             # jieba 领域词典
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

**中文分词（Groq 模式）：**
- jieba 0.42.1 + 领域词典（`data/jieba_domain_dict.txt`，未找到时降级为默认词典并 warning）

**本地模型优化（可选，失败自动降级）：**
- silero-vad-notorch + onnxruntime（macOS 长音频 VAD 预分片）
- soundfile（macOS 音频加载）

**可选：** [srt-enhancer（润色功能）](https://github.com/zzh-editor/Srt-Enhancer)

## License

[MIT](LICENSE)