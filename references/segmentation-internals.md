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
- **中文残片后向并入**：≤3 字纯中文短段（如 `集`、`的话`、`对吧`、`的一个`）在 gap <300ms 时并入前段；前段以英文结尾时，仅限虚词短语集合 `_CN_CLITIC_PHRASES`（`node 的话`、`muscle 的一个`），拼接补空格（精校对照）
- **短英文回并**：前段以中文结尾、后段为短英文（去括注后 ≤16 字符，残片除外）、gap <300ms、合并不超 `min(max_chars,30)` 时回并（`可能比较 repetitive`、`这样子的一个 dictionary`）；真句边界 gap ≥300ms 不触发
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
  ├── 幻觉检测（五条规则并行；命中项写入审计 sidecar）：
  │     ├── `_is_repeat_loop`：极低字符复现率/超短词汇表的 loop 判定
  │     ├── `_is_known_hallucination`：黑名单（支持空格归一化匹配）
  │     ├── `_is_dense_hallucination`：密度判定（≥15 字且时长 <1.0s 时字速 ≥15 字/s → 幻觉）
  │     ├── `_is_sparse_hallucination`：稀疏判定（时长 ≥20s 且字速 <0.5 字/s → 幻觉）
  │     └── `_is_no_speech_hallucination`：质量指标判定（Groq verbose_json 返回的 `no_speech_prob ≥0.8` 且 `avg_logprob ≤-1.0` 时判定静默段幻觉，缺指标则跳过）
  └── 合并相邻重复段（仅当文本不区分大小写相同且 `gap = cur.start - prev.end ≤ 1.0s` 时合并，跨远距重复保留）

两阶段分工：预清洗处理 ASR 噪声（空/零时长），后清洗处理 VAD 边界重叠和幻觉。被删除或合并的条目会写入 `<输出srt>.cleanup.json`，包含原文本、时间、命中规则和动作，供复核与恢复；没有清理动作时 sidecar 写入空数组，避免复用输出路径时留下旧记录。

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
