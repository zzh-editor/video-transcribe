---
name: video-transcribe
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → Whisper 转写（macOS 用 MLX 加速，其他用 faster-whisper） → 可选调用 srt-enhancer 润色 → 可选翻译（默认不触发，仅当请求含关键词翻译/重写时进入翻译；输出纯中文或纯英文，双语仅按明确要求）→ 输出高精度 SRT 字幕文件。触发词：转录、转录音频、转录视频、转录字幕、把文件转成字幕、把音频转录成字幕、把视频转录成字幕、transcribe、transcribe audio、transcribe video、generate subtitles、generate srt、convert to srt"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Question, Task, Skill, WebSearch]
version: 3.0.1
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
② VAD 切割
      │   macOS arm64 → Silero VAD 分片（silero-vad-notorch + onnxruntime）
      │   其他平台   → faster-whisper 内置 vad_filter
      ▼
③ Whisper 逐片转录
      │   macOS arm64 → mlx-whisper (Apple GPU)
      │   其他平台   → faster-whisper (CPU/CUDA)
      │   每 VAD 片独立转录，时间戳绝对化后拼接
      ▼
④ refine_segments.py 预清洗 + 评分引擎断句 / Groq word adapter
      │   _clean_segments 去空/零时长/重复
      │   本地 → word-timestamp 评分引擎（pause/natural break/scored cuts）
      │   Groq  → jieba 聚词 + 评分引擎（异常段必切 + 正常段含 ≥0.80s 词隙强制切 + 静默缩边 + 重叠修复）
      ▼
⑤ cleanup_segments.py 后清洗
      │   合并相邻重复（VAD 边界重叠 + 幻觉检测）
      ▼
⑥ raw.srt ← 原始 SRT
      │
      ▼
⑦ 🔴 CHECKPOINT: 润色确认
      │
      ├── 是 → ⑧ srt-enhancer 润色
      └── 否 → ⑨ 以 raw.srt 为基线
      │
      ▼
⑩ [可选] 翻译（仅请求含“翻译/重写”关键词时触发；纯中文/英文，默认不触发）
      │
      ▼
⑪ 输出：最终 SRT（与输入文件同目录）
```

## 使用方式

用户提供一个本地视频或音频文件路径。此技能不处理 URL 下载，只处理本地文件。

输出 SRT 文件保存在**输入文件所在目录**，命名为 `<输入文件名>_<语言>.srt`。

## 环境与模型

技能完全自包含，所有依赖和模型缓存位于技能目录下：

```
.opencode/skills/video-transcribe/
├── venv/          ← Python 虚拟环境（mlx-whisper / silero-vad-notorch / onnxruntime）
├── models/        ← Whisper 模型缓存
└── scripts/setup.sh ← 依赖自动安装脚本
```

首次运行 `bash scripts/setup.sh` 自动创建 venv 并安装依赖。Whisper 模型约 1.6GB，首次使用时自动下载至 `models/` 目录（`HF_HOME` 指向此目录）。卸载技能时可一并清除。

## Step 0: 环境初始化

```bash
bash scripts/setup.sh
```

脚本自动检测 `venv/` 和 `models/` 目录，缺失则创建并安装依赖。

## 🔴 CHECKPOINT 🛑 STOP: 选择转录引擎

检查技能目录下的 `config.json` 是否存在。

### 首次运行（无配置文件）

**用 Question 工具弹窗询问用户：**
- header: "选择转录引擎"
- description: "选择转录方式：本地模型或 Groq API"
- options:
  - label: "本地模型 (whisper-large-v2)" → description: "使用本地 Whisper 模型，免费但需要 1.6GB 存储空间"
  - label: "Groq API (whisper-large-v3)" → description: "使用 Groq 云端 API，更准确但需要 API Key"
- multiple: false

用户选择 Groq API：
  **用 Question 工具弹窗询问 API Key：**
  - header: "输入 Groq API Key"
  - description: "从 https://console.groq.com/keys 获取"
  - 用户输入 API Key

  保存配置：
  ```json
  {"engine": "groq", "groq_api_key": "gsk_xxx", "groq_model": "whisper-large-v3"}
  ```

用户选择本地模型：
  保存配置：
  ```json
  {"engine": "local"}
  ```

配置文件路径：`~/.config/opencode/skills/video-transcribe/config.json`

### 后续运行

读取 `config.json`，根据 `engine` 字段自动选择引擎。

用户可在提示中覆盖本次选择，例如「这次用 API」或「用本地模型」。若用户要求覆盖，更新 `config.json`。

### 强制本地模型重新下载

若用户要求重新下载模型，运行：
```bash
rm -rf models/
bash scripts/setup.sh
```
Whisper 模型在首次转写时自动下载至 `models/`。

## Step 1: 提取音频

创建临时目录：
```bash
mkdir -p "<output_dir>/tmp"
```

根据引擎选择提取方式：

**本地模型 (engine=local)** — 提取为 16kHz 单声道 WAV（Whisper 标准输入格式）：
```bash
ffmpeg -i "<input>" -vn -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

**Groq API (engine=groq)** — **跳过 WAV 提取，直接把原始文件传给 transcribe.py**（如 mp3/m4a/flac/wav 均可）。`groq_transcribe.py` 会一步转成 16kHz 单声道 Opus/OGG 再上传，避免「先抽 16kHz WAV → 再转 OGG」的二次转换（对输入通常是 mp3 的情况，直接 MP3 → 16kHz mono OGG 单次转码，质量损失最小、上传体也小）：

```bash
cp "<input>" "<output_dir>/tmp/audio.src"   # 原样保留，交给脚本处理
```

## Step 2: 转写

参考第 0.5 步 `config.json` 确定的引擎，执行转写（此为 LLM Agent 工作流指令，`transcribe.py` 本身通过 `--engine` CLI 参数选择引擎）。

### 使用本地模型 (engine=local)

使用 `scripts/transcribe.py`，平台自适应引擎：

```bash
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --max-line-length 25 \
  --max-line-ms 6000
```

**高级选项：**

