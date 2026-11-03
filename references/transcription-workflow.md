## 环境与模型

运行优先使用已具备所需依赖的本机 Python；缺包时才在技能目录创建或复用虚拟环境，不向系统 Python 全局安装包。支持 macOS、Linux 和 Windows。

```
.opencode/skills/video-transcribe/
├── venv/          ← 缺少依赖时创建的 Python 虚拟环境
├── models/        ← Whisper 模型缓存
└── scripts/bootstrap.py ← 跨平台依赖检测与安装
```

Python 要求 3.10 或更新版本。依赖缺失时，bootstrap 会检测本机 Python，优先复用已有运行时，否则修复或创建技能虚拟环境，只安装当前引擎缺少的包。Whisper 模型约 1.6GB，首次使用时自动下载至 `models/` 目录（`HF_HOME` 指向此目录）。

## Step 0: 环境初始化

读取或选择转录引擎后运行：

macOS/Linux 运行 `bash scripts/setup.sh --engine groq`；Windows PowerShell 运行 `./scripts/setup.ps1 --engine groq`。`--engine` 可选 `groq` 或 `local`。首次没有配置时，默认安装 Groq 依赖；若用户随后选择本地模型，再运行一次 `--engine local`。使用输出中的 `Python executable` 路径运行后续 Python 脚本；如果机器没有 Python 3.10+，停止并提示用户安装 Python，不能声称已创建虚拟环境。

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
bash scripts/setup.sh --engine local
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

**Groq API (engine=groq)** — **跳过 WAV 提取，直接把原始文件传给 transcribe.py**（如 mp3/m4a/flac/wav 均可）。Groq 支持且不超过 25MB 的格式会原样上传；超限或不支持的格式才转成 16kHz 单声道 Opus/OGG：

```bash
cp "<input>" "<output_dir>/tmp/audio.src"   # 原样保留，交给脚本处理
```

## Step 2: 转写

读取 `config.json` 确定的引擎，执行转写（此为 LLM Agent 工作流指令，`transcribe.py` 本身通过 `--engine` CLI 参数选择引擎）。

### 英文字幕断句策略

英文字幕保持单行，按原文标点和意群断句。用户提供的精校字幕仅用于学习断句风格，除非确认是同一段音频，否则不能借用其文字或时间轴。

- Groq `--language en` 使用 `scripts/english_segments.py`：即使原段很短，也在有可靠词时间的逗号、分号、冒号及句末标点后断开；标点留在前条。小数、缩写、缩合词中的符号不当作句界。
- 默认 `--max-line-length 60 --max-line-ms 6000`，对齐用户给出的单行样例长度。英文词间空格计入长度。长意群才补切短语边界，不为凑长度跨标点合并，也不把中文 8–18 字规则套在英文上。明确指定的行长以用户要求为准。
- 保留单词、缩合词和正常词间空格；英文片段合并由下方规则控制，不运行中英混排的英文短语/残片合并步骤。
- `merge_english_fragments`：把时间相连、未以标点收尾的残句合成意群；前条以依赖词收尾、后条以小写承接或冠词开头、或后条只有一个词时，才考虑合并。独立介词短语不单独触发合并。句末/逗号等标点、完整短应答和超过 0.4s 的停顿会挡住合并；合并后仍受最大行长限制。未合并的短 cue 再补足可读时长。
- `audio_align.py`：Groq 的 word 时间戳会把句后的静默并进词区间（一条 `so` 占 8.5s）。转录后按 0.2s RMS 包络（阈值 = 噪声底 +10dB）收缩每条的首尾静默，首尾各留 0.25s；改动的条目写入 `<输出>.align.json`。需要 numpy。
- 英文路径的 cleanup 跳过 `dense_text` / `sparse_text` / `no_speech_metrics` 三条密度启发式（阈值是中文口径，英文快速语速与 Groq 的静默段 no_speech_prob 会误伤真实句子）；黑名单与重复环检测保留。
- 内部切点来自原始 words，先按词序对齐，再限制到原 segment 时间范围。缺词或错序不按字符比例估时，`timing_review` 标记保留到 words sidecar；超长或明显异常段须复核或局部补转录。
- 断句回归比较时固定同一份原始响应，避免 API 多次识别差异混入结果；不要把原始响应写进最终字幕目录。

### 使用本地模型 (engine=local)

使用 `scripts/transcribe.py`，平台自适应引擎：

英文输入需将示例中的语言和断句参数改为 `--language en --max-line-length 60 --max-line-ms 6000`；中文仍使用下方示例的中文参数。标点优先路径目前由 Groq 英文入口执行。

