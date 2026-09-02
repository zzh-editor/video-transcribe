# video-transcribe 最终优化方案

> 版本：v1.0 定稿（2026-08-26）
> 输入：`video-transcribe-audit.md`（P0-P3 13 项）+《针对 video-transcribe-audit 优化方案的分析》（逐条复核）+ 对 `video-transcribe` 实际代码全量通读（SKILL.md v3.0.1 / transcribe.py / refine_segments.py / cleanup_segments.py / groq_word_adapter.py / groq_transcribe.py / setup.sh / README）
> 结论：以第二份复核为准、第一份为底；不重构 pipeline，分 4 阶段落地。

---

## 0. 总体判断

- `VAD → Whisper → refine → cleanup → SRT` 分层合理，Groq 路径的 `_split_at_silence / _shrink_silent_edges / _deoverlap / _absorb_isolated_crumbs` 防御链完整，无需推翻架构。
- 真正阻断的只有 2 项（P0-1 / P0-2）且互锁；其余为参数透传、边界条件、文档契约、测试盲区。
- 第一份报告两处过推：P1-1 把“未显式传参”等同于准确率下降，P1-4 把 Groq 的 overlap 推演到本地 VAD；P3-2 的 `>=` 是策略选择不是 bug。这三项不按原报告机械修复。
- 第一份报告评分 8/10，抓住了实质问题，上述三项需降级或暂缓。

---

## 1. 最终定级矩阵（13 项）

| 编号 | 问题摘要 | 证据位置 | 第一份定级 | 复核定级 | **本方案定级** | 处置 |
|---|---|---|---|---|---|---|
| P0-1 | `transcribe_faster` 未定义，`engine != mlx` 必 `NameError` | `scripts/transcribe.py:468` | P0 | P0 | **P0 立即修** | 实现完整 adapter |
| P0-2 | `setup.sh` 单条 pip 装 `mlx-whisper` 导致非 macOS `set -e` 失败 | `scripts/setup.sh:2,17` | P0 | P0 | **P0 立即修** | 按平台拆分 |
| P1-1 | 非 VAD / fallback 分支未显式传 `logprob/no_speech` 阈值 | `scripts/transcribe.py:225,195` | P1 | 降 P2 | **P2 一致性** | 三处统一显式传参 |
| P1-2 | Groq 路径硬编码 `pause_threshold=0.15` 吞掉 `--pause-ms` | `scripts/transcribe.py:385,420` | P1 | P1 | **P1 立即修** | 用户值优先，默认 0.15 |
| P1-3 | `cleanup` 合并同文本不检查时间间隔 | `scripts/cleanup_segments.py:144` | P1 | P1 | **P1 立即修** | 加 `MAX_DUP_MERGE_GAP_S=1.0` |
| P1-4 | 本地 VAD 缺 `deoverlap` | `scripts/transcribe.py:206` vs `groq_word_adapter.py:692` | P1 | 暂缓 P2/P3 | **P2 待验证，暂不全局植入** | 先补 invariant test |
| P2-1 | `_verify_text/_verify_times` 未接入生产流程 | `scripts/groq_word_adapter.py:612,639` | P2 | P2 | **P2 修** | mutation 出口统一校验 |
| P2-2 | 独立 `refine_segments.py input.srt` 对 SRT 基本空转 | `scripts/refine_segments.py:637,670` | P2 | P2 | **P2 修文档** | 明确能力边界 |
| P2-3 | `data/jieba_domain_dict.txt` 缺失静默降级 | `scripts/groq_word_adapter.py:132` / `SKILL.md:491` | P2 | P1/P2 | **P2+ 高优** | 补文件 + warning |
| P2-4 | `--full-segment` 未在 SKILL.md 说明 | `scripts/transcribe.py:348` | P2 | P2 | **P2 修文档** | 补 Step 2 |
| P3-1 | `norm_match` 死代码 | `scripts/groq_word_adapter.py:189` | P3 | P3 | **P3 顺手删** | 1 行删除 |
| P3-2 | `_score_gap` `>=` tie-break 偏最右 | `scripts/refine_segments.py:302` | P3 | 待决策 | **P3 待决策，不改** | 先 A/B |
| P3-3 | 测试盲区：`transcribe/cleanup/groq_transcribe/setup` 无覆盖 | `scripts/tests/` | P3 | P3 | **P3 随 P0/P1 同步补** | 3 个新测试文件 |

---

## 2. 修复原则

1. **测试先行**：每个 P0/P1 修复同步补一条 smoke 单测，防止“整条路径未实现”再次漏测。
2. **参数统一不等于效果保证**：P1-1 显式传参是为两路径策略一致，不宣称“准确率提升”。
3. **不扩大行为面**：P1-4 的全局 deoverlap、P2-2 的 JSON 输入协议、P3-2 的 `>=` 改 `>`，均需证据再动。
4. **静默降级必须告警**：P2-3 补 warning。
5. **文档与代码同版本**：涉及 CLI contract 的改动（P1-2 / P2-2 / P2-4）必须同步改 SKILL.md 与 README。

