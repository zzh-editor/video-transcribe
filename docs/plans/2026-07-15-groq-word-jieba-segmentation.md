---
title: Groq Word Timestamp and Jieba Segmentation - Plan
date: 2026-07-15
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Groq Word Timestamp and Jieba Segmentation - Plan

## Goal Capsule

改善 Groq `whisper-large-v3` 中文转录中的异常长字幕：使用 Groq 顶层字符级 `words` 提供真实时间坐标，以独立 `jieba.Tokenizer` 和版本化领域词典将字符聚合为词组级 timed units，再仅对超过现有长度或时长限制的 Groq segment 运行断句。正常 Groq segment 保持原样，本地 `whisper-large-v2` 的调用、数据结构、阈值、评分和输出行为保持不变。

成功结果应满足：第 11 类带标点长复句和第 47 类无标点超长句能被拆成可读、时间准确的条目；已有短碎 segment 不被继续拆分；任一 segment 的 word 对齐失败只触发该 segment 的局部回退；拆分前后文字不丢失、不改写。

---

## Product Contract

### Problem Frame

Groq 当前只请求 `segment` granularity，并保留服务端原始 segment 边界。该策略避免了过去把字符级 word timestamp 当作词组直接送入本地评分引擎造成的过碎问题，但无法处理 Groq 偶发的长复句和十秒以上无标点长句。本地模型已返回中文词组级 `words`，其断句行为已经验收，不应因 Groq 改进而变化。

### Actors

- A-1 字幕使用者：需要可读、时间准确且不过碎的中文字幕。
- A-2 技能维护者：需要能够独立调整 Groq 适配逻辑和领域词典，而不承担本地路径回归风险。

### Key Flows

- F-1 Groq 正常响应：同时取得顶层 `segments` 与顶层 `words`，仅异常 segment 经对齐、聚词和评分断句后输出。
- F-2 Groq 正常短 segment：即使存在顶层 `words`，仍原样输出，不进入评分拆分。
- F-3 Groq 局部异常：某个 segment 的 words 缺失、时间无效或文本覆盖不足时，仅该 segment 回退；其他 segment 继续使用 word 时间坐标。
- F-4 本地转录：继续走 `transcribe_mlx`/`transcribe_faster_whisper` 与 `refine()` 现有路径，产出与改动前一致。

### Requirements

- R-1 `scripts/groq_transcribe.py` 必须在一次 `verbose_json` 请求中同时发送 `timestamp_granularities[]=segment` 和 `timestamp_granularities[]=word`，读取响应顶层 `segments` 与顶层 `words`。
- R-2 `segments[].text` 是唯一文字真源；Groq words 只提供时间坐标，不得替换、重写或重新拼接 ASR segment 文本。
- R-3 Groq words 必须使用独立字段和适配入口，不得写入 `segment["words"]`，从数据结构上阻止其进入本地 word 主路径。
- R-4 仅当去空白字符数超过当前 `max_chars`，或 segment 时长超过当前 `max_line_ms` 时，才将 Groq segment 判定为异常并尝试拆分。首版不加入阅读速度或其他会扩大处理面的启发式。
- R-5 中文字符级时间戳必须通过独立 `jieba.Tokenizer`、精确模式和版本化用户词典聚合为词组级 timed units；不得修改 `jieba` 全局 tokenizer 状态。
- R-6 领域词典首版必须覆盖游戏开发、游戏引擎、图形学、技术美术、动画/建模/绑定/材质/特效、编程语言与工程工具、海外留学/申请/作品集/学位院校，以及相关公司、工作室、游戏和软件专名。
- R-7 连续英文、数字及技术标识符必须作为不可从中间断开的整体处理，包括 `Dynamic Hair Pipeline`、`NBA 2K`、`C++` 和 `Unreal Engine`；标点保留自原文并附着到相邻文本单元。
- R-8 words 归属、文本对齐、词组时间投影和断句必须保持时间单调、不重叠，且时间范围不得越出原 segment。
- R-9 每个 segment 独立验证和回退；一个 segment 失败不得使整份字幕失败或迫使其他正常 segment 改变边界。
- R-10 拆分结果按顺序连接并移除 Unicode 空白后，必须与原 segment 做同样规范化后的文本完全一致，包含原有标点和技术标识符。
- R-11 本地转录路径的模型调用、输入数据结构、默认阈值、评分算法、断句边界和输出必须保持不变。

### Acceptance Examples