```bash
# 强制启用 VAD（≥10 分钟长音频建议开启，<10 分钟自动关闭）
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --vad

# 强制关闭 VAD
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --no-vad

# 自定义断句停顿阈值（本地中文 300ms / 英文 500ms，Groq 150ms；--pause-ms 两引擎均生效）
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --pause-ms 500

# Groq 专用：对所有段强制评分重切（默认仅异常段 + 含 ≥0.80s 词隙的正常段；A/B 对比时临时开启，需验证）
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --engine groq --groq-api-key "$API_KEY" \
  --full-segment

# 默认每次转录都会生成带 word 时间戳的 JSON：<输出srt>.words.json
# （如 tmp/raw.srt → tmp/raw.srt.words.json）。L3 语义断句复核直接读取该文件，
# 无需重新转录。--export-refined 仅用于覆盖默认路径：
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --export-refined "<output_dir>/tmp/custom_words.json"
```

脚本特性：
- macOS (arm64) → mlx-whisper（Apple GPU）；其他平台 → faster-whisper（CPU/CUDA）
- 首次运行自动下载模型（约 1.6GB Whisper）至技能目录 `models/`
- 内置 refine_segments.py（语义断句）和 cleanup_segments.py（去空+去重）流水线

### 使用 Groq API (engine=groq)

使用 Groq 云端 `whisper-large-v3`，同时请求顶层 segments 和字符级 words：

```bash
API_KEY=$(python3 -c "
import json, os
p = os.path.expanduser('~/.config/opencode/skills/video-transcribe/config.json')
print(json.load(open(p))['groq_api_key']
)")
venv/bin/python3 scripts/transcribe.py "<输出目录>/tmp/audio.src" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --engine groq \
  --groq-api-key "$API_KEY"
```

注意事项：
- 脚本会统一把输入转成 **16kHz 单声道 Opus/OGG** 再上传（`groq_transcribe._compress_audio` 内置，libopus）。输入已是 16kHz mono OGG 且 ≤25MB 时零转换直传；其余任何格式（mp3/m4a/flac/wav，无论是否超 25MB）一律一步转成 OGG。**不要手动转 FLAC/MP3/wav 再喂脚本**，避免二次转换
- 音频超过 **25MB** 也没关系：`_compress_audio` 按时长推码率（16-128kbps）保证落在限内，超限时自动减半码率重编 ≤3 次
- 无需 VAD 分片（API 服务端处理）
- words 为字符级（中文单字），通过 `groq_word_adapter.py` 用 jieba 聚合为词组后评分
- 异常段（`char_count>max_chars` 或 `duration>max_line_ms`）必切；正常段若含 **≥0.80s 词隙**（`_SILENCE_SPLIT_GAP_S`）亦强制切（实测 19 处 1.5-16s 跨静默合并均需此）；否则仅做静默缩边（`_shrink_silent_edges` 缩首尾、`_SILENT_PAD_S=0.2s`）
- 缺失 words、时间无效或覆盖率不足时按 segment 独立回退（`_fallback_split`）

脚本特性：
- 返回 `{segments, words}`；words 为清洗后的顶层字符级，不嵌入 segment
- 异常检测：`_is_abnormal()` 基于 max_line_length / max_line_ms；**静默硬切优先**：`_split_at_silence()` 在 `gap>0.80s` 处硬切（绕过 `MIN_LINE_CHARS`），剩余超长片再走评分
- 对齐 → jieba 聚词 → 英文/技术标识符保护 → 复用 `_segment_words()` 评分
- 静默治理：`_shrink_silent_edges()` 双向缩首尾（`ratio<0.4` 且省 ≥0.8s 才缩）、`_deoverlap()` 修 279-500ms 重叠（`_OVERLAP_EPS_S=0.02s`）、`_absorb_isolated_crumbs()` 合并短词碎片
- 局部回退：缺 words / 对齐失败 / 覆盖率 < 90% → `_fallback_split()` 标点比率兜底；`_inherit_quality()` 传递 `no_speech_prob/avg_logprob`

## Step 3: refine_segments.py 预清洗 + 语义断句优化

由 `transcribe.py` 在内存中对 Whisper 原始 segments 执行（写入 `raw.srt` 之前），两步合一次调用：

1. `_clean_segments()` — 去空文本/零时长/完全重复（ASR 噪声过滤）
2. `_segment_words()` — word-timestamp 评分引擎断句（自然停顿/溢出分割 + pending_break 回退）

**Groq 路径**走 `groq_word_adapter.refine_groq_segments()`：取顶层字符级 words，对齐 segment 文本后经 jieba 聚合为词组，仅对超限 segment 复用 `_segment_words()` 评分。本地路径不变。

也可独立调用（仅做英文残片合并与空段过滤，不重做 word-level 评分断句）：

```bash
venv/bin/python3 scripts/refine_segments.py "<output_dir>/tmp/raw.srt"
```

> 独立运行不具备 word timestamps，仅走 `_merge_fragments` 与空段过滤；如需验证评分断句能力，请走 `transcribe.py` 的内存流水线（每次转录默认输出 `<output>.words.json`）。

输出覆盖 `raw.srt`（时间轴无损）。算法细节见附录「断句算法」。

## Step 3.5: cleanup_segments.py 清洗

由 `transcribe.py` 在 refine 之后、写入 `raw.srt` 之前自动执行，无感运行。主要职责：
- 合并 VAD 分片边界重叠产生的相邻重复段（同一词出现在两片交界处）
- 检测并移除 VAD 静音段幻觉循环（如重复的"请不吝点赞 订阅 转发"模式）
- 去空文本段（与 refine 的预清洗冗余，做安全兜底）

若需独立验证，也可手动运行：

```bash
venv/bin/python3 scripts/cleanup_segments.py "<output_dir>/tmp/raw.srt"
```

输出覆盖 `raw.srt`。

## 🔴 CHECKPOINT 🛑 STOP: 语义断句复核（L3，可选）

refine 的评分引擎是启发式断句。若要达到人工精校的断句质量（在语义小句边界切分，如动宾后/主语后/语义起始词前），需 Agent 用 LLM 复核，由 `scripts/semantic_review.py` 分离「切分点安全性」和「时间戳」逻辑，LLM 只从安全断点里选语义合适的。

