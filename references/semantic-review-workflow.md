## L3 语义断句复核

仅当 `transcription_intake.semantic_review=true` 时处理；为 false 时直接保留评分引擎结果，不再询问。没有 intake 时先回到开场问卷补齐答案，不得在音频转写后才询问。

转写默认生成 `<输出srt>.words.json`，直接复用，不需要重新转录。Agent 只在安全断点中选语义合适的切点，并从相邻合并候选中选择被 ASR 错切的残句组。

### 低 Token 复核与竖屏交接

- `--compact-llm-view` 会在生成 `splits.json` 时另写 `<output>.llm.txt`，仅含候选文本、切点标号 `[k@char]` 和合并候选，不含 boundary 回显或 word 浮点时间戳。复核只读 `.llm.txt`，把选中的 char 写入 `splits.json` 的 `selected_cuts`；`--apply` 仍需完整 JSON。
- `splits.json` 的结构是 `{idx: {safe_cuts: [...]}}`，与竖屏脚本 `--splits` 的 `{idx: [chunk, ...]}` 不同，不能互传。竖屏时另生成 chunk 计划，或传入与增强字幕对应的 `--words-json`。

### A. 生成候选与安全断点

```bash
"<PYTHON_EXECUTABLE>" scripts/semantic_review.py \
  "<output_dir>/tmp/raw.srt.words.json" "<output_dir>/tmp/raw.srt" \
  -o "<output_dir>/tmp/splits.json" --compact-llm-view
```

复核时只读 `splits.llm.txt`：它只含候选文本、切点标号 `[k@char]` 和合并候选，不含 boundary 回显或 word 浮点时间戳。完整 `splits.json` 留给 `--apply`。

候选筛选条件：去掉空白后不少于 16 字且无标点的段、时长超过 4 秒的段、纯英文短残片（≤3 个字母）。脚本通过 `_align_chars` 贪心对齐文本与 words，容忍 ASR 错序和错切。

安全切点规则：

- 中文切点落在 jieba search-mode 词界，避免切断词语。
- 英文切点不得落在 `[A-Za-z0-9._+\-#&/]+` token 内部。
- 中英切换位置和标点边界天然安全。
- words 缺失但文本有换行时，可按换行生成比例时间切点并标记 `time_mode=proportional`；无 words 且无换行时保持 `time_reliable=false`，不伪造切点。
- `time_reliable` 由覆盖率（≥90%）和时间单调性共同判断。只有可靠段进入 LLM 切分。
- `_merge_candidates` 仅列出时间相接、合计不超过 30 字、后条由助词/代词/承接动词起始的候选；不自动合并。

### B. 选择并应用切点

1. 只处理 `time_reliable=true` 的候选段，只从 `safe_cuts` 里选；其他段保持不变。
2. 中文按小句边界切；英文优先原标点，长意群才按指定行长切，不拆词、不跨标点合并、不套中文长度规则。
3. 只合并因代词、助词或动词承接而被 ASR 切开的相邻段；明显停顿、完整句、重复或靠猜测才通顺的内容不合并。合并范围填入 `_selected_merges`，不可重叠、不可跨越已选切点。
4. 将切点写入对应 `selected_cuts`，合并范围写入 `_selected_merges`，再执行：

```bash
"<PYTHON_EXECUTABLE>" scripts/semantic_review.py \
  "<output_dir>/tmp/splits.json" "<output_dir>/tmp/raw.srt" \
  --apply -o "<output_dir>/tmp/refined.srt"
```

脚本校验切点安全性、时间单调性和相邻合并范围。若没有任何有效选择，输出应与 `raw.srt` 相同；成功时用 `refined.srt` 替换 `raw.srt` 作为新基线，并汇报调整前后的条目数。运行脚本使用带 jieba 的 Python；复核跳过或没有有效切点时，保留评分引擎结果。