- AE-1 Groq 请求包含两个同名 granularity 参数，解析结果保留顶层 segment 和 word 两类数据。
- AE-2 “技术美术、实时引擎、动态头发系统、角色创建系统、计算机图形学、作品集、娱乐技术硕士”被词典作为完整术语识别。
- AE-3 segment 标点或空格未出现在 Groq words 中时，对齐仍成功，输出连接后保留 segment 原文。
- AE-4 第 11 类带逗号长复句拆成语义完整条目；第 47 类无标点、约 11.8 秒的超长句使用真实字符时间拆分。
- AE-5 长度和时长均未超限的 Groq segment 完全保持原边界；已有碎片不被再次拆分。
- AE-6 words 缺失、零时长、乱序、低文本覆盖或 segment 边界空洞分别只触发对应 segment 的回退。
- AE-7 固定的本地 words 输入在改动前后得到逐字段相同的 segment 输出。

### Scope Boundaries

本计划包含 Groq 顶层 words 接入、字符与原文对齐、`jieba` 聚词、异常 segment 断句、局部回退、领域词典、依赖、测试和文档更新。

本计划不包含音频能量或 VAD 边界吸附、forced alignment、LLM 断句、局部调用本地 Whisper、Groq VAD 多请求、替换 ASR 模型，也不调整本地评分阈值或算法。首版不以阅读速度作为异常判定条件；如真实样本证明长度和时长不足以覆盖问题，再作为独立变更评估。

---

## Planning Contract

### Architecture Decisions

- D-1 保留 `scripts/refine_segments.py` 作为本地行为所有者，新增 `scripts/groq_word_adapter.py` 作为 Groq 专用防腐层。原因是过去的回归来自把两类 `words` 视为同一结构；独立入口比在 `refine()` 中增加更多条件更安全。
- D-2 `transcribe_groq()` 返回一个简单映射，至少包含 `segments` 和 `words`，而不是把 words 嵌套进每个 segment。当前模块规模不需要新增 dataclass 或公共类型层。
- D-3 顶层 word 首先按与 segment 的最大时间重叠归属；跨边界且重叠相同则以 word 中点归属，中点恰好落在边界时归前一 segment。无重叠且中点不在任何 segment 内的 word 不强行归属，并使相关异常 segment 走局部回退。
- D-4 对齐在规范化字符序列上顺序执行：忽略 Unicode 空白和标点进行匹配，但保存每个规范化字符到 `segment.text` 原始 offset 的映射。对齐只生成时间映射，最终文本始终从原始 offset 切片。
- D-5 对齐有效条件固定为：word 时间均为有限数值且 `start < end`、顺序单调、落在 segment 时间容差内，并且 segment 规范化非标点字符覆盖率至少为 90%。覆盖不足不猜测，直接局部回退。
- D-6 `jieba.Tokenizer` 从 `data/jieba_domain_dict.txt` 加载；词典文件只放 `词语 词频 词性` 有效行，不放注释，以避免依赖未验证的 userdict 注释语法。领域分类和维护规则写入 `README.md`。
- D-7 Groq 聚合结果构造成与现有评分入口兼容的临时 `{word, start, end}` units，仅在 adapter 内调用既有 `_segment_words()`。首版允许导入该私有函数，但不重命名、不抽取共享模块、不修改其实现；这是用有限内部耦合换取本地零行为变更。
- D-8 无 words 或对齐失败时，正常 segment 原样保留；异常 segment 使用现有 `_fallback_split()` 的标点/字符比例逻辑作最后兜底。adapter 只调用既有函数，不改变无 words 本地主路径语义。

### Text and Time Invariants

- 输出文本块必须是 `segment.text` 的有序、不重叠原文切片；words 和 `jieba` 只决定候选边界。
- 标点不参与覆盖率分母，但必须保留在原文切片中；边界上的标点优先附着前一块。
- 一个词组的 `start` 取其首个已对齐字符的 start，`end` 取末个已对齐字符的 end，并裁剪到原 segment 范围。
- 英文/数字/技术标识符保护在 `jieba` 结果之后、评分之前完成；相邻且属于同一原文 token 的单元合并，避免从内部切开。
- 若无法同时满足文字守恒与时间不变量，必须回退，不能输出部分对齐结果。

### Dependency Decision

固定引入 `jieba==0.42.1`。该版本发布时间较早，但仍是 PyPI 最新稳定版，MIT 许可，提供独立 `Tokenizer`、UTF-8 userdict 和带原文 offset 的 `tokenize()`，直接满足本方案。代价是约 19.2 MB 的 source distribution 和新增安装时间；项目现有依赖管理方式是 `scripts/setup.sh` 直接安装，因此首版沿用该方式，不额外引入 requirements 或打包系统。

### Execution Direction