1. 转写已默认生成带 word 时间戳的 segments JSON（`<输出srt>.words.json`，见 Step 2），直接读取，无需重新转录；除非想换路径，否则不用传 `--export-refined`
2. **用 Question 工具弹窗询问用户：**
   - header: "语义断句复核"
   - description: "是否启用 LLM 语义断句复核？耗时增加，但断句质量接近人工精校（每段约 8-18 字、在小句边界切分）"
   - options:
     - label: "启用" → description: "运行 semantic_review.py，Agent 在安全断点内选语义断点，写回 raw.srt"
     - label: "跳过" → description: "保留评分引擎断句结果"
   - multiple: false

启用时执行：

**Step A — 生成候选与安全断点（确定性，无 LLM）**：

```bash
cd <video-transcribe 技能目录>
venv/bin/python3 scripts/semantic_review.py "<output_dir>/tmp/raw.srt.words.json" "<output_dir>/tmp/raw.srt" -o "<output_dir>/tmp/splits.json"
```

脚本自动处理（无需 Agent 手动对齐）：
- **候选段筛选**：去空白 ≥16 字符且无标点的段 / 时长 >4s 的段 / 纯英残片段（≤3 字母）
- **字符对齐** `_align_chars`：贪心游走 text ↔ words，容忍 Groq 的错序/错切（如 `感觉好吧 → 感+好吧+觉`）
- **安全断点** `_safe_boundaries`：
  - 中文断点只落在 jieba 词边界（`jieba.tokenize(mode='search')`），避免切断「做树/感觉」这类词
  - 英文断点不得落在 `[A-Za-z0-9._+\-#&/]+` token 内部（不切 smooth/modular/Mouse Shader）
  - 中-英 / 英-中 / 标点边天然安全
- **words 时间戳缺失时按换行断**：Groq 约 40% 段 words 为空但 text 自带 `\n`（自然停顿边界），脚本按字符比例均分时间生成断点（`time_mode="proportional"`），此类段也可切，不再因 `time_reliable=false` 整段跳过
- 标记 `time_reliable`（覆盖率 ≥90% + 时序单调）；输出 splits.json（每段含 text/reason/time_reliable/safe_cuts[]/selected_cuts[]）

**Step B — LLM 选断点并写回**：

1. 读 splits.json，仅处理 `time_reliable=true` 的候选段
2. 用 LLM 判断语义断点：只在 `safe_cuts` 里选（每项含 `char`/`boundary`/`start`/`end`），在小句边界切（动宾后/主语后/同位语前/语义起始词如"因为/那/你/这种"之前），每段 8-18 字
3. 将选中 char 填入该段的 `selected_cuts` 数组，其他段留空
4. 写回：

```bash
venv/bin/python3 scripts/semantic_review.py "<output_dir>/tmp/splits.json" "<output_dir>/tmp/raw.srt" --apply -o "<output_dir>/tmp/refined.srt"
```

脚本校验每个 cut 在安全断点内、时间单调，重建时间轴重新编号写为 `refined.srt`（若全部无选中则与 raw.srt 内容一致）。校验后 `cp refined.srt raw.srt` 作为新基线，并告知用户调整前后段数。

- 不臆造时间轴：words 缺失且无换行的段保持 `time_reliable=false`，不做比例切分
- 不满足判定标准或用户选择跳过 → 保持评分引擎结果。

**注意**：运行需在技能目录下用 `venv/bin/python3`（jieba 依赖）。semantic_review 的 apply 只处理 `selected_cuts` 非空的段，不含 `selected_cuts` 的段原样保留。

## 🔴 CHECKPOINT 🛑 STOP: 润色确认

检查 `~/.config/opencode/skills/srt-enhancer/` 是否存在。将结果告知用户。

srt-enhancer **不存在** → **用 Question 工具弹窗询问用户：**
- header: "安装 srt-enhancer？"
- description: "srt-enhancer 未安装，是否自动安装以启用润色功能？"
- options:
  - label: "是，安装" → description: "执行 npx skills@latest install ..."
  - label: "否，跳过" → description: "以 raw.srt 为基线继续"
- multiple: false

用户选「是」→ 执行 `npx skills@latest install https://github.com/zzh-editor/Srt-Enhancer`。安装成功则进入 Step 4，失败则跳过润色。

用户选「否」→ 跳过润色，以 raw.srt 为基线继续。

srt-enhancer **已存在** → **用 Question 工具弹窗询问用户：**
- header: "调用 srt-enhancer 润色？"
- description: "是否对原始 SRT 进行去口癖、ASR 纠错、混排规范化等润色处理？"
- options:
  - label: "是，调用润色" → description: "进入 Step 4 润色流程"
  - label: "否，跳过润色" → description: "以 raw.srt 为基线继续"
- multiple: false

## Step 4: [可选] 润色

srt-enhancer 位于 `~/.config/opencode/skills/srt-enhancer/`，提供去口癖、ASR 纠错、的/得/地修正、标点清理、中英文混排空格规范化。

### 必须执行的子步骤

用 Skill 工具加载 srt-enhancer 技能后，**必须按顺序执行以下子步骤**，不可跳过：

| 子步骤 | srt-enhancer 对应 | 本流程约束 | 说明 |
|--------|-------------------|-----------|------|
| ① 领域检测 | Step 2.5 `domain_scanner.py` | 无 | 自动检测字幕领域（Maya/Python/Gaming/AI-3D 等），为联网搜索提供 `search_context` |
| ② AI 构建 config + 联网校准术语 | Step 3 | 无 | **必须执行联网搜索**：对 correction-table.md 未匹配的术语，用 `"{term}" + "{search_context}"` 联网校准权威写法。生成的 config 和 terminology_overrides **在对话中直接展示**（非 Question 弹窗），征询用户意见 |
| ③ enhance.py 流水线 | Step 4 | 无（srt-enhancer 已不包含断句步骤） | 保留 normalize → terminology → spacing → finalize |
| ④ AI 复核 + 置信度评分 | Step 5-6 | 无 | 书名号标记 + 置信度评分 |
| ⑤ 用户确认 diff | Step 6 | 无 | 组织 diff 审核表**直接在对话中输出**（非 Question 弹窗），然后简短提问确认。用户确认后将增强结果写入 `tmp/enhanced.srt` |

