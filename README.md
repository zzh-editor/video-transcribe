# Video Transcribe

视频/音频转录为 SRT 字幕。提取音频 → Whisper 转写 → refine 断句 + merge 英文碎片 → cleanup 清洗 → 可选润色/翻译，输出高精度字幕。

支持两种转录引擎：

| 引擎 | 平台 | 模型 | 成本 | 速度 |
|------|------|------|------|------|
| **mlx-whisper**（本地） | macOS arm64 | whisper-large-v2 | 免费（本地 GPU） | ~1x 实时 |
| **Groq API**（云端） | 任意 | whisper-large-v3 | 按量付费 | ~50x 实时 |

## 处理流程

```
输入视频/音频
     │
     ▼
① 提取音频 (ffmpeg → 16kHz WAV)
     │
     ├── 本地模型 ──────────────  Groq API ────┐
     │   macOS arm64 → mlx-whisper            │
     │   可选 VAD 分片 (长音频 >10min)        │
     │   word_timestamps=True                 │  API 服务端处理
     │                                        │  timestamp_granularities=segment
     ▼                                        ▼
② refine_segments.py
     ├── _clean_segments (去空/零时长/重复)
     ├── 本地模型 → _segment_words (评分引擎断句)
     └── Groq API → _merge_fragments (合并英文碎片)
     │
     ▼
③ cleanup_segments.py
     ├── 去空文本段
     ├── 幻觉检测 (重复循环过滤)
     └── 合并相邻重复 (VAD 边界重叠)
     │
     ▼
④ raw.srt ← 原始 SRT
     │
     ├── [可选] srt-enhancer 润色 (去口癖/纠错/空格)
     └── [可选] AI 翻译 (纯中文/中上原下/原上中下)
     │
     ▼
⑤ 最终 SRT 输出 (与输入文件同目录)
```

### 路径差异详解

| 阶段 | 本地模型 (mlx) | Groq API |
|------|---------------|----------|
| 音频处理 | ffmpeg → 16kHz WAV | ffmpeg → 16kHz WAV → 超 25MB 自动压缩 MP3 |
| VAD | 可选 Silero VAD 分片（>10min 默认开启） | 无（API 服务端处理） |
| 转录 | mlx-whisper 逐片/全段，word_timestamps=True | Groq whisper-large-v3，segment 级时间戳 |
| refine | _segment_words 评分引擎按标点/停顿/长度断句 | _merge_fragments 仅合并英文碎片（如 "posit"+"ion"），不额外切分 |
| 输出片段数 | 较多（按语义精细切分） | 较少（保留 Groq 原始边界） |

## 快速开始

### 1. 环境准备

```bash
# 必需
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Linux
winget install ffmpeg        # Windows

# 创建虚拟环境并安装依赖
bash scripts/setup.sh
```

### 2. 本地模型（macOS arm64）

```bash
venv/bin/python scripts/transcribe.py lecture.mp3 --language zh
```

参数说明：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--language` / `-l` | 自动检测 | 语言代码，如 zh, en, ja |
| `--output` / `-o` | 同目录 .srt | 输出路径 |
| `--max-line-length` | 25 | 每行最大字符数 |
| `--max-line-ms` | 6000 | 每行最大时长 (ms) |
| `--pause-ms` | 300 (zh) / 500 (en) | 断句停顿阈值 (ms) |
| `--vad` | 自动（>10min 开启） | 强制启用 VAD 分片 |
| `--no-vad` | — | 强制关闭 VAD 分片 |

示例：

```bash
# 10 分钟以内短音频（默认关闭 VAD）
venv/bin/python scripts/transcribe.py short.wav --language zh -o output.srt

# 长音频（自动开启 VAD，<0.8s 碎片合并间隔 1s）
venv/bin/python scripts/transcribe.py lecture.mp4 --language zh

# 手动控制 VAD
venv/bin/python scripts/transcribe.py lecture.mp4 --language zh --vad
venv/bin/python scripts/transcribe.py clip.wav --language en --no-vad