先建立行为基线和失败复现，再改实现。实施开始时先运行现有测试并记录 `scripts/tests/test_refine.py::test_fallback_no_words` 的当前结果；该测试已被发现可能与现有“无 words 保留原 segment”实现不一致，必须先判断是过时断言还是实际基线失败。不得为了让新增 Groq 功能通过而顺手改变本地无 words 行为。

### Assumptions

- Groq `verbose_json` 响应继续按官方契约在顶层提供 `segments` 和 `words`。
- CLI 的 `max_line_length` 与 `max_line_ms` 继续作为 Groq 异常判定和评分限制来源。
- 对齐覆盖率 90% 是保守首值；只能通过静态 fixture 和附件回归样本调整，不能以提高通过率为由容忍文字不一致。
- `scripts/srt_io.py` 是现有未跟踪文件，不属于本计划，不得修改、依赖或删除。

---

## Implementation Units

### IU-1 Capture Groq Segments and Top-Level Words

**Goal:** 在不改变 API 错误处理和音频压缩行为的前提下，正确请求并传递 Groq 顶层 word timestamps。

**Requirements:** R-1, R-2, R-3

**Files:**

- Modify: `scripts/groq_transcribe.py`
- Add: `scripts/tests/fixtures/groq_zh_verbose.json`
- Add/Modify: `scripts/tests/test_groq_word_adapter.py`

**Approach:**

将 multipart 参数从仅 `segment` 扩展为同时提交 `segment` 与 `word`。分别清洗顶层 `segments` 和 `words`：segment 保留 `start`、`end`、`text`；word 只接受非空文本和可转换的有限 `start/end`，但不要在 API 层做跨对象对齐。返回 `{segments, words}`，让调用方显式选择 Groq adapter。保留当前文件大小检查、压缩、超时、HTTP 状态映射和错误文案。

静态 fixture 应来自脱敏后的真实响应形状，至少包含中文字符 words、缺失标点/空格的 words、英文连续文本和一个跨 segment 边界的 word；测试不访问网络，也不读取 API key。请求参数测试通过 mock `requests.post` 捕获 multipart data，验证两个同名 granularity 值均存在。

**Test Scenarios:**

1. 成功响应返回互相独立的 `segments` 与 `words`，words 未嵌入 segment。
2. 请求体同时包含 `segment` 和 `word`，且 `response_format` 仍为 `verbose_json`。
3. 非法或空 word 被过滤，但有效 segment 不受影响。
4. 缺少顶层 `words` 时返回空列表而非整次失败，为后续局部回退保留条件。
5. 现有 401、429、超时、压缩和超限错误行为保持不变。

**Verification:** `scripts/groq_transcribe.py` 的 mock 单元测试通过，fixture 的数据形状与官方顶层结构一致，且 diff 中不存在向 segment 写入 `words` 的逻辑。

### IU-2 Align Characters and Build Timed Jieba Units

**Goal:** 将每个 Groq segment 的原文安全映射到字符时间戳，再聚合为可供评分的词组级 timed units。

**Requirements:** R-2, R-3, R-5, R-6, R-7, R-8, R-10

**Files:**

- Add: `scripts/groq_word_adapter.py`
- Add: `data/jieba_domain_dict.txt`
- Add/Modify: `scripts/tests/test_groq_word_adapter.py`
- Modify fixture: `scripts/tests/fixtures/groq_zh_verbose.json`

**Approach:**

在 adapter 中依次实现小而明确的内部步骤：验证顶层 words；按最大时间重叠和确定性 tie-break 归属 segment；建立忽略空白/标点的原文 offset 映射；顺序对齐 word 字符；计算覆盖率；使用独立 `jieba.Tokenizer` 和 `tokenize()` 获取原文 offsets；将 token offset 投影到首末字符时间；合并连续英文、数字及技术标识符；把边界标点保留在原文切片中。

词典使用统一的高词频起步，词性以 `nz` 为主，仅在确有通用词性时使用更具体标签。首版词条以验收样本和高频领域术语为边界，不追求穷举；每个新增多字词必须有“应整体分词”测试，容易吞并普通短语的词条还要有反例测试。

adapter 对每个 segment 返回成功的 timed units 或明确的不可对齐结果，不抛出会终止整份字幕的内容级异常。依赖缺失、词典文件缺失属于安装错误，应快速失败并给出明确路径，而不是静默降级为不稳定分词。

**Test Scenarios:**