### 不执行的步骤

- **跳过 Step 7「Generate Output File」**：输出由本流程 Step 5-7 接管

### 调用模板

```
加载 srt-enhancer 技能，将 tmp/raw.srt 作为输入。必须执行：
1. 运行 domain_scanner.py 检测领域
2. AI 构建 config 并对未匹配术语执行联网校准
3. 运行 enhance.py
4. AI 复核 + 置信度评分 + diff 审核
5. 用户确认后，将增强结果写入 tmp/enhanced.srt

跳过 Step 7（Generate Output File），返回本流程。
```

## 🔴 CHECKPOINT 🛑 STOP: 润色完成验证

srt-enhancer 执行完毕后，**必须逐项确认以下内容**，任一项未通过则回退到 raw.srt：

| 验证项 | 如何确认 | 未通过处理 |
|--------|---------|-----------|
| 领域检测已执行 | srt-enhancer 报告了检测到的领域（如 `general`/`maya`/`python`） | 重新执行 domain_scanner.py |
| 联网校准已执行 | diff 审核表中有「联网校准」类型的修改项，或术语表已全部匹配无需联网 | 检查 config 中 domain 是否正确，确保 search_context 非空 |
| diff 审核表已展示 | 用户已确认或逐条审核 diff 表 | 跳过润色，以 raw.srt 为基线 |
| tmp/enhanced.srt 存在 | `ls tmp/enhanced.srt` 成功 | 跳过润色，以 raw.srt 为基线 |

确认全部通过后进入 Step 5。

## Step 5: 准备基线字幕

将润色或原始的 SRT 统一为 `tmp/final.srt`：

```bash
if [ -f "tmp/enhanced.srt" ]; then
    cp tmp/enhanced.srt tmp/final.srt
else
    cp tmp/raw.srt tmp/final.srt
fi
```

`tmp/final.srt` 将作为翻译和最终输出的输入。

## 🔴 CHECKPOINT 🛑 STOP: 翻译触发判定

翻译是可选步骤：**仅当用户本次请求包含关键词「翻译」或「重写」时进入翻译管线，否则跳过翻译直接输出。不弹 Question 询问是否需要翻译。**

### 1. 内容验证

```bash
# 确认文栏存在且包含有效 SRT 条目（非空时间戳块）
grep -c '^[0-9]\+$' "tmp/final.srt"
```

验证通过 → 读取前 5 条字幕在对话中展示给用户预览原文内容。
验证失败（空文件/无有效条目）→ 回到 Step 3 重跑 refine+cleanup，如重试后仍无效则报错终止。

### 2. 语种判断与翻译决策（含双向路由）

从 Whisper 输出中获取检测语种并告知用户（同时展示预览片段）。**翻译前必须先判定方向，再加载对应行业规则文件：**

- **用户请求包含 `翻译` / `重写` 关键词** → 进入 Step 6 翻译流水线。方向判定优先级：
  1. **用户请求明确指定方向**（如“翻成英文”“中翻英”“英译中”）→ 以用户指定为准，直接加载对应文件
  2. **未明确指定但源语言为中文（`zh`）** → 默认中译英：加载 `docs/游戏留学SRT翻译规则_中译英.md`（默认全英、去括注）
  3. **未明确指定且源语言为非中文（`en` / 其他）** → 默认英译中：加载 `docs/游戏留学SRT翻译规则.md`（中文优先+英文括注）
- **用户请求不含 `翻译` / `重写` 关键词** → 跳过翻译，直接进入 Step 7 输出原文。

> 方向判定后，后续 Step 6 必须严格按对应文件执行；不可混用两套规则。

## Step 6: [可选] 翻译

AI（当前会话的 LLM）直接逐段翻译，不调用外部翻译 API。
逐段读取 `tmp/final.srt` 中的文本，按判定的模式（见「翻译模式」）生成对应格式，严格保留原始时间戳。

翻译时遵循以下规则，优先级从高到低：

### 基线规则（始终适用，按输出语言区分）

**输出为中文时：**
1. 每行 ≤18 个中文字符，按语义断点拆分
2. 去标点（书名号《》、术语括注 `()` 例外）
3. 中英文间加空格
4. 专有名词保留英文
5. 自然口语化
6. 严格保留时间戳

**输出为英文时：**
1. 每行 ≤42 字符（含空格），按英文语义断点拆分，单条最多两行
2. 允许必要英文标点 `, . ? !`，保持轻量，不用中文标点
3. 中英文/数字间保留自然空格
4. 专有名词/代码/公式保留英文原样
5. 自然口语化、简洁
6. 严格保留时间戳

### 行业补充指南（双向，按方向加载）

- **英译中（非中文 → 中文）**：游戏美术 / 3D 制作 / 绑定 / 游戏留学方向，参见 `docs/游戏留学SRT翻译规则.md`。该文件为**英译中专用**，含术语中文优先+首次括注英文、ASR 误识别修正、留学高频词表等。当与基线冲突时，以基线为准。
- **中译英（中文 → 英文）**：中文课堂/分享类字幕译为英文时，参见 `docs/游戏留学SRT翻译规则_中译英.md`。该文件为**中译英专用**，由英译中镜像倒推，核心是**英文优先、全英输出、去除所有 `中文(英文)` / `英文(中文)` 括注、拼写纠正（如 jointt→joint）、行长 ≤42 字符**。当与基线冲突时，以基线为准。

> **执行要求：** 判定方向后必须在对话中声明已加载哪一份规则文件，并按该文件的 §二 术语处理与 §三 可读性逐条执行。翻译 SRT 字幕时，所有“括注剥离/添加”操作必须按对应文件的 §二.1 执行。

### 翻译模式

进入翻译管线后，模式判定：用户请求中明确给了模式（如「中上原下」「英上中下」）按用户指定；未指定则**必须用 Question 弹窗询问**，不得静默用方向默认。

**用 Question 工具弹窗询问用户（方向已定，只弹模式，不弹方向）：**

