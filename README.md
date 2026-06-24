# video-transcribe

视频音频转录为高精度 SRT 字幕文件。macOS Apple Silicon 通过 MLX 加速，其他平台自动切换 faster-whisper。

## 安装

```bash
# 使用 npx 安装（推荐）
npx skills@latest install https://github.com/zzh-editor/video-transcribe

# 或直接克隆
git clone https://github.com/zzh-editor/video-transcribe.git
cd video-transcribe
bash scripts/setup.sh
```

需要 Python 3.8+ 及 ffmpeg。

## Pipeline

```
本地视频/音频文件
      │
      ▼
① 提取音频 (ffmpeg → 16kHz WAV)
      │
      ▼
② Whisper 转写 → raw segments 为基线的增量分段
      │   macOS arm64 → mlx-whisper (Apple GPU)
      │   其他平台   → faster-whisper (CPU/CUDA)
      │   合并过短段 + 拆分长句 + 应答词保护
      ▼
③ refine_segments.py 级联语义断句
      │   句末标点 / 转折连词 / 话题标记 / 话语标记 / 时间状语 / OK 隔离
      │   递归切割 + 智能短句合并
      ▼
④ raw.srt ← 原始 SRT（时间轴无损）
      │
      ▼
⑤ 可选 srt-enhancer 润色（去口癖/ASR纠错/混排规范）
      │   传入 --skip refine（本流程已做语义断句）
      ▼
⑥ 可选翻译（纯中文 / 中上原下 / 原上中下）
      │
      ▼
⑦ 输出：最终 SRT
```

## 特性

- **双后端自动切换**：macOS arm64 自动用 mlx-whisper（Apple GPU），其他平台用 faster-whisper
- **以 Whisper raw segments 为基线**：保留模型原生分段质量，仅做必要微调（合并过短段 + 拆分超长段），40+ 应答词保护
- **级联语义断句**：refine_segments.py 以 7 级优先级（句末标点/转折连词/话题标记/话语标记/时间状语/时间词/OK隔离）递归切割，按字符比例分配时间
- **可选润色**：集成 [Srt-Enhancer](https://github.com/zzh-editor/Srt-Enhancer) 去口癖、ASR 纠错、混排空格规范，自动跳过重复的语义断句
- **可选翻译**：非中文内容支持纯中文 / 中上原下 / 原上中下三种翻译模式
- **Whisper large-v2 模型**，首次运行自动下载（约 1.6GB）至技能目录

## 断句策略

```
Phase 1 — 合并过短相邻段
  ├── 段时长 < 0.6s 且与前段间隙 < 300ms
  ├── → 合并到前段（保护词如 好/对/OK 独立保留）
  └── 避免 Whisper 因微小停顿产生的碎片化分段

Phase 2 — 拆分超长段
  ├── 字符数 > 25 或时长 > 6s 触发拆分
  ├── a. 优先在逗号/连词处拆
  ├── b. 次选最大词间隙处拆
  └── c. 字符边界兜底

Phase 3 — 清理
  ├── 丢弃无效时间戳 / 去重连续文本 / 修正时间重叠
```

**后处理 — refine_segments.py 级联语义切割**：以 7 级优先级从高到低检测切割点，递归分割至阈值，最后合并短碎片。

## 使用

```bash
# 安装依赖
bash scripts/setup.sh

# 提取音频
ffmpeg -i input.mp4 -vn -ar 16000 -ac 1 audio.wav

# 转写（中文，每行最长 25 字符，最长 6 秒）
venv/bin/python3 scripts/transcribe.py audio.wav \
  --output output.srt --language zh \
  --max-line-length 25 --max-line-ms 6000

# 独立运行语义优化（可选，transcribe.py 内部已调用）
venv/bin/python3 scripts/refine_segments.py raw.srt
```

参数说明：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--max-line-length` | 25 | 每行最多字符数 |
| `--max-line-ms` | 6000 | 每段最长时长（ms） |
| `--language` | 自动检测 | 语言代码（zh/en/ja 等） |
| `--pause-ms` | 300zh/500en | 停顿断句阈值 |
| `--engine` | auto | 强制指定引擎（mlx/faster-whisper） |

## 依赖

- **ffmpeg** — 音频提取
- **Python 3.8+**
- **mlx-whisper**（macOS arm64）或 **faster-whisper**（其他平台）
- Whisper large-v2 模型（首次运行自动下载）

## 文件结构

```
video-transcribe/
├── scripts/
│   ├── transcribe.py         # 转写主脚本（mlx/faster-whisper + 增量分段）
│   ├── refine_segments.py    # 级联语义断句优化
│   └── setup.sh              # 环境自动安装脚本
├── docs/
│   └── 游戏留学SRT翻译规则.md # 行业翻译补充指南
├── config.json               # 模型配置
├── SKILL.md                  # Agent skill 定义
└── README.md
```

## License

[MIT](LICENSE)