---

## 3. 分阶段计划

### Phase 0 — 测试脚手架（0.5 天）

> 目标：先让“是否跑通”可被 CI 判定，再动核心代码。

**新建 `scripts/tests/test_transcribe.py`**

- mock `mlx_whisper.transcribe` / `faster_whisper.WhisperModel`，验证 `engine=auto/mlx/faster-whisper/groq` 四分支均能跑到 `write_srt` 不抛 `NameError`
- 验证 `--pause-ms` 在 Groq 分支透传到 `refine_groq_segments`（断言 `pause_threshold` 实参）
- 验证非 VAD 分支调用 `mlx_whisper.transcribe` 时带 `logprob_threshold=-1.0, no_speech_threshold=0.6`

**新建 `scripts/tests/test_cleanup_segments.py`**

- 幻觉 4 规则：`_is_repeat_loop / _is_known_hallucination / _is_dense_hallucination / _is_no_speech_hallucination`
- 同文本合并 gap 边界：`gap=0.5s → 合并`、`gap=30s → 不合并`

**新建 `scripts/tests/test_groq_transcribe.py`**

- mock `requests.post` 覆盖 401 / 429 / timeout / ConnectionError 四分支
- 覆盖 `_compress_audio` 的 WAV→MP3 两级降级路径

**验收**：`pytest scripts/tests/ -q` 新增 3 文件可执行，现有 `test_refine.py / test_groq_word_adapter.py` 仍通过。

### Phase 1 — P0 阻断（1 天，二者互锁必须一起）

#### P0-2 `scripts/setup.sh:2,17`

**现状**：单条 `pip install mlx-whisper faster-whisper socksio soundfile`，非 macOS 上 `mlx-whisper` 失败触发 `set -e`，连 `faster-whisper` 也没装上。

**改动**：

```bash
# 通用依赖
venv/bin/pip install --quiet socksio requests jieba==0.42.1
# 平台分支
if [[ "$(uname)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    venv/bin/pip install --quiet mlx-whisper soundfile silero-vad-notorch onnxruntime
else
    venv/bin/pip install --quiet faster-whisper
    # silero/onnx 在非 macOS 不强制，避免额外失败面
fi
```

**验证**：Linux/Windows/GitHub Actions 上 `bash scripts/setup.sh` 零退出，`python -c "from faster_whisper import WhisperModel"` 成功。

#### P0-1 `scripts/transcribe.py:206,468`

**现状**：`seg_func = transcribe_mlx if engine == "mlx" else transcribe_faster` 引用未定义；`engine=auto` 在非 arm64 已正确切到 `faster-whisper`，但整条路径不可达。

**改动**：新增 `transcribe_faster()`，签名对齐 `transcribe_mlx`：

```python
def transcribe_faster(audio_path: str, model_name: str, language: str | None,
                      max_line_length: int, pause_threshold: float,
                      max_line_ms: int, vad: bool = False) -> list[dict]:
```

内部要点：

- `WhisperModel(model_name, download_root=get_model_path(), device="auto")`
- `model.transcribe(audio_path, language=language, word_timestamps=True, vad_filter=vad, log_prob_threshold=-1.0, no_speech_threshold=0.6, initial_prompt=...)`（`initial_prompt` 按 faster-whisper 版本兼容处理）
- 用 `_normalize_segments:72` 统一为 `{start,end,text,words}`，兼容 `Segment.words` 为对象或 dict 两种形态
- 抽公共后处理 `def _postprocess(segments, max_line_length, pause_threshold, max_line_ms)`，让 `transcribe_mlx` 与 `transcribe_faster` 复用 `refine_segs:247` + `cleanup_segs:261` 同一套流水线，避免两份拷贝分叉

**验证**：`test_transcribe.py` 中 faster 分支单测通过；`engine=auto` 在 Linux mock 下产出 SRT。

### Phase 2 — 确定性 P1（0.5 天）

#### P1-2 `scripts/transcribe.py:385,420`

**现状**：CLI 已算好 `pause_threshold = (args.pause_ms/1000) if args.pause_ms is not None else get_pause_threshold(lang)`（385 行），但 Groq 分支硬编码 `pause_threshold=0.15`（420 行），用户传 `--pause-ms 500` 无效。

**改动**：

```python
# Groq 分支调用前
groq_pause = (args.pause_ms / 1000.0) if args.pause_ms is not None else 0.15
# 传入
refine_groq_segments(..., pause_threshold=groq_pause, full_segment=args.full_segment)
```