- header: "选择字幕模式"
- 方向为中译英（源语言 `zh` / 用户要求翻成英文）时 options：
  1. label: "纯英文字幕（默认）" → description: "全英输出，去除中文括注"
  2. label: "英上中下" → description: "英文在上，中文在下（双语）"
  3. label: "中上英下" → description: "中文在上，英文在下（双语）"
- 方向为英译中（源语言非中文）时 options：
  1. label: "纯中文字幕（默认）" → description: "全中文输出，首次括注英文"
  2. label: "中上原下" → description: "中文在上，原文在下（双语）"
  3. label: "原上中下" → description: "原文在上，中文在下（双语）"
- multiple: false

用户确认后再按所选模式执行。之后若用户仍不指定，才落回方向默认——**中译英 → 纯英文**（全英去括注），**英译中 → 纯中文**。

可用模式（仅三选一，来自上方弹窗选项）：

**英译中：**
1. 纯中文字幕（默认）
2. 中上原下（中文在上，原文在下）
3. 原上中下（原文在上，中文在下）

**中译英：**
1. 纯英文字幕（默认，全英去中文括注）
2. 英上中下（英文在上，中文在下）
3. 中上英下（中文在上，英文在下）

## Step 7: 输出

复制到输入文件同目录：

```bash
if [ -f "<output_dir>/tmp/enhanced.srt" ]; then
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<语言>_Enhance.srt"
else
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<语言>.srt"
fi
```

文件名规则：
- 调用了润色（srt-enhancer）→ `<输入文件名>_<语言>_Enhance.srt`
- 未调用润色 → `<输入文件名>_<语言>.srt`

告知用户文件路径。

**用 Question 工具弹窗询问用户：**
- header: "清理临时文件？"
- options:
  - label: "是，删除 tmp/ 目录" → description: "清理中间文件（WAV、原始 SRT、润色临时文件等）"
  - label: "否，保留" → description: "保留 tmp/ 目录供调试参考"
  - label: "是，先输出竖屏字幕再清理" → description: "对 tmp/final.srt 调用 srt-enhancer 竖屏管线生成 9:16 竖屏字幕，再删除 tmp/ 目录"
- multiple: false

用户选择「是，删除 tmp/ 目录」则执行 `rm -rf "<output_dir>/tmp"`。

用户选择「是，先输出竖屏字幕再清理」则执行竖屏输出流程，完成后删除 tmp/ 目录。

### Step 7.5: [可选] 输出竖屏字幕

对 `tmp/final.srt` 调用 srt-enhancer 的竖屏管线（见 srt-enhancer「输出竖屏字幕」章节），为 9:16 竖屏视频重断句。竖屏字幕每行 4-12 字、按语义边界断句、时间轴按字数比例重排。

**用 Skill 工具加载 srt-enhancer 技能**，对 `tmp/final.srt` 执行：

1. AI 逐条读取文本，生成语义断句计划（每行 ≤12 字、断在语义边界），写入 `tmp/vertical_splits.json`
2. 运行 `scripts/vertical.py tmp/final.srt --splits tmp/vertical_splits.json -o "<输入文件目录>/<输入文件名>_<语言>_竖屏.srt"`
3. 校验输出：断句数增加、每行 ≤12 字、时间轴总跨度不变，并向用户展示 3-5 条断句样例
4. 删除 `tmp/vertical_splits.json`（临时文件）

输出文件名规则：`<输入文件名>_<语言>_竖屏.srt`（润色与否不影响竖屏命名）。完成后执行 `rm -rf "<output_dir>/tmp"`。

## 禁止做的事

| # | 反模式 | 原因 | 违反后果 |
|---|-------|------|---------|
| 1 | 接受 URL 或网络下载输入 | 此技能只处理本地文件 | 无法预估下载时间，文件格式不可控，转录失败率高 |
| 2 | 输出烧录字幕（硬字幕）或视频文件 | 此技能只输出独立 SRT 字幕文件 | 输出文件不可编辑，用户无法二次调整时间轴 |
| 3 | 输出 Markdown / TXT / JSON 格式 | 此技能仅输出 SRT 格式 | 播放器和剪辑软件无法识别，字幕不可用 |
| 4 | 合并润色和翻译到一步 | 执行步骤之间必须经过检查点确认 | 无法逐层审核质量，错误被掩盖，回滚困难 |
| 5 | 修改原始时间戳或合并 SRT 条目 | 翻译时严格对齐原文时间戳 | 中英文时间轴错位，字幕与语音不同步 |
| 6 | 跳过 🔴 CHECKPOINT 检查点 | 检查点是防止自主失控的安全门 | 润色/翻译质量无法审核，错误被掩盖 |
| 7 | 未经用户确认安装 srt-enhancer | 安装应由用户决定 | 意外修改用户环境，可能与已有版本冲突 |
| 8 | 跳过 srt-enhancer 子步骤（省略 domain detection 或 web calibration） | 联网校准是术语准确性的关键保障 | 术语靠 AI 猜测，错别字/ASR 误识别无法修正，字幕质量下降 |
| 9 | Step 4 完成后不验证 srt-enhancer 输出 | 润色结果可能不完整 | 低质量字幕被当作最终结果，用户无法察觉缺失的修正 |
| 10 | 在对话中打印 Groq API Key | API Key 可能被日志记录或泄露 | 只在需要时从 config.json 读取，不在命令或对话中明文显示 |
| 11 | 下载 Whisper 模型前不检查磁盘空间 | 1.6GB 模型可能因空间不足下载失败 | 检查可用空间 ≥3GB 后再下载 |

## 依赖

### 必需
- **ffmpeg**：音频提取
- **Python 3.8+**

### 转录引擎（至少选一个）
- **mlx-whisper**（macOS arm64 本地模型）：`venv/bin/pip install mlx-whisper`
- **faster-whisper**（其他平台本地模型）：`venv/bin/pip install faster-whisper`
- **requests** + API Key（Groq API）：`venv/bin/pip install requests`

