---
name: video-transcribe
description: "视频音频转录为字幕。输入视频/音频本地文件，提取音频 → Whisper 转写（macOS 用 MLX 加速，其他用 faster-whisper） → 可选调用 srt-enhancer 润色 → 可选翻译（纯中文/中上原下/原上中下）→ 输出高精度 SRT 字幕文件。触发词：转录、转录音频、转录视频、转录字幕、把文件转成字幕、把音频转录成字幕、把视频转录成字幕、transcribe、transcribe audio、transcribe video、generate subtitles、generate srt、convert to srt"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep]
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
④ refine_segments.py 预清洗 + 级联语义断句
      │   _clean_segments 去空/零时长/重复 → 教学口语标记 / 转折连词 / 话题标记 / 话语标记 / 时间标记分段 + 动态最小字数控制 + 递归切割
      ▼
⑤ cleanup_segments.py 后清洗
      │   合并相邻重复（refine 的重复检测切分后收口）
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
⑩ [可选] 翻译（纯中文 / 中上原下 / 原上中下）
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

## 断句算法

以 Whisper raw segments 为基线，通过 refine_segments.py 做预清洗 + 语义断句优化：

### 0. 预清洗 `_clean_segments`
进入断句前先过滤 ASR 噪声：
- 空文本段（`text.strip() == ""`）
- 零时长段（`start == end`）
- (文本, 起始时间) 完全重复段

### 1. 语义切割（6 级置信度，级联匹配）