`refine_groq_segments` 函数默认值保持 `0.3` 不动，调用端默认值 0.15 保留为 Groq 经验值。

**文档同步**：`SKILL.md: Step 2` 高级选项补充“Groq 默认 150ms，本地中文 300ms/英文 500ms，`--pause-ms` 两引擎均生效”。

#### P1-3 `scripts/cleanup_segments.py:144`

**现状**：`if s["text"].strip().lower() == last["text"].strip().lower(): last["end"]=s["end"]` 不看时间，口播重复或静音两侧的同文本会被吞并并拉伸时间轴。

**改动**：

```python
MAX_DUP_MERGE_GAP_S = 1.0
if s["text"].strip().lower() == last["text"].strip().lower() \
   and s.get("start", 0) - last.get("end", 0) <= MAX_DUP_MERGE_GAP_S:
    last["end"] = s["end"]
```

**描述修正**：仅相邻同文本且间隔 ≤1s 时合并（覆盖 VAD 边界重叠）；横跨静音的远距离重复不再合并，避免字幕横跨静音区间。

### Phase 3 — P1-1 降级项 + P2 契约（0.5 天）

#### P1-1 `scripts/transcribe.py:174,195,228`（三处统一）

**现状**：VAD chunk 路径 `transcribe.py:180` 传了 `logprob_threshold=-1.0, no_speech_threshold=0.6`，但非 VAD 整段路径 `228` 与 VAD fallback 全量路径 `195` 未传。README 称“ASR 降噪”通用特性，但两路径策略不一致。

**改动**：三处均显式传 `logprob_threshold=-1.0, no_speech_threshold=0.6`（含 `transcribe_faster`）。README 措辞改为“解码抑制参数（两路径统一显式配置）”，避免“降噪”误导。

**说明**：此为一致性修复，不宣称对短音频准确率有可量化提升；是否真有差异需 runtime 对比，非本次必验。

#### P2-1 `scripts/groq_word_adapter.py:612,639`

**现状**：`_verify_text/_verify_times:612` 已定义且被单测覆盖，但 `refine_groq_segments:639` 主流程未调用，时间单调性无运行时保证。

**改动**：不在每次拆合后插校验，改为 3 个 mutation 出口统一校验：`_split_seg_with_words` 返回后、`_merge_fragments_groq` 后、`_deoverlap` 后。失败回退到 `_fallback_split` 或保留原 segment，并 `print(..., file=sys.stderr)` 警告。函数本身逻辑不改。

#### P2-3 `data/jieba_domain_dict.txt`

**现状**：`SKILL.md:491 / README:104` 列为 Groq 中文必需，`groq_word_adapter.py:132` 静默降级，实际仓库无 `data/` 目录。

**改动**：

- 补 `data/jieba_domain_dict.txt` 并纳入版本控制（若词表涉私则放最小可用示例 + 生成脚本说明）
- `_get_tokenizer:140` 加 `print(f"warning: jieba domain dict not found at {dict_path}, using default dictionary", file=sys.stderr)`
- `SKILL.md` 依赖节标注“未找到则降级为默认词典，会有 warning”

#### P2-2 `SKILL.md:227` / `scripts/refine_segments.py:637`

**现状**：`parse_srt:670` 固定 `words=[]`，`refine:612` 对无 words 段仅做 `_merge_fragments`，独立 `refine_segments.py input.srt` 不能重做评分断句，与 SKILL.md“诊断验证”描述不符。

**改动**：不新增 JSON 输入协议（避免扩大接口面），仅改文档：

> 独立运行 `refine_segments.py <input.srt>` 仅做英文残片合并与空段过滤，不重做 word-level 评分断句；如需验证断句能力，需走 `transcribe.py --export-refined` 的内存流水线。

#### P2-4 `scripts/transcribe.py:348` / `SKILL.md`

**现状**：`--full-segment` 存在，CLI help 已说明“Groq 专用、默认关闭、需验证”，SKILL.md 未提及。

**改动**：`SKILL.md: Step 2` Groq 高级选项补一条：适用场景为怀疑正常段仍有未被 `_should_force_split` 捕获的跨静默合并时临时 A/B 对比，默认关闭，未大规模验证。

### Phase 4 — P3 收尾与待决策（0.5 天）

#### P3-1 `scripts/groq_word_adapter.py:189`

删除死代码 `norm_match = unicodedata.normalize("NFKC", text_no_ws.replace("", ""))`（`replace("", "")` 为 no-op，且变量未被引用）。

#### P1-4 暂缓项（不全局植入 deoverlap）

**现状**：Groq 路径 `_deoverlap:692,703` 已实测修复 279-500ms 重叠；本地 VAD 路径 `transcribe.py:206` 无 deoverlap。本地是先用 Silero VAD 切音频再 `+ offset` 拼接，overlap 来源与 Groq 不同，无证据表明必然重叠。