### 中文分词（Groq 模式必需）
- **jieba==0.42.1**：`venv/bin/pip install jieba==0.42.1`（MIT，约 19 MB，自定义词典支持）
- 领域词典：`data/jieba_domain_dict.txt`（UTF-8 userdict 格式，每行 `词语 词频 词性`；未找到时降级为默认词典并打印 warning）

### 本地模型优化（可选，失败自动降级）
- **silero-vad-notorch**（macOS 长音频 VAD 预分片）：`venv/bin/pip install silero-vad-notorch`
- **onnxruntime**（VAD 推理引擎）：`venv/bin/pip install onnxruntime`
- **soundfile**（macOS 音频加载，mlx 必需）：`venv/bin/pip install soundfile`
- **socksio**（代理支持）：`venv/bin/pip install socksio`

### 模型
- whisper-large-v2 约 1.6GB（本地模型引擎使用）
- Silero VAD ONNX 约 2.3MB（VAD 优化使用）

### 可选
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

## 临时文件

- `output_dir/tmp/` — 中间产物（提取的 WAV、原始 SRT、润色后 SRT）
- `models/` — Whisper 模型缓存

AI 处理完成后可清理 `tmp/` 目录。

## 失败模式

| 触发条件 | 一线修复 | 仍失败兜底 |
|---------|---------|-----------|
| `ffmpeg` 未安装 | macOS → `brew install ffmpeg`，Ubuntu → `sudo apt install ffmpeg`，Windows → `winget install ffmpeg` | 停止执行，告知用户手动安装后重试 |
| ffmpeg 提取失败 | 检查输入文件是否存在、格式是否支持 | 转换为 WAV 后重试：`ffmpeg -i input -vn -ar 16000 -ac 1 output.wav` |
| refine_segments.py 执行失败（非空输入仍报错） | 检查 raw.srt 格式、校验 refine_segments.py 日志 | 跳过语义断句，使用 Whisper 原始 segments 作为 raw.srt 基线 |
| `import mlx_whisper` 失败（macOS） | `venv/bin/pip install mlx-whisper` | 检查 Python ≥ 3.8 且为 Apple Silicon 芯片 |
| `from faster_whisper import WhisperModel` 失败（其他平台） | `venv/bin/pip install faster-whisper` | 检查 Python ≥ 3.8 |
| Whisper 模型下载失败 | 检查网络、重试 | 确保网络可访问 HuggingFace，或手动下载模型至 `models/` |
| `import silero_vad_notorch` 失败 | `venv/bin/pip install silero-vad-notorch onnxruntime` | 降级为整段 Whisper 转录（无 VAD 分片） |
| VAD 分片失败（onnxruntime 推理异常） | `venv/bin/pip install --upgrade onnxruntime` | 降级为整段 Whisper 转录（无 VAD 分片） |
| VAD 分片后无有效语音段 | 检查音频是否为纯音乐/噪音 | 自动降级为整段转录 |
| 转写内存不足 | 关闭其他应用后重试 | 确保 8GB+ RAM |
| srt-enhancer 不存在 | 用 Question 询问用户是否安装（`npx skills@latest install https://github.com/zzh-editor/Srt-Enhancer`） | 用户同意则安装后进入 Step 4，拒绝则跳过润色 |
| srt-enhancer 安装失败 | 检查网络和 npx 是否可用 | 跳过润色，以 raw.srt 为基线 |
| srt-enhancer 调用失败 | 检查 enhance.py 日志输出 | 跳过润色，以 raw.srt 为基线 |
| srt-enhancer 输出异常（空文件/乱码） | 检查 enhance.py 日志输出 | 跳过润色，以 raw.srt 为基线 |
| domain_scanner.py 执行失败 | 回退到 AI 关键词扫描，使用 `general` 领域 | 跳过领域检测，以 `general` 继续 |
| 联网校准搜索失败（超时/无结果） | 跳过联网校准，使用本地 correction-table.md | 未匹配术语标注 ❗ 低置信度（50-69%）提交用户确认 |
| 联网校准搜索结果无权威来源 | 跳过该术语修正，标注 ❗ | 保留原文，标记 `#unverified` |
| srt-enhancer 子步骤被跳过（未执行 domain detection 或 web calibration） | 回退到 Step 4 重新执行完整子步骤清单 | 跳过润色，以 raw.srt 为基线 |
| AI 翻译执行失败（上下文超限/超时/输出格式异常） | 减小每批翻译量（每次 5 条 SRT 条目）、重试 | 跳过翻译，以未翻译的 final.srt 作为最终输出 |
| 竖屏输出失败（vertical.py 报错/计划缺失） | 用 vertical.py 的 `--max-chars` 硬切回退 | 跳过竖屏输出，仍清理 tmp/ 并告知用户 |
| Groq API 文件 >25MB | 无需处理，直接传原始文件，`_compress_audio` 自动转 16kHz mono OGG 并按时长推码率降到限内 | 转码后仍超限 → 用 Question 询问是否切换本地模型 |
| Groq API Key 无效 (401) | 提示检查 API Key | 用 Question 询问是否重新输入 Key |
| Groq 网络超时 (600s) | 提示连接超时 | 建议切换本地模型 |
| Groq 速率限制 (429) | 提示频率超限，稍后再试 | 建议切换本地模型 |
| Groq API 连接失败 | 检查网络连接 | 建议切换本地模型 |
| `requests` 未安装（Groq 模式） | `venv/bin/pip install requests` | 切换本地模型 |
| `import jieba` 失败（Groq 模式） | `venv/bin/pip install jieba==0.42.1` | Groq adapter 跳过，保留原始 segment |
| config.json 损坏或格式无效 | 提示检查 JSON 格式，展示预期结构 | 用 Question 询问是否重新执行引擎选择流程 |
| Groq 中文 prompt 被复述为幻觉（实测：`请准确转写专业术．保持简体中文。` 在全片静音段出现 20+ 处，no_speech_prob/avg_logprob 均正常，无法被质量指标识别） | 默认不传 prompt（`DEFAULT_GROQ_PROMPT=None`），如需传只传英文 prompt（实测英文 prompt 无复述幻觉但也无术语改善）；黑名单锚点 `请准确转写专业`/`保持简体中文` 清除残留 | 术语错识靠润色环节 correction-table 修正，不依赖 prompt |
| Groq 静音窗口包含短词段（≤3 字符却 4-11s，如 `OK`/`然后`/`对然后`，word 时间戳覆盖整个窗口不可靠） | `_absorb_isolated_crumbs` 将孤立短词吸收进无缝衔接的后段（gap≤0.5s） | 保持原样（文本正确，仅时间轴未精确收缩） |
| Groq 正常段跨大段静默合并（实测 19 处 1.5-16s，如 #87 2.3s/#167 5.9s/#394 6.9s 合并为单条双行字幕） | `_should_force_split` 检 `max_internal_gap>0.80s` → `_split_at_silence` 硬切（绕过 MIN_LINE_CHARS），剩余超长片再评分细切 | `_fallback_split` 标点比率兜底 |
| Groq 输出时间重叠（实测 4 处 279-500ms，如 `100-112.5→101.8-102.6` 前段尾部压后段） | `_deoverlap()` 按 start 排序后 `prev.end = cur.start - 0.02s` 修正，二轮执行（合并后 + 终局缩边后） | 保持重叠（播放器取后段覆盖） |
| Groq 段首尾静默膨胀（`ratio<0.4` 且首/尾各省 ≥0.8s，如 13s 窗口实际语音仅 1.1s） | `_shrink_silent_edges()` 双向缩至 `first_word.start-0.2s`/`last_word.end+0.2s`，合并后二轮缩边 | `_shrink_silent_tail` 仅缩尾（旧逻辑） |
| Groq 对同一输入多次调用返回非确定段数（实测 137/163/172/282/269/524 不等） | 属 API 正常行为，重跑结果不同；段数差异大不代表代码 bug | 对比质量用单次结果，不追求段数一致 |
| Groq 音频压缩质量损失（96-112kbps MP3 丢失高频细节） | 直接把原始输入（mp3/m4a/flac/wav）喂给 transcribe.py --engine groq，`groq_transcribe._compress_audio` 一步转 **16kHz mono Opus/OGG**（仅对已是 16kHz mono OGG 的输入零转换直传，避免 MP3→16k WAV→OGG 二次转换；用户偏好只用 OGG，勿手动转 FLAC/MP3/wav） | 接受 OGG 压缩或切换本地模型 |

