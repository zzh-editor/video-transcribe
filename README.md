# Video Transcribe

视频音频转录为字幕 Agent Skill — Whisper 转写 + 语义断句 + [可选润色](https://github.com/zzh-editor/Srt-Enhancer) + 可选翻译，输出高精度 SRT 字幕文件。

## 触发词

在支持 Agent Skills 的 CLI（OpenCode、Claude Code、Cursor 等）中，说以下任意一句 + 视频/音频文件路径即可自动调用：

```
转录 / 转录音频 / 转录视频 / 转录字幕
把文件转成字幕 / 把音频转录成字幕 / 把视频转录成字幕
transcribe / transcribe audio / transcribe video
generate subtitles / generate srt / convert to srt
```

输入后 Agent 会自动：提取音频 → Whisper 转写 → 语义断句 → [可选] 润色 → [可选] 翻译 → 输出 SRT。

## 安装

### OpenCode / Claude Code / Cursor 等

```bash
# 安装（自动注册到 skills 列表）
npx skills@latest install https://github.com/zzh-editor/video-transcribe

# 更新
npx skills@latest update https://github.com/zzh-editor/video-transcribe

# 查看已安装的 skills
npx skills@latest list
```

### 直接克隆（开发者）

```bash
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash scripts/setup.sh
git pull origin main  # 更新
```

> 需要 Python 3.8+ 和 ffmpeg。

## 工作流程

```
本地视频/音频文件
      │
      ▼
① 提取音频 (ffmpeg → 16kHz WAV)
      │
      ▼
② VAD 切割
      │   macOS arm64 → Silero VAD 分片（silero-vad-notorch + onnxruntime）
      │   其他平台   → faster-whisper 内置 vad_filter
      ▼
③ Whisper 逐片转录
      │   macOS arm64 → mlx-whisper (Apple GPU)
      │   其他平台   → faster-whisper (CPU/CUDA)
      │   含 logprob_threshold=-1.0, no_speech_threshold=0.6 降噪
      ▼
④ refine_segments.py 预清洗 + 级联语义断句
      │   去空/零时长/重复 → 6 级切割（强连词/话题/话语/时间/回应模式/重复检测）
      │   + 动态最小字数控制 + 递归切割
      ▼
⑤ cleanup_segments.py 后清洗
      │   合并相邻重复 + 幻觉检测（重复字符循环过滤）
      ▼
⑥ raw.srt ← 原始 SRT
      │
      ▼ 🔴 CHECKPOINT: 润色确认
      ├── 是 → ⑧ srt-enhancer 润色
      └── 否 → ⑨ 以 raw.srt 为基线
      │
      ▼ 🔴 CHECKPOINT: 内容验证 + 语种判断
⑩ [可选] 翻译（纯中文 / 中上原下 / 原上中下）
      │
      ▼
⑪ 输出：最终 SRT（与输入文件同目录）
```

## 处理能力

| 功能 | 说明 |
|------|------|
| 平台自适应 | macOS arm64 → mlx-whisper (Apple GPU)；其他 → faster-whisper (CPU/CUDA) |
| VAD 智能分片 | Silero VAD 精准切割静音段，min_segment_s=1.0 过滤短爆音 |
| ASR 降噪 | logprob_threshold=-1.0 过滤低置信语音块，no_speech_threshold=0.6 过滤非语音 |
| 语义断句 | 6 级级联切割：强连词/话题标记/话语标记/时间标记/回应模式/重复检测 + 教学口语标记（然后/之后/我们来） |
| 预清洗 | refine 内建 `_clean_segments`：去空文本/零时长/ASR 重复 |
| 后清洗 + 幻觉检测 | cleanup_segments.py：合并相邻重复 + `_is_repeat_loop` 检测（重复字符循环/黑名单） |
| 动态最小字数 | 高频教学标记按触发词动态控制最小左右字数，防止过度碎切 |
| 应答词保护 | 50+ 个应答词独立成块，不与其他合并 |
| [可选] 润色 | 调用 srt-enhancer：去口癖、ASR 纠错、的得地修正、混排空格、标点清理 |
| [可选] 翻译 | AI 逐段翻译，支持纯中文/中上原下/原上中下三种模式，含游戏美术/3D 行业补充指南 |

## Pipeline 验证点

`video-transcribe` 包含 2 个强制用户确认检查点（🔴 CHECKPOINT）：
- **润色确认**：srt-enhancer 存在时询问是否调用，不存在时询问是否安装
- **翻译确认**：含内容验证（校验 `tmp/final.srt` 非空 + 预览前 5 条）+ 语种判断 + 翻译模式选择

## 直接调用脚本

```bash
# 基础转录
venv/bin/python3 scripts/transcribe.py audio.wav \
  --output raw.srt \
  --language zh \
  --max-line-length 25 \
  --max-line-ms 6000

# 强制启用 VAD（≥30 分钟长音频推荐）
venv/bin/python3 scripts/transcribe.py audio.wav \
  --output raw.srt \
  --language zh \
  --vad

# 强制关闭 VAD
venv/bin/python3 scripts/transcribe.py audio.wav \
  --output raw.srt \
  --language zh \
  --no-vad

# 独立运行语义断句验证
venv/bin/python3 scripts/refine_segments.py raw.srt

# 独立运行后清洗验证
venv/bin/python3 scripts/cleanup_segments.py raw.srt
```

## 脚本

| 脚本 | 用途 |
|------|------|
| `scripts/transcribe.py` | Whisper 转录核心，平台自适应引擎（mlx-whisper / faster-whisper），内建 refine+cleanup |
| `scripts/refine_segments.py` | 预清洗 + 6 级级联语义断句引擎 |
| `scripts/cleanup_segments.py` | 后清洗：合并相邻重复段 + 幻觉检测（`_is_repeat_loop`） |
| `scripts/setup.sh` | 依赖自动安装（创建 venv、安装依赖、配置模型路径） |

## 文件结构

```
video-transcribe/
├── scripts/            # 可执行脚本
│   ├── transcribe.py       # Whisper 转录核心
│   ├── refine_segments.py  # 预清洗 + 语义断句引擎
│   ├── cleanup_segments.py # 后清洗 + 幻觉检测
│   └── setup.sh            # 依赖安装
├── venv/               # Python 虚拟环境（自动创建）
├── models/             # Whisper 模型缓存（自动下载，约 1.6GB）
├── docs/               # 行业翻译规则
├── SKILL.md            # Agent skill 定义（含完整工作流）
└── README.md
```

## 依赖

### 必需
- **ffmpeg** — 音频提取（`brew install ffmpeg` / `sudo apt install ffmpeg`）
- **Python 3.8+**
- **mlx-whisper**（macOS arm64）— Apple GPU 加速
- **faster-whisper**（其他平台）— CPU/CUDA 转录
- **silero-vad-notorch** — VAD 分片（无 torch 依赖）
- **onnxruntime** — VAD 推理引擎

### 可选
- **srt-enhancer** — 独立润色技能，提供去口癖、ASR 纠错、混排规范化

## 失败处理

| 场景 | 处理方式 |
|------|---------|
| ffmpeg 未安装 | 提示安装命令，停止执行 |
| refine_segments.py 执行失败 | 跳过语义断句，使用 Whisper 原始 segments |
| Whisper 模型下载失败 | 检查网络，重试或手动下载至 `models/` |
| VAD 分片失败 | 降级为整段 Whisper 转录 |
| srt-enhancer 不存在 | 询问用户是否安装，拒绝则跳过润色 |
| 转写内存不足 | 关闭其他应用，确保 8GB+ RAM |
| AI 翻译失败（超时/格式异常） | 减小 batch 重试 → 跳过翻译，以原文输出 |

## License

[MIT](LICENSE)