# 自定义断句参数
venv/bin/python scripts/transcribe.py lecture.mp4 \
  --language zh \
  --max-line-length 32 \
  --max-line-ms 8000 \
  --pause-ms 500
```

### 3. Groq API

需要 API Key，从 [Groq Console](https://console.groq.com/keys) 获取。

```bash
venv/bin/python scripts/transcribe.py lecture.mp3 \
  --language zh \
  --engine groq \
  --groq-api-key "gsk_xxx"
```

注意事项：
- 文件超 **25MB** 自动压缩（16kHz MP3，计算最低可用码率）
- 无需 VAD 分片
- refine 阶段**只合并英文碎片、不额外断句**，保留 Groq 原始分割
- 超时 600s，建议 < 2h 音频

### 4. 验证 refine 效果

```bash
# 查看断句统计
venv/bin/python -c "
import sys; sys.path.insert(0, 'scripts')
from groq_transcribe import transcribe_groq
from refine_segments import refine
s = transcribe_groq('audio.wav', api_key='...', language='zh')
r = refine(s, max_chars=25, max_line_ms=6000)
o = sum(1 for i in range(1,len(r)) if r[i]['start']<r[i-1]['end'])
print(f'{len(s)} raw -> {len(r)} refined, overlaps={o}')
"
```

## 文件结构

```
video-transcribe/
├── scripts/
│   ├── transcribe.py          # 主入口：CLI + 双引擎调度
│   ├── groq_transcribe.py     # Groq API 转写 + >25MB 自动压缩
│   ├── refine_segments.py     # 断句引擎：评分断句 / 合并碎片 / 预清洗
│   ├── cleanup_segments.py    # 后清洗：去重 + 幻觉检测
│   ├── setup.sh               # 依赖一键安装
│   └── tests/
│       └── test_refine.py     # 断句引擎单元测试
├── venv/                      # Python 虚拟环境
├── models/                    # Whisper 模型缓存 (～1.6GB)
├── docs/                      # 行业翻译规则
├── SKILL.md                   # Agent skill 定义
└── README.md
```

## 断句算法

### 本地模型路径（有 word timestamps）

评分引擎 `_segment_words` 逐词扫描，基于 6 维度打分：

1. **自然断点**：标点（句号+5，逗号+3）或停顿 ≥ 阈值（+4）
2. **`pending_break` 延迟断点**：行不够长时不切，等到够长再切
3. **溢出评分**：超限时遍历所有切点，取最高分
4. **不良行首/行尾罚分**（-4）：避免以"但是/所以/"等开头或"的/了"等结尾
5. **碎片罚分**（-2）：行 < 8 字符且分数低时抑制切分
6. **英文词边界保护**：不从中文字符边界切段

### Groq API 路径（无 word timestamps）

不额外切分，仅 `_merge_fragments` 合并英文碎片：
- 检测相邻 segment 尾部字母 + 首部字母均为英文字母 → 候选合并
- 合并后总字符 ≤ max_chars 时执行合并
- 两遍：pre-merge（max_chars × 2）处理长碎片 + post-merge（max_chars）兜底

### 后清洗 `cleanup_segments`

- 幻觉检测：低字符复现率 + 极小词汇表 → 循环 hallucination
- 合并相邻重复段（VAD 边界重叠或 refine 切分后相同文本段）

## 依赖

**必需：** ffmpeg, Python 3.8+

| 包 | 用途 | 安装 |
|----|------|------|
| mlx-whisper | macOS arm64 本地转录 | `venv/bin/pip install mlx-whisper` |
| faster-whisper | 非 macOS 本地转录 | `venv/bin/pip install faster-whisper` |
| requests | Groq API 调用 | `venv/bin/pip install requests` |
| silero-vad-notorch | macOS VAD 分片（可选） | `venv/bin/pip install silero-vad-notorch` |
| onnxruntime | VAD 推理（可选） | `venv/bin/pip install onnxruntime` |
| soundfile | 音频加载（mlx 必需） | `venv/bin/pip install soundfile` |

## License

[MIT](LICENSE)