---

## 附录: 断句算法

以 Whisper raw segments 为基线，通过 refine_segments.py 做预清洗 + 评分引擎断句：

### 0. 预清洗 `_clean_segments`

进入断句前先过滤 ASR 噪声：
- 空文本段（`text.strip() == ""`）
- 零时长段（`start == end`）
- (文本, 起始时间) 完全重复段

### 1. 评分引擎 `_segment_words`（word timestamps 主路径）

对于有 `words` 字段的 segment，使用评分引擎逐词扫描确定断点：

#### 1a. 自然断点（`natural_breaks`）

在词间停顿 ≥ 阈值时标记，覆盖以下来源：
- **静音停顿**：`words[i].start - words[i-1].end >= pause_threshold`（中文 300ms，英文 500ms）
- **强标点结尾**：左词末尾字符为 `。！？.?!…`（标点奖励在评分中叠加）

#### 1b. `pending_break` 延迟断点机制

自然断点出现在行过短（`chunk_chars < _DEFAULT_MIN_LINE_CHARS=8`）时，不立即切分，而是记录位置；当行长度达到 `max_chars // 2` 时，使用最早记录的自然断点位置切分。此机制保证：
- 不因短行过早切分
- 不会错过自然断点
- 保留最早的自然停顿位置（不覆盖）

#### 1c. 溢出评分 `_score_gap`

当 `pre_chars > max_chars` 或 `pre_dur > max_dur` 时触发溢出，遍历 `[start+1, end)` 所有可能切点，按 6 维度打分：

| 维度 | 条件 | 分值 | 说明 |
|------|------|------|------|
| 强标点 | 左词末位 `。！？.?!…` | `+5` | 句末强分割 |
| 弱标点 | 左词末位 `，、；：,;:` | `+3` | 语气停顿 |
| 静音停顿 | gap ≥ threshold | `+4` | 实际语速停顿 |
| 半停顿 | gap ≥ 0.5 × threshold | `+1` | 轻微停顿 |
| 行长度 | pre_chars ≥ max_chars//2 | `+2` | 行够长时倾向切分 |
| 不良行首 | 右词在 `BAD_LINE_START` | `-4` | 避免以连接词/语气词开头 |
| 不良行尾 | 左词在 `BAD_LINE_END` | `-4` | 避免以助词/介词结尾 |
| 碎片行 | pre_chars < 8 且分数 < 3 | `-2` | 避免过短行 |

取最高分切点。若所有切点分数均 ≤ -999（即无一合法），且存在 `pending_break`，则回退到 `pending_break` 位置。

#### 1d. `correct_edge` 边界修正

切分后检查左块末词和右块首词：
- 若右块首词在 `BAD_LINE_START` 或左块末词在 `BAD_LINE_END`，尝试偏移一个词
- 最多偏移 2 次，避免无限循环

### 2. 降级路径 `_fallback_split`（无 word timestamps）

当 seg 无 `words` 字段时（独立 CLI 运行），退化为简单比率分割：
- 按字符数和时长计算目标片数
- 在常见标点处优先切分（`，、。？！；：`）
- 按时间比分配各片时间戳

### 3. 全局常量

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `_DEFAULT_MIN_LINE_CHARS` | `8` | 最小行字符数（低于此值触发碎片罚分） |
| `_DEFAULT_MAX_CHARS` | `30` | 默认最大行字符数 |
| `_DEFAULT_MAX_LINE_MS` | `4000` | 默认最大行时长（ms） |
| `BAD_LINE_START` | 约 25 词 | 不适宜行首的词（连接词/语气词/助词，如"但是/所以/而且/的/了/吗"） |
| `BAD_LINE_END` | 约 30 词 | 不适宜行尾的词（助词/介词/连词，如"的/了/在/到/从"） |
| `GOOD_LINE_START` | 约 15 词 | 适宜行首的词（逻辑连接词，如"首先/其次/最后/但是/所以"） |

