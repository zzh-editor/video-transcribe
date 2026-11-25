## Groq API 转录

使用 `whisper-large-v3`，请求顶层 segments 和字符级 words。原始响应落盘到 `<输出srt>.raw.json`，后处理算法调整可离线重放，不再消耗 API。只读取 `GROQ_API_KEY` 环境变量；未设置时，`transcribe.py` 从技能目录 `.env` 载入同名变量。

### 准备音频

Groq 支持且不超过 25MB 的文件原样上传；超限或不支持的格式才转成 16kHz 单声道 Opus/OGG。不要手动有损转码以规避大小限制。

```bash
cp "<input>" "<output_dir>/tmp/audio.src"
```

文件超过 **25MB** 时，`_compress_audio` 按时长推算 16–128kbps 码率并自动压缩；超限时最多减半码率重编 3 次。

### 长音频分片调用（>30 分钟）

Groq 官方文档明确建议大音频分片（chunking）后独立处理再合并。实测 68 分钟单次调用出现两类解码失败：

1. **语言漂移**：单次长调用中 Whisper 解码器语言锚定失效，中文语音被整段转成英语（W13 的 2334s、2767s、4045s 段），且 `no_speech_prob` 很低（解码器"自信"），文本级清理无法识别。半小时分片 + 每片 `--language` 强制锚定可防。
2. **解码退化**：长静默后输出 `"."` 纯标点段或丢失真实语音（W13 的 2082-2176s 段 7 条真实字幕全丢）。半小时一片时片内仍含长静默，此风险不靠分片根治，由转录后的本地 VAD 幻觉清理兜底；若发现真实语音丢失，可临时调小 `CHUNK_MERGE_GAP_S` 回到细粒度分片。

触发条件：音频 > 30 分钟且 VAD 地图可用时，`groq_transcribe.plan_chunks` 按半小时上限贪心装桶（gap <300s 合并、单片跨度 ≤30 分钟），片边界只落在语音区间边缘的静默处（不截词），每片 ffmpeg 切出（语音区 ±0.2s pad）单独上传：

- 每片独立调用，解码上下文重锚定，语言用 `--language` 每片强制传入（长音频必须显式指定语言）
- 半小时一片且每片强制 ≤25MB（`_cut_chunk` 按跨度自适应码率，默认 48kbps 时 30 分钟 ≈ 10.8MB，留足余量），一个 68 分钟音频只要 2-4 次调用；片内会包含长静默，静默幻觉由转录后的本地 VAD 幻觉清理兜底（见下节）
- word/segment 时间戳按片偏移回填全局时间轴；片间节流 3.3s 保 RPM
- VAD 不可用时降级为单次调用（25MB 压缩路径不变）

### 本地 VAD 语音地图（长音频幻觉防护）

转录响应返回后、进入断句前，`transcribe.py` 用本地 Silero VAD（ONNX，免 torch，`silero-vad-notorch`）对原始音频做一次全片扫描，写 `<输出srt>.vad.json`：

```json
{"duration": 4080.9, "speech": [{"start": 0.31, "end": 5.12}, ...],
 "speech_ratio": 0.62, "gap_count": 87, "largest_gaps": [...]}
```

该地图喂给三个环节：

1. **幻觉清理**（`cleanup_segments._is_offspeech_hallucination`）：与语音重叠 <0.3s 且条目中点距最近语音区 ≥2.0s（深入静默区）的条目判定为静默窗幻觉并删除（reason `offspeech_vad`，写入 cleanup sidecar），中英文路径都生效。2.0s 容忍带吸收 Silero 对轻声语音的浅层（0.8-1.0s）漏检；真实幻觉位于大 gap 深处（实测 78-369s）。黑名单含拆分变体（`请不吝点赞订阅转发打赏支持`、`明镜与点点栏目` 单独成条）。

### 后处理参数离线重放

调整断句/清理参数不需要重新转录：`raw.json` + `vad.json` 喂给重放脚本即可零成本验证：

```bash
"<PYTHON_EXECUTABLE>" scripts/replay_postprocess.py "<输出srt>.raw.json" \
  --vad "<输出srt>.vad.json" --out tmp/replay.srt
```

### 已知限制（whisper-large-v3 识别能力，非管线缺陷）