**处置**：不把 `_deoverlap` 塞进 `cleanup_segments.cleanup()` 全局。改为补 invariant test：

- 构造含 279-500ms overlap 的本地 VAD 模拟输出，验证 `refine→cleanup` 链路是否已重叠
- 构造无 overlap 输入，验证 byte/semantic equivalent（不被误改）

基于实测再决定是否将 `_deoverlap` 提升为共享工具。当前保留 Groq 的两轮调用不变。

#### P3-2 待决策（不直接改 `>=`）

`refine_segments.py:302` 的 `if s >= best_score:` 使同分取最右（更长行），是否符合“人工精校约 12 字/段 8-18 字”需数据判定。保留现状，新增 `scripts/eval_segmentation.py` 的 A/B 任务：同语料下 `>` vs `>=` 的行长分布、段数、人工抽检 20 条，结论写入 `docs/` 再定。

---

## 4. 不做的与为什么不做

| 项 | 不做内容 | 原因 |
|---|---|---|
| P1-4 | 全局在 `cleanup()` 末尾加 `_deoverlap` | 缺乏本地 VAD 实测证据，扩大行为面可能误改时间轴；先测试再定 |
| P2-2 | 让 `refine_segments.py` 支持 JSON 输入以实现“诊断验证” | 扩大 CLI 协议面，收益低；改文档即可 |
| P3-2 | 直接把 `>=` 改 `>` | 策略选择，需 corpus A/B |
| P1-1 | 宣称“传参后准确率提升” | 未做 runtime 对比，仅保证两路径策略一致 |

---

## 5. 风险与回滚

- `transcribe_faster` 最大风险是 `WhisperModel` 返回的 `words` 结构与 `mlx_whisper` 差异，靠 `_normalize_segments:72` 适配层隔离；异常时回退为 faster-whisper 原始 segments 直出，不阻断 SRT 产出。
- `setup.sh` 拆分后，Linux 上 `silero-vad-notorch` 不再强制安装，长音频 VAD 在 Linux 降级为 `faster-whisper` 的 `vad_filter`，与 `SKILL.md: Pipeline` 一致，无回归。
- `cleanup` 加 gap 阈值可能让原本被合并的 VAD 边界重叠残留两条重复字幕，但“重复”比“吞句+拉长时间轴”更易被用户发现和手动去重，是正确取舍。

---

## 6. 验收标准

- [ ] 非 macOS `bash scripts/setup.sh` 零退出，`from faster_whisper import WhisperModel` 成功
- [ ] `engine=faster-whisper` 分支不再 `NameError`，能产出 SRT
- [ ] `transcribe.py --engine groq --pause-ms 500` 实际传入 `refine_groq_segments(pause_threshold=0.5)`（单测断言）
- [ ] 同文本 `gap=0.5s → 合并`、`gap=30s → 不合并`（单测）
- [ ] 缺失 `data/jieba_domain_dict.txt` 时终端有 warning
- [ ] 独立 `refine_segments.py input.srt` 的文档不再误导为“断句诊断”
- [ ] `pytest scripts/tests/ -q` 全绿（含新增 3 文件）

---

## 7. 执行清单（给执行 agent）

1. Phase 0 测试脚手架 → 提交 `test_transcribe / test_cleanup_segments / test_groq_transcribe`
2. Phase 1 P0-2 + P0-1 → 提交 `setup.sh + transcribe.py (+ _postprocess 抽取)`
3. Phase 2 P1-2 + P1-3 → 提交 `transcribe.py + cleanup_segments.py + SKILL.md`
4. Phase 3 P1-1 + P2-1/2/3/4 → 提交 `transcribe.py + groq_word_adapter.py + SKILL.md/README + data/`
5. Phase 4 P3-1 + invariant test + P3-2 评估脚本 → 提交 `groq_word_adapter.py + eval`

---

## 附录：文件清单

- `~/.config/opencode/skills/video-transcribe/SKILL.md`
- `~/.config/opencode/skills/video-transcribe/README.md`
- `~/.config/opencode/skills/video-transcribe/scripts/transcribe.py`
- `~/.config/opencode/skills/video-transcribe/scripts/refine_segments.py`
- `~/.config/opencode/skills/video-transcribe/scripts/cleanup_segments.py`
- `~/.config/opencode/skills/video-transcribe/scripts/groq_word_adapter.py`
- `~/.config/opencode/skills/video-transcribe/scripts/groq_transcribe.py`
- `~/.config/opencode/skills/video-transcribe/scripts/setup.sh`
- `~/.config/opencode/skills/video-transcribe/data/jieba_domain_dict.txt`（待补）
- `~/.config/opencode/skills/video-transcribe/scripts/tests/`（待新增 3 文件）