`BAD_LINE_START` / `BAD_LINE_END` / `GOOD_LINE_START` 完整列表见 `refine_segments.py` 第 36-66 行。

### 4. 英文残片合并 `_merge_fragments`

断句后统一执行 `_merge_fragments`，修复被 ASR 拆断的英文单词（如 offer 被拆成 `off`+`er`）：

- **词级修复优先于行长度**：当相邻段前段末字符与后段首字符都是英文字母，且间隙 <300ms（连续语音）时，强制拼接，不再受 `max_chars` 限制（`_MERGE_HARD_LIMIT=60` 字符硬上限兜底）
- **残片前向并入**：纯英文字母 ≤3 字符、时长 <500ms 的段（如独立 `er` 残片），无法并入前段时，尝试并入后段开头
- 时间戳取合并两段的起止

## 附录: 清洗算法

清洗分两阶段：

### 预清洗（refine_segments.py 内建）

`_clean_segments()` 在语义切割前执行，过滤 ASR 噪声信号：
- 空文本段、零时长段、(文本,起始时间) 完全重复段

### 后清洗（cleanup_segments.py）

以 refine 输出的 segments 为基线，通过 cleanup_segments.py 做最终清洗：

cleanup_segments.py
  ├── 去空文本段（text.strip() == "" 直接删除，与 refine 的 `_clean_segments` 冗余兜底）
  ├── 幻觉检测（四规则并联，任一命中即删）：
  │     ├── `_is_repeat_loop`：极低字符复现率/超短词汇表的 loop 判定
  │     ├── `_is_known_hallucination`：黑名单（支持空格归一化匹配）
  │     ├── `_is_dense_hallucination`：密度判定（≥15 字且时长 <1.0s 时字速 ≥15 字/s → 幻觉）
  │     └── `_is_no_speech_hallucination`：质量指标判定（Groq verbose_json 返回的 `no_speech_prob ≥0.8` 且 `avg_logprob ≤-1.0` 时判定静默段幻觉，缺指标则跳过）
  └── 合并相邻重复段（仅当文本不区分大小写相同且 `gap = cur.start - prev.end ≤ 1.0s` 时合并，跨远距重复保留）

两阶段分工：预清洗处理 ASR 噪声（空/零时长），后清洗处理 VAD 边界重叠和幻觉。

## 附录: Groq Word Adapter（顶层字符 words + jieba 聚词）

Groq `whisper-large-v3` 的 `verbose_json` 响应在顶层提供字符级 `words`（中文单字时间戳）。`groq_word_adapter.py` 将其转为词组级 timed units，仅对异常 segment 断句：

### 1. 顶层 word 归属 `_assign_words_to_segments`

每个顶层 word 按最大时间重叠归属到 segment。若多个 segment 有相同重叠长度（跨边界 word），按 word 中点与 segment 中点的距离 tie-break；中点仍相同时优先前 segment。

### 2. 字符时间映射 `_build_char_time_map`

忽略 Unicode 空白和 NFKC 标准化后，将 Groq words 的字符级时间戳按顺序对齐到原文非标点字符。标点时间取前/后最近字符或全段最后时间。

### 3. 覆盖率检查 `_check_coverage`

非标点字符中已有时间映射的比例。≥90% 通过；不足则回退。纯标点段直接 100%。

### 4. jieba 聚词 `_build_phrased_units`

使用独立 `jieba.Tokenizer` 加载 `data/jieba_domain_dict.txt`，精确模式 `tokenize()` 返回原文 offsets，投影到首末字符时间。

### 5. 技术标识符保护 `_protect_technical_tokens`

相邻满足 `_is_ascii_ident`（全 ASCII 字母/数字/`+#-_.&/`）的单元合并为单个 token，防止 `C++`、`NBA 2K`、`Dynamic Hair Pipeline` 被中间拆分。

### 6. 异常检测 `_is_abnormal` + 静默硬切触发 `_should_force_split`

去空白字符数 > `max_chars` 或 duration（ms）> `max_line_ms` 时判定为异常；**正常段若最大词隙 `_max_internal_gap > 0.80s`（`_SILENCE_SPLIT_GAP_S`）亦判定需强制切**（实测 W6 19 处跨 1.5-16s 静默的正常段由此捕获）。异常/强制切均走 `_split_seg_with_words()`。

### 7. 静默治理 `_shrink_silent_edges` / `_deoverlap` / `_absorb_isolated_crumbs`

- `_shrink_silent_edges()`：双向缩首尾，仅当 `speech_span/duration < 0.4` 且省 ≥0.8s 才缩，首尾各留 `_SILENT_PAD_S=0.2s`；合并/吸收后二轮缩边 + 二轮去重叠
- `_deoverlap()`：按 `start` 排序后，`cur.start < prev.end` 时 `prev.end = cur.start - 0.02s`（`_OVERLAP_EPS_S`），修 4 处 279-500ms 重叠
- `_absorb_isolated_crumbs()`：`≤3 字且 >3.0s` 的孤立短词（如 OK/好）若与后段 `gap ≤0.5s` 则并入后段首部；`_merge_fragments_groq()` 合并 `≤5 字` 且词隙 `≤5.0s` 的碎片链

### 8. 硬切优先 `_split_at_silence`

`gap > 0.80s` 的词隙处硬切（绕过 `_DEFAULT_MIN_LINE_CHARS=8` 限制），每片按词组时间夹逼文本（`_build_phrased_units` + 时间重叠筛选），片间按字数守恒校验；硬切后的每片若仍超长再走 `_segment_words()` 评分二次细切。

### 9. refine `refine_groq_segments`（总装）

1. `_assign_words_to_segments()` 归属顶层 words
2. 异常/强制切段 → `_split_seg_with_words()`（含硬切优先）→ `_inherit_quality()` 传递 `no_speech_prob/avg_logprob`
3. 正常无大隙段 → `_shrink_silent_edges()` 仅缩边并保留 `words`
4. 全量 `_merge_fragments_groq()` → `_absorb_isolated_crumbs()` → `_deoverlap()` → 二轮缩边 → 二轮去重叠；每段独立验证文字守恒和时间单调