- **术语听错**：游戏美术词汇偶发错字（低模→DMOD、check action→我查一下）。领域词汇表可经 `--prompt` 传入，但中文 instruction prompt 有在静默窗被念成幻觉语音的先例（分片后静默近零、风险下降，仍需 A/B 实测后再启用）。
- **繁体偶发**（~1.6% 条目）：`--language zh` 下 Groq 解码器可能输出繁体（"這邊的話…"）。现已默认注入简体引导 prompt（`ZH_SIMPLIFIED_PROMPT`，与本地引擎 `INITIAL_PROMPT_ZH` 同文），把简体锚定提前到 Groq 阶段；剩余漏网繁体仍交由润色阶段（srt-enhancer）统一简体。用户显式传 `--prompt` 时不叠加引导句。
- **极短轻声词**：孤立 "OK/对不起" 类 <0.5s 词偶尔被服务端 VAD 丢弃，audit sidecar 与人工校对可补。
2. **静默硬切**（`groq_word_adapter._vad_split_indices`）：Groq 会把词的 span 拉伸到填满静默，词间隙扫描看不见真实停顿；VAD gap ≥0.8s 处强制切分，跨隙词归左片，片间保留 150ms 间隙。
3. **碎片合并**（`_crumb_gap`）：合并判定优先用 VAD 语音区间的真实 speech-to-speech 间距，其次词时间戳，再次 RMS 能量。

`min_gap_s=0.6` 以下的停顿合并为同一语音区，不参与断句。依赖缺失（`silero-vad-notorch`/`onnxruntime`）或解码失败时打印 warning 并降级为原词间隙启发式，转录不中断。`vad_map.py` 可独立运行：

```bash
"<PYTHON_EXECUTABLE>" scripts/vad_map.py "<audio>" -o map.json
```

### 执行

用户选择自动识别时省略 `--language`；半小时以上的长音频必须通过问卷确认语种并显式传入，否则可能语言漂移（见上文"长音频分片调用"）。传 `--language zh` 时若未另传 `--prompt`，会自动附加简体引导句（`ZH_SIMPLIFIED_PROMPT`），在 Groq 阶段锚定简体输出，避免事后简繁转换。API 返回的识别语种会决定后处理分支和默认断句参数。

```bash
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.src" \
  --output "<output_dir>/tmp/raw.srt" \
  --language en --max-line-length 60 --max-line-ms 6000 \
  --engine groq
```

首次使用且 `GROQ_API_KEY` 未设置时，在 Agent 会话中请求密钥一次；收到后自动写入技能目录 `.env`，设为仅当前用户可读写，后续转录自动载入。密钥不得回显或写入日志。`.env` 不进入 Git；复制技能到其他设备时，需通过安全渠道随目录复制该文件。

### Groq 后处理约束

- API 服务端处理 VAD，不做本地分片；本地 VAD 仅做后处理语音地图（见上文"本地 VAD 语音地图"）。
- 中文 words 为字符级，由 `groq_word_adapter.py` 用 jieba 聚词。
- 中文异常段按 `char_count > max_chars` 或 `duration > max_line_ms` 切分；正常段在词间隙 **≥0.80s** 或 VAD gap ≥0.80s 时强制切分，否则只收缩静默边界。静默边缘在局部能量比低于 0.4 且可收缩至少 0.8s 时调整；首尾 padding 为 0.2s；有 VAD 地图时 wordless 段直接用语音区间定位主语音块，不再走能量扫描。
- `_deoverlap()` 修正相邻条目重叠，保留 150ms 安全间隙（`groq_word_adapter.py` 的 `_OVERLAP_EPS_S = 0.15`）；`_absorb_isolated_crumbs()` 只把无缝衔接的孤立短词吸收到邻段，不跨明显停顿。
- 缺失 words、时间无效或覆盖率不足时按 segment 回退，不能伪造词级时间。
- `--full-segment` 是 Groq 专用调试选项，会对所有 segments 强制评分重切；默认不要启用，A/B 比较时才临时使用并验证。
- 响应形状为 `{segments, words}`；word timestamps 是顶层字符级数据，不嵌在 segment 中。
- 默认导出 `<输出srt>.words.json`；需要自定义路径时才传 `--export-refined`。
- 每次运行还写 `<输出srt>.cleanup.json`，记录清理动作；无动作时内容为 `[]`。
- 英文路径跳过中文密度启发式，保留黑名单和重复环检测；`audio_align.py` 收缩句尾静默。