```bash
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --max-line-length 25 \
  --max-line-ms 6000
```

**高级选项：**

```bash
# 强制启用 VAD（≥10 分钟长音频建议开启，<10 分钟自动关闭）
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --vad

# 强制关闭 VAD
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --no-vad

# 自定义断句停顿阈值（本地中文 300ms / 英文 500ms，Groq 150ms；--pause-ms 两引擎均生效）
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --pause-ms 500

# Groq 专用：对所有段强制评分重切（默认仅异常段 + 含 ≥0.80s 词隙的正常段；A/B 对比时临时开启，需验证）
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --engine groq --groq-api-key "$API_KEY" \
  --full-segment

# 默认每次转录都会生成带 word 时间戳的 JSON：<输出srt>.words.json
# （如 tmp/raw.srt → tmp/raw.srt.words.json）。L3 语义断句复核直接读取该文件，
# 无需重新转录。--export-refined 仅用于覆盖默认路径：
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --export-refined "<output_dir>/tmp/custom_words.json"
```

脚本特性：
- macOS (arm64) → mlx-whisper（Apple GPU）；其他平台 → faster-whisper（CPU/CUDA）
- 首次运行自动下载模型（约 1.6GB Whisper）至技能目录 `models/`
- 内置 refine_segments.py（语义断句）和 cleanup_segments.py（去空+去重）流水线
- 本地转录会保留 ASR 质量指标；每次运行都会写 `<输出srt>.cleanup.json`，记录清理动作，无动作时内容为 `[]`

### 使用 Groq API (engine=groq)

使用 Groq 云端 `whisper-large-v3`，同时请求顶层 segments 和字符级 words：

以下示例处理英文；中文输入改为 `--language zh --max-line-length 25 --max-line-ms 6000`。

```bash
API_KEY=$(python3 -c "
import json, os
p = os.path.expanduser('~/.config/opencode/skills/video-transcribe/config.json')
print(json.load(open(p))['groq_api_key']
)")
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<输出目录>/tmp/audio.src" \
  --output "<output_dir>/tmp/raw.srt" \
  --language en \
  --max-line-length 60 \
  --max-line-ms 6000 \
  --engine groq \
  --groq-api-key "$API_KEY"
```

注意事项（以下 jieba、异常段评分及比例回退说明适用于中文路径；英文执行上面的独立断句策略）：
- Groq 支持格式（FLAC、M4A、MP3、MP4、MPEG、MPGA、OGG、WAV、WEBM）且 ≤25MB 时原样上传，避免额外有损转码；超限或其他格式才转成 16kHz 单声道 Opus/OGG。不要为绕过大小限制手动转成有损格式
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

**Groq 中文路径**走 `groq_word_adapter.refine_groq_segments()`：取顶层字符级 words，对齐 segment 文本后经 jieba 聚合为词组，仅对超限 segment 复用 `_segment_words()` 评分。本地路径不变。

**Groq 英文路径**在 `transcribe.py` 内直接调用 `refine_english_segments()`，之后运行 cleanup 并写出 SRT。不要再独立运行 `refine_segments.py`，它的混排残片合并会破坏已经选好的英文断点。`timing_review` 条目需说明，不能声称全部时间戳均已精校。

`refine_segments.py` 也可独立调用，但它不做英文专用断句或合并；英文请始终走 `transcribe.py`，以保留原文标点、词级时间和专用合并规则：

```bash
"<PYTHON_EXECUTABLE>" scripts/refine_segments.py "<output_dir>/tmp/raw.srt"
```

> 独立运行不具备 word timestamps，仅走旧版 `_merge_fragments` 与空段过滤；不要用它校准英文断点。需要验证词级断句时走 `transcribe.py` 内存流水线（默认输出 `<output>.words.json`）。

输出覆盖 `raw.srt`（时间轴无损）。算法细节见附录「断句算法」。

## Step 3.5: cleanup_segments.py 清洗

由 `transcribe.py` 在 refine 之后、写入 `raw.srt` 之前自动执行，无感运行。主要职责：
- 合并 VAD 分片边界重叠产生的相邻重复段（同一词出现在两片交界处）
- 检测并移除 VAD 静音段幻觉循环（如重复的"请不吝点赞 订阅 转发"模式）
- 去空文本段（与 refine 的预清洗冗余，做安全兜底）

若需独立验证，也可手动运行：

```bash
"<PYTHON_EXECUTABLE>" scripts/cleanup_segments.py "<output_dir>/tmp/raw.srt"
```

输出覆盖 `raw.srt`。

## 🔴 CHECKPOINT 🛑 STOP: 语义断句复核（L3，可选）

