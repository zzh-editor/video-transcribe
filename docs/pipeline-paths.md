# Groq vs 本地转写路径差异（P2 文档）

> 更新：2026-09-12。记录两条转写路径的处理差异与已知限制，作为方案《技能优化方案》P1/P2 逐项的落地文档。

## 路径总览

`transcribe.py` 按 `--engine` 分流：

- **groq**：远程 API，`whisper-large-v3`，一次上传全片。
- **mlx / faster-whisper**：本地模型 `large-v2`（mlx 仅 macOS arm64），`VAD 分片 → 逐片转录`。

## Groq 路径（`transcribe.py:453-515`）

1. `transcribe_groq()`：音频归一化为 16kHz 单声道 Opus/OGG，≤25MB（`_compress_audio` 按时长推 16-128kbps），单请求 `verbose_json` + `segment`/`word` 双粒度。
2. `_clean_segments()` 去空段 + `_merge_fragments()` 修跨段断词。
3. `refine_groq_segments()`（`groq_word_adapter.py`）主切分引擎。
4. `cleanup_segments()` 幻觉/重复清理。
5. 导出 `.words.json` + 写 SRT。

### 数据特征（与本地不同）
- **words 为顶层数组**（不嵌入 segment），单字级时间戳（中文按字）。
- **Groq 词级时间戳存在"静音填充"**：词的 span 会被延伸到下一词 start，单个 CJK 字可标 7-17s（实测 `然后`7s、`这`17s）。词级 gap 因此不可全信。
- **窗口边界无缝**：segment 常前后紧贴（next.start == prev.end），真实停顿被吞。
- 提供 quality metrics：`no_speech_prob` / `avg_logprob` / `compression_ratio`（resolution 在 segment 层）。

### groq_word_adapter 主流程
pre-merge → `_assign_words_to_segments` → abnormal（char/dur 超标）走 `_split_seg_with_words`（jieba 聚词评分），normal 走 `_shrink_silent_edges`；wordless 段只走 `_shrink_wordless_start` 能量收缩；后处理 `_merge_fragments_groq` → `_merge_english_phrase_segments` → `_attach_cn_particle` → re-split → `_absorb_isolated_crumbs` → `_deoverlap`×2。

## 本地路径（`transcribe.py:523-552`）

1. 模型 `large-v2`（默认），macOS arm64 用 mlx，否则 faster-whisper。
2. VAD 分片，每片独立转写（透明传参：max_line_length / pause_threshold / max_line_ms / vad）。
3. `transcribe_mlx/faster` 内部完成首轮切分，words 内嵌在 segment 里（`words` 字段直接可用）。
4. 导出 `.words.json` + 写 SRT。

本地路径**不经过** `refine` / `cleanup`（mlx/faster 引擎内部已做段落切分）。这是与 Groq 路径的最大差异：Groq 依赖 adapter 的 jieba+评分二次切分，本地靠引擎内嵌 words 直接出稿。

## 已知限制与对应修复（本轮改动摘要）

| # | 问题 | 修复 |
|---|---|---|
| P0-2 | Groq 全线 words=[]（老 API 状态）时异常段无兜底 | `refine_groq_segments` wordless abnormal 段用 `_fallback_split` + 能量收缩；`groq_transcribe` 空 words 打警告 |
| P1 | wordless 段尾静音未收缩 | `_shrink_wordless_start` 双向收缩（start+end） |
| bug① | cleanup 密度规则误删英文快速段（20.3 chars/s "find this blue dot."） | `HALLUC_MIN_CHARS_PER_SEC_EN=25` 按语言分阈值（`_is_latin_text`） |
| bug②-1 | 英文短语跨窗口误合并 → 断词（"there"→the+re） | `_build_char_time_map` 标点白名单（Groq 英文词自带标点）+ `_merge_english_phrase_segments` 句点保护 |
| bug②-2 | 静音填充词让 crumb gap 假小 → 超长合并段（24.8s） | `_merge_fragments_groq` 加 energy 参数，边界词 span > `_WORD_FILL_MAX_S`(2s) 用 `speech_clusters` 重锚 gap |
| 额外 | Whisper 30s 尾段 "谢谢大家" 幻觉 | cleanup 加 `_is_sparse_hallucination`（>20s 且 <0.5 字符/秒） |

## 验证

- 165 tests OK（venv python 3.14.7 / jieba 0.42.1）。
- 端到端重放：192 段+1321 words → refined 289 → cleaned 287，最长段从 24.8s 降到 6.17s。
- 文本守恒：diff 仅剩 "OK" 去重 + "Thank you." 稀疏幻觉清理，均属正确行为，无真实文本丢失。