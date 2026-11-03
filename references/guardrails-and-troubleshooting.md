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
| 12 | 中译英完成后跳过术语验证或静默替换 | 术语验证是领域术语准确性的最后栅栏 | 原始术语挪用（big studio/HR department）进入输出，行业观众读着出戏 |

## 依赖

### 必需
- **ffmpeg**：音频提取
- **Python 3.10+**：运行转录脚本；缺少 Python 时需先安装，虚拟环境不能替代 Python 解释器
- **依赖引导**：macOS/Linux 用 `bash scripts/setup.sh --engine groq|local`，Windows PowerShell 用 `./scripts/setup.ps1 --engine groq|local`；本机已有全部依赖时直接复用，否则创建或修复技能虚拟环境并只装缺项，不向系统 Python 全局安装
- **Groq / 对齐**：`requests`、`jieba==0.42.1`、`numpy`

### 转录引擎（至少选一个）
- **mlx-whisper**（macOS arm64 本地模型）：由 bootstrap 按平台安装
- **faster-whisper**（Windows/Linux/Intel Mac 本地模型）：由 bootstrap 按平台安装
- **requests** + API Key（Groq API）：由 bootstrap 按引擎安装

### 中文分词（Groq 模式必需）
- **jieba==0.42.1**：由 bootstrap 安装（MIT，自定义词典支持）
- **numpy**：音频对齐 `audio_align.py` 使用；缺失时脚本会告警并保留原时间戳
- 领域词典：`data/jieba_domain_dict.txt`（UTF-8 userdict 格式，每行 `词语 词频 词性`；未找到时降级为默认词典并打印 warning）

### 本地模型优化（可选，失败自动降级）
- **silero-vad-notorch**（macOS 长音频 VAD 预分片）
- **onnxruntime**（VAD 推理引擎）
- **soundfile**（macOS 音频加载，mlx 必需）

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
| 找不到 Python 或版本低于 3.10 | 安装 Python 3.10+；Windows 可用 `py -3` 启动，macOS/Linux 用 `python3` | 停止执行；没有解释器时不能创建 venv |
| `venv` 的基础 Python 已卸载/升级 | 运行平台对应的 `setup.sh` 或 `setup.ps1`；bootstrap 先尝试修复旧环境，失败则保留旧目录并新建 `.venv` | 使用输出的 `Python executable`；安装失败时报告完整 pip 错误 |
| `ffmpeg` 未安装 | macOS → `brew install ffmpeg`，Ubuntu → `sudo apt install ffmpeg`，Windows → `winget install ffmpeg` | 停止执行，告知用户手动安装后重试 |
| ffmpeg 提取失败 | 检查输入文件是否存在、格式是否支持 | 转换为 WAV 后重试：`ffmpeg -i input -vn -ar 16000 -ac 1 output.wav` |
| refine_segments.py 执行失败（非空输入仍报错） | 检查 raw.srt 格式、校验 refine_segments.py 日志 | 跳过语义断句，使用 Whisper 原始 segments 作为 raw.srt 基线 |
| `import mlx_whisper` 失败（macOS） | 运行平台对应的 setup 脚本并指定 `--engine local` | 检查 Python ≥ 3.10 且为 Apple Silicon 芯片 |
| `from faster_whisper import WhisperModel` 失败（其他平台） | 运行平台对应的 setup 脚本并指定 `--engine local` | 检查 Python ≥ 3.10 |
| Whisper 模型下载失败 | 检查网络、重试 | 确保网络可访问 HuggingFace，或手动下载模型至 `models/` |
| `import silero_vad_notorch` 失败 | 不影响基础转录 | 降级为整段 Whisper 转录（无 VAD 分片） |
| VAD 分片失败（onnxruntime 推理异常） | 跳过 VAD | 降级为整段 Whisper 转录（无 VAD 分片） |
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
| `requests` 未安装（Groq 模式） | 运行平台对应的 setup 脚本并指定 `--engine groq` | 安装失败时报告 pip 错误；不回退到全局 pip |
| `import jieba` 失败（中文断句） | 运行平台对应的 setup 脚本并指定 `--engine groq` | 安装失败时停止并保留原始 SRT，不静默声称完成分词重切 |
| `numpy` 未安装（音频对齐） | 运行平台对应的 setup 脚本并指定 `--engine groq` | 对齐可降级并保留原时间戳；之后补装并重试 |
| config.json 损坏或格式无效 | 提示检查 JSON 格式，展示预期结构 | 用 Question 询问是否重新执行引擎选择流程 |
| Groq 中文 prompt 被复述为幻觉（实测：`请准确转写专业术．保持简体中文。` 在全片静音段出现 20+ 处，no_speech_prob/avg_logprob 均正常，无法被质量指标识别） | 默认不传 prompt（`DEFAULT_GROQ_PROMPT=None`），如需传只传英文 prompt（实测英文 prompt 无复述幻觉但也无术语改善）；黑名单锚点 `请准确转写专业`/`保持简体中文` 清除残留 | 术语错识靠润色环节 correction-table 修正，不依赖 prompt |
| Groq 静音窗口包含短词段（≤3 字符却 4-11s，如 `OK`/`然后`/`对然后`，word 时间戳覆盖整个窗口不可靠） | `_absorb_isolated_crumbs` 将孤立短词吸收进无缝衔接的后段（gap≤0.5s） | 保持原样（文本正确，仅时间轴未精确收缩） |
| Groq 正常段跨大段静默合并（实测 19 处 1.5-16s，如 #87 2.3s/#167 5.9s/#394 6.9s 合并为单条双行字幕） | `_should_force_split` 检 `max_internal_gap>0.80s` → `_split_at_silence` 硬切（绕过 MIN_LINE_CHARS），剩余超长片再评分细切 | `_fallback_split` 标点比率兜底 |
| Groq 输出时间重叠（实测 4 处 279-500ms，如 `100-112.5→101.8-102.6` 前段尾部压后段） | `_deoverlap()` 按 start 排序后 `prev.end = cur.start - 0.02s` 修正，二轮执行（合并后 + 终局缩边后） | 保持重叠（播放器取后段覆盖） |
| Groq 段首尾静默膨胀（`ratio<0.4` 且首/尾各省 ≥0.8s，如 13s 窗口实际语音仅 1.1s） | `_shrink_silent_edges()` 双向缩至 `first_word.start-0.2s`/`last_word.end+0.2s`，合并后二轮缩边 | `_shrink_silent_tail` 仅缩尾（旧逻辑） |
| Groq 对同一输入多次调用返回非确定段数（实测 137/163/172/282/269/524 不等） | 属 API 正常行为，重跑结果不同；段数差异大不代表代码 bug | 对比质量用单次结果，不追求段数一致 |
| Groq 上传大小或格式限制 | 支持格式且 ≤25MB 时原样传入；超限或不支持时由 `_compress_audio` 转成 16kHz mono Opus/OGG | 超限转码仍失败时切换本地模型；不声称转码能提升识别率 |

---