refine 的评分引擎是启发式断句。若要达到人工精校的断句质量（在语义小句边界切分，如动宾后/主语后/语义起始词前），需 Agent 用 LLM 复核，由 `scripts/semantic_review.py` 分离「切分点安全性」和「时间戳」逻辑。LLM 只从安全断点里选语义合适的切点，并从相邻合并候选中选择残句组。

1. 转写已默认生成带 word 时间戳的 segments JSON（`<输出srt>.words.json`，见 Step 2），直接读取，无需重新转录；除非想换路径，否则不用传 `--export-refined`
2. **用 Question 工具弹窗询问用户：**
   - header: "语义断句复核"
    - description: "是否启用 LLM 语义断句复核？中文按小句边界调整，并复核被 ASR 错切的相邻残句；英文限制单行，长句在可靠词级时间点拆分。"
   - options:
     - label: "启用" → description: "运行 semantic_review.py，Agent 在安全断点内选语义断点，写回 raw.srt"
     - label: "跳过" → description: "保留评分引擎断句结果"
   - multiple: false

启用时执行：

**Step A — 生成候选与安全断点（确定性，无 LLM）**：

```bash
cd <video-transcribe 技能目录>
"<PYTHON_EXECUTABLE>" scripts/semantic_review.py "<output_dir>/tmp/raw.srt.words.json" "<output_dir>/tmp/raw.srt" -o "<output_dir>/tmp/splits.json"
```

脚本自动处理（无需 Agent 手动对齐）：
- **候选段筛选**：去空白 ≥16 字符且无标点的段 / 时长 >4s 的段 / 纯英残片段（≤3 字母）
- **字符对齐** `_align_chars`：贪心游走 text ↔ words，容忍 Groq 的错序/错切（如 `感觉好吧 → 感+好吧+觉`）
- **安全断点** `_safe_boundaries`：
  - 中文断点只落在 jieba 词边界（`jieba.tokenize(mode='search')`），避免切断「做树/感觉」这类词
  - 英文断点不得落在 `[A-Za-z0-9._+\-#&/]+` token 内部（不切 smooth/modular/Mouse Shader）
  - 中-英 / 英-中 / 标点边天然安全
- **words 时间戳缺失时按换行断**：Groq 约 40% 段 words 为空但 text 自带 `\n`（自然停顿边界），脚本按字符比例均分时间生成断点（`time_mode="proportional"`），此类段也可切，不再因 `time_reliable=false` 整段跳过
- 标记 `time_reliable`（覆盖率 ≥90% + 时序单调）；输出 splits.json（每段含 text/reason/time_reliable/safe_cuts[]/selected_cuts[]，顶层含 `_merge_candidates`/`_selected_merges`）
- **相邻合并候选** `_merge_candidates`：仅列出时间相接、总长 ≤30 字且后条以助词、代词或承接动词开始的组；不自动合并

**Step B — LLM 选断点并写回**：

1. 读 splits.json，仅处理 `time_reliable=true` 的候选段
2. 用 LLM 判断语义断点：只在 `safe_cuts` 里选（每项含 `char`/`boundary`/`start`/`end`）。中文按小句边界切；英文优先保留原标点边界，长意群按指定行长补切，不跨标点合并、不拆词，也不套用 8–18 字的中文长度要求
3. 复核 `_merge_candidates`：仅选择因代词、助词或动词承接而被 ASR 切开的相邻条目；完整句、明显停顿、重复语音或仅靠猜测才通顺的条目不合并。把每个确认范围填到 `_selected_merges`，如 `[[26, 27]]`；不要跨过已选 `selected_cuts` 的条目。
4. 将选中 char 填入该段的 `selected_cuts` 数组，其他段留空
5. 写回：

```bash
"<PYTHON_EXECUTABLE>" scripts/semantic_review.py "<output_dir>/tmp/splits.json" "<output_dir>/tmp/raw.srt" --apply -o "<output_dir>/tmp/refined.srt"
```

脚本校验每个 cut 在安全断点内、时间单调；`_selected_merges` 只接受不重叠、未被切分的相邻原始条目，合并后取首尾时间并重新编号。若全部无选中则与 raw.srt 内容一致。校验后 `cp refined.srt raw.srt` 作为新基线，并告知用户调整前后段数。

- 不臆造时间轴：words 缺失且无换行的段保持 `time_reliable=false`，不做比例切分
- 不满足判定标准或用户选择跳过 → 保持评分引擎结果。

**注意**：运行需使用 bootstrap 输出的 Python（需含 jieba）。semantic_review 的 apply 只处理 `selected_cuts` 和 `_selected_merges`；其他条目原样保留。