1. 跨 segment 边界 word 按最大重叠归属；相同重叠按中点和前段优先规则稳定归属。
2. Groq words 不含中文标点和中英文空格时仍能对齐，输出原文标点和空格不丢失。
3. 零时长、非有限时间、乱序 words 被判为该 segment 不可对齐。
4. 规范化字符覆盖率低于 90% 时不可对齐；达到阈值且未覆盖部分仅为标点/空白时可继续。
5. “技术美术、实时引擎、动态头发系统、角色创建系统、计算机图形学、作品集、娱乐技术硕士”各自形成完整 token。
6. `Dynamic Hair Pipeline`、`NBA 2K`、`C++`、`Unreal Engine` 不产生内部候选断点。
7. timed units 时间单调、不重叠、不越出原 segment，首末时间来自实际已对齐字符。
8. units 对应的原文切片连接并去 Unicode 空白后与输入完全一致。
9. 词典反例不会把相邻普通词错误合并为未声明的长术语。

**Verification:** adapter 单元测试覆盖所有对齐失败分支、词典术语和文本/时间不变量；测试不依赖网络或全局 `jieba` 状态。

### IU-3 Refine Only Abnormal Groq Segments

**Goal:** 仅改善异常 Groq segment，并以 segment 为单位安全回退，同时锁定本地行为。

**Requirements:** R-4, R-8, R-9, R-10, R-11

**Files:**

- Modify: `scripts/groq_word_adapter.py`
- Modify: `scripts/transcribe.py`
- Modify only if baseline expectation is stale: `scripts/tests/test_refine.py`
- Add/Modify: `scripts/tests/test_groq_word_adapter.py`
- Modify fixture: `scripts/tests/fixtures/groq_zh_verbose.json`

**Approach:**

新增 `refine_groq_segments(segments, words, max_chars, max_line_ms, pause_threshold)`。函数先保留正常 segment；仅对字符数或时长超限者调用 IU-2 的对齐和聚词。对齐成功时，把临时 units 传入现有 `_segment_words()`，但不修改 `_segment_words()`、`_score_gap()` 或任何全局常量。对齐失败时，对异常 segment 调用现有 `_fallback_split()`；正常 segment 即使无 words 也原样保留。最后逐 segment 检查文字守恒和时间不变量，失败则回退原 segment，而不是保留部分结果。

`scripts/transcribe.py` 的 Groq 分支接收 `{segments, words}` 并调用 Groq adapter；本地分支和其 `refine()` 调用保持逐行不变。Groq 后续已有 `_merge_fragments()`/cleanup 责任应维持当前顺序，防止跨段英文碎片修复丢失；具体接线以当前调用顺序为准，不复制清洗逻辑。

实施前为一个固定本地 words 输入保存精确期望输出，实施后做逐字段相等断言。若 `test_fallback_no_words` 基线失败，先根据当前产品契约把测试改名或修正为“本地/通用 refine 无 words 保留 segment”，并在同一提交中说明这是纠正过时测试，不是算法变化；若基线已通过，则不修改该测试。

**Test Scenarios:**

1. 字符数和时长均未超限的 Groq segment 保持对象边界与文本不变。
2. 带逗号的第 11 类长复句使用 timed units 拆成完整子句，连接后文字守恒。
3. 第 47 类无标点、约 11.8 秒的超长句按词组和真实字符时间拆分，不使用纯比例时间作为成功路径。
4. 已有短碎 Groq segment 不因 words 存在而继续拆分。
5. 一个异常 segment 对齐失败时走 `_fallback_split()`，相邻成功或正常 segment 不受影响。
6. 缺 words、segment 边界空洞、乱序和低覆盖分别验证局部回退。
7. 评分结果违反文字或时间不变量时丢弃该 segment 的拆分结果并恢复原 segment。
8. 固定本地 words fixture 经现有 `refine()` 得到与改动前逐字段相同的输出。
9. Groq 现有跨段英文碎片合并和 cleanup 顺序保持有效。

**Verification:** 附件代表性文本 fixture 的异常长段显著缩短、正常段完全不变；全部本地测试通过；`scripts/refine_segments.py` 无行为性 diff。

### IU-4 Package Dependency and Document the Contract

**Goal:** 让新路径可安装、可维护，并使技能文档准确描述 Groq 与本地的不同处理方式。

**Requirements:** R-5, R-6, R-11

**Files:**

- Modify: `scripts/setup.sh`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify if it contains Groq segmentation expectations: `test-prompts.json`

**Approach:**

在现有 pip 安装命令中固定 `jieba==0.42.1`，不引入新的依赖清单。更新文档中的流水线、依赖、Groq 注意事项、断句算法和失败模式：明确 Groq 请求顶层字符 words，只对异常 segment 做独立对齐和聚词；本地继续使用模型原生词组 words 和既有评分；对齐失败按 segment 回退。记录词典位置、有效行格式、首版领域范围和“新增词条必须新增回归样本”的维护规则。