| 级别 | 类型 | 触发词 | 切分位置 |
|------|------|--------|---------|
| **STRONG** | 强连词 | 但是/但/所以/不过/然而/可是/因此/因而/而且/并且/那如果/那这样/那就/**然后** | 词首 |
| **STRONG** | 话题标记 | 那（智能区分话题标记 vs 限定词） | 词首 |
| **STRONG** | 话语标记 | 首先/其次/然后/接着/另外/还有/此外/同样/比如说/说白了/也就是说/所以说/**我们来/**我们再来/**来看一下** 等 | 词尾后 |
| **STRONG** | 时间标记 | 到时候/有时候/接下来/那接下来/那现在/现在我们来/我们一起来/**之后/完成之后/做好之后** | 词首 |
| **STRONG** | 回应+话题模式 | 好那/好现在/好那我们/好我们/好接下来/对那/行那 | 正则匹配偏移 |
| **MEDIUM** | OK/语气词隔离 | OK/ok/Okay | 词尾 |
| **MEDIUM** | 回应标签前切分 | 是吧/对吧/好吧/没问题/没错/是的/就这样 等 | 词首 |
| **MEDIUM** | 重复检测 | 前 2 CJK 字符在文本中再次出现 | 重复位置 |

### 2. 动态最小字数 `_MIN_LEN_BY_TRIGGER`

为防止高频教学口语标记（如 `然后`/`之后`/`我们来`）在短片段中过度碎切，对特定触发词要求左右各至少 N 字符才允许切分：

| 触发词 | 最小左右字数 |
|--------|------------|
| 然后 | 6 |
| 之后/完成之后/做好之后 | 5 |
| 我们来/我们再来/来看一下 | 5 |
| 其他 | 3 |

### 3. 递归切割

对子块递归应用上述规则，至无可用切点或切分后子块违反动态字数约束。

### 应答词保护列表

以下词独立成块（不与其他合并，也不被切分）：
好/对/嗯/是/不/行/哦/啊/呀/喏/嗯哼/好的/对的/明白/知道/可以/没错/是的/不行/好吧/对了/对哦/对啊/嗯嗯/好啦/行了/可以啊/没问题/没事/知道了/明白了/没关系/ok/okay/yes/no/right/sure/yeah/yep/nope/nah/alright/indeed 等 50+ 个

## 清洗算法

清洗分两阶段：

### 预清洗（refine_segments.py 内建）

`_clean_segments()` 在语义切割前执行，过滤 ASR 噪声信号：
- 空文本段、零时长段、(文本,起始时间) 完全重复段

### 后清洗（cleanup_segments.py）

以 refine 输出的 segments 为基线，通过 cleanup_segments.py 做最终清洗：

cleanup_segments.py
  ├── 去空文本段（text.strip() == "" 的 segment 直接删除）
  └── 合并相邻重复段（相邻 segment 文本一致时，扩展前段的 end 时间，删除后段）

后清洗置于 refine 之后，因为 refine 的重复检测（Level 8）会主动将文本切为两份，
可能导致相邻 segment 内容相同——cleanup 在这一步统一收口合并。

两阶段分工：预清洗处理上游 ASR 的噪声 block，后清洗处理 refine 切分后产生的相邻重复。

## 失败模式

| 触发条件 | 一线修复 | 仍失败兜底 |
|---------|---------|-----------|
| `ffmpeg` 未安装 | macOS → `brew install ffmpeg`，Ubuntu → `sudo apt install ffmpeg`，Windows → `winget install ffmpeg` | 停止执行，告知用户手动安装后重试 |
| ffmpeg 提取失败 | 检查输入文件是否存在、格式是否支持 | 转换为 WAV 后重试：`ffmpeg -i input -vn -ar 16000 -ac 1 output.wav` |
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

**变量约定**：以下命令中 `<input>` 为用户提供的输入文件路径，`<output_dir>` 为输入文件所在目录（`dirname <input>`）。所有脚本路径相对于技能目录 `~/.config/opencode/skills/video-transcribe/`。

## Step 0: 环境初始化

```bash
bash scripts/setup.sh
```

脚本自动检测 `venv/` 和 `models/` 目录，缺失则创建并安装依赖。

## Step 1: 提取音频

创建临时目录：
```bash
mkdir -p "<output_dir>/tmp"
```

提取为 16kHz 单声道 WAV（Whisper 标准输入格式）：
```bash
ffmpeg -i "<input>" -vn -ar 16000 -ac 1 "<output_dir>/tmp/audio.wav"
```

## Step 2: Whisper 转写

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
# 强制启用 VAD（≥30 分钟长音频必须开启，<10 分钟自动关闭）
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --vad

# 强制关闭 VAD
venv/bin/python3 scripts/transcribe.py "<output_dir>/tmp/audio.wav" \
  --output "<output_dir>/tmp/raw.srt" \
  --language zh \
  --no-vad
```

脚本特性：
- macOS (arm64) → mlx-whisper（Apple GPU）；其他平台 → faster-whisper（CPU/CUDA）
- 首次运行自动下载模型（约 1.6GB Whisper）至技能目录 `models/`
- 内置 refine_segments.py（语义断句）和 cleanup_segments.py（去空+去重）流水线

## Step 3: refine_segments.py 预清洗 + 语义断句优化

在 `raw.srt` 基础上运行 ASR 预清洗（去空/零时长/重复）和内容语义断句（脚本由 transcribe.py 内部调用，也可独立运行验证）：

```bash
venv/bin/python3 scripts/refine_segments.py "<output_dir>/tmp/raw.srt"
```

输出覆盖 `raw.srt`（时间轴无损）。算法细节见「断句算法」节。

**v2 新增**：内建 `_clean_segments` 预清洗 + 教学口语标记（然后/之后/我们来/来看一下）+ 动态最小字数控制。

## Step 3.5: cleanup_segments.py 清洗

在 refine 之后自动运行（transcribe.py 内部调用），无需手动执行。算法细节见「清洗算法」节。

若需独立验证，也可手动运行：

```bash
venv/bin/python3 scripts/cleanup_segments.py "<output_dir>/tmp/raw.srt"
```

输出覆盖 `raw.srt`。

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
| ③ enhance.py 流水线 | Step 4 | 添加 `--skip refine`（跳过 refine 断句，因 Step 3 已做语义断句） | 保留 normalize → terminology → spacing → finalize |
| ④ AI 复核 + 置信度评分 | Step 5-6 | 无 | 书名号标记 + 置信度评分 |
| ⑤ 用户确认 diff | Step 6 | 无 | 组织 diff 审核表**直接在对话中输出**（非 Question 弹窗），然后简短提问确认。用户确认后将增强结果写入 `tmp/enhanced.srt` |

### 不执行的步骤

- **跳过 Step 7「Generate Output File」**：输出由本流程 Step 5-7 接管
- **enhance.py 添加 `--skip refine`**：避免与 Step 3 的语义断句重复切割破坏时间轴

### 调用模板

```
加载 srt-enhancer 技能，将 tmp/raw.srt 作为输入。必须执行：
1. 运行 domain_scanner.py 检测领域
2. AI 构建 config 并对未匹配术语执行联网校准
3. 运行 enhance.py --skip refine
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

## 🔴 CHECKPOINT 🛑 STOP: 翻译确认

从 Whisper 输出中获取检测语种并告知用户。

语种为 `zh` → 自动跳过翻译，进入 Step 7。

语种非中文 → **用 Question 工具弹窗询问用户：**
- header: "选择翻译模式"
- description: "将字幕翻译为中文"
- options:
  - label: "纯中文字幕" → description: "仅输出中文翻译"
  - label: "中上原下" → description: "中文在上，原文在下"
  - label: "原上中下" → description: "原文在上，中文在下"
  - label: "不需要翻译" → description: "保留原文 SRT"
- multiple: false

## Step 6: [可选] 翻译

AI（当前会话的 LLM）直接逐段翻译，不调用外部翻译 API。
逐段读取 `tmp/final.srt` 中的文本，按用户选择的模式生成对应格式，
严格保留原始时间戳，每行中文字幕不超过 18 个字符并按语义断点拆分。

翻译时遵循以下规则，优先级从高到低：

### 基线规则（始终适用）

1. 每行 ≤18 个中文字符，按语义断点拆分
2. 去标点
3. 中英文间加空格
4. 专有名词保留英文
5. 自然口语化
6. 严格保留时间戳

### 行业补充指南

游戏美术 / 3D 制作 / 绑定方向的非中文内容翻译，参见技能目录下的 `docs/游戏留学SRT翻译规则.md`。该文件作为基线规则的行业特化补充，包含术语处理、ASR 术语误识别修正等详细规则。当两者冲突时，以基线规则为准。

### 翻译模式

1. 纯中文字幕
2. 中上原下（中文在上，原文在下）
3. 原上中下（原文在上，中文在下）
4. 不需要翻译

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
- multiple: false

用户选择「是」则执行 `rm -rf "<output_dir>/tmp"`。

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

## 依赖

### 必需
- **ffmpeg**：音频提取
- **Python 3.8+**
- **mlx-whisper**（macOS arm64）：`venv/bin/pip install mlx-whisper`
- **faster-whisper**（其他平台）：`venv/bin/pip install faster-whisper`
- **silero-vad-notorch**（VAD 分片，无 torch 依赖）：`venv/bin/pip install silero-vad-notorch`
- **onnxruntime**（VAD 推理引擎）：`venv/bin/pip install onnxruntime`
- **soundfile**（音频加载）：`venv/bin/pip install soundfile`
- **socksio**（如配置了 SOCKS 代理）：`venv/bin/pip install socksio`
- **模型**：
  - whisper-large-v2 约 1.6GB
  - Silero VAD ONNX 约 2.3MB

### 可选
- **srt-enhancer**：独立润色技能，位于 `~/.config/opencode/skills/srt-enhancer/`

## 临时文件

- `output_dir/tmp/` — 中间产物（提取的 WAV、原始 SRT、润色后 SRT）
- `models/` — Whisper 模型缓存

AI 处理完成后可清理 `tmp/` 目录。