检查 `SKILL.md` 的 `description:`；若仍为中文或已包含中文则不改 description。新增自然语言注释使用中文，不翻译代码、变量名、关键字、路径或技术标识符。仅当 `test-prompts.json` 当前覆盖 Groq 边界行为时更新对应期望，避免无关文案改动。

**Test Scenarios:**

1. 全新环境执行 setup 后可导入固定版本 `jieba` 并加载词典。
2. 词典路径按脚本位置解析，不依赖调用者当前工作目录。
3. README 与 SKILL 对 Groq、本地和回退路径的描述与代码一致。
4. 文档不再声称 Groq 永远保留原始 segment，也不声称 Groq words 位于 `segments[]`。

**Verification:** 在实施环境检查 `venv/bin/python3 -c "import jieba; print(jieba.__version__)"` 输出 `0.42.1`，随后运行完整测试；人工搜索文档中的 Groq、word timestamp 和依赖描述，确认不存在旧契约冲突。

### Sequencing and Dependencies

IU-1 与 IU-2 可在测试 fixture 形状确定后并行开发，但 IU-3 依赖二者完成。IU-4 在 API 和 adapter 契约稳定后更新，避免文档反复。推荐执行顺序为：记录测试基线与本地 characterization fixture，完成 IU-1，完成 IU-2，接入 IU-3，最后完成 IU-4 和全量验证。

---

## Verification Contract

### Automated Verification

主测试命令：

```bash
venv/bin/python3 -m unittest discover -s scripts/tests -p 'test_*.py'
```

实施阶段若当前 venv 尚未安装 `jieba`，先运行 `bash scripts/setup.sh`，再执行测试。测试必须使用静态 Groq JSON fixture 和 HTTP mock，不访问真实 Groq API，不读取或打印 `config.json` 中的 API key。

### Regression Proof

- 改动前记录完整测试结果，明确标注任何既有失败，特别是 `test_fallback_no_words`。
- 使用固定本地 words fixture 对 `refine()` 输出做逐字段 characterization，改动后必须完全一致。
- `scripts/refine_segments.py` 原则上保持无 diff；若实施发现无法调用现有私有入口，应暂停并重新评估，而不是直接抽取或重写评分代码。
- 比较附件代表性场景：异常 Groq 长段被拆、正常 Groq 短段不变、本地结果不变。附件本身不提交仓库；只提交最小脱敏文本和时间 fixture。

### Quality Gates

- 文字守恒：每个被拆 segment 的输出连接后，移除 Unicode 空白与原文相同；标点不得丢失。
- 时间守恒：所有条目 `start < end`、全局单调且不重叠，子条目不越出原 segment。
- 处理面守恒：正常 Groq segment 不变，本地路径不变。
- 故障隔离：每种无效 words 场景只影响对应 segment。
- 领域分词：所有列出的中文术语和英文技术标识符通过正向测试，并至少包含容易错误吞并的反例。
- API 契约：请求两个 granularity，响应从顶层读取 words。

### Manual Review

用同一音频分别比较改动前后的 Groq SRT，并与本地 SRT 参照，重点检查：第 11 至 15 类长复句、第 47 类无标点长句、后半段已有碎片、英文专名边界以及字幕与语音时间同步。人工验收关注语义完整性和不过碎，不要求 Groq 复制本地的具体边界。

---

## Definition of Done

- Groq 在一次请求中取得顶层 `segments` 和 `words`，且 words 从未写入 `segment["words"]`。
- 独立 adapter 完成确定性归属、原文对齐、`jieba` 聚词、英文/标识符保护、异常检测和局部回退。
- 只有超过现有长度或时长限制的 Groq segment 被重新断句，正常 Groq segment 原样保留。
- 所有拆分满足文字与时间不变量；失败只回退对应 segment。
- 领域词典覆盖首版约定领域，并由正向及反例测试约束。
- 本地固定 fixture 输出与改动前逐字段一致，现有测试和新增测试全部通过；任何改前失败已明确记录并单独处置。
- `jieba==0.42.1` 可由 `scripts/setup.sh` 安装，README、SKILL 和相关 test prompts 与实现契约一致。
- 未修改、依赖或删除未跟踪的 `scripts/srt_io.py`。
- 未引入 VAD 吸附、forced alignment、LLM 断句、局部本地 Whisper、Groq 多请求或本地评分重构。
- 调试日志、临时 fixture、实验脚本和其他实施期临时代码已清理，只保留必要的脱敏回归数据。
