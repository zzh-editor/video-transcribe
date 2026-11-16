## 本地媒体转录路由

收到音频或视频后先验证输入。只在 `VIDEO_TRANSCRIBE_CONFIG` 已显式设置时读取该配置文件；否则不推断 Agent 客户端或用户目录，从本次问卷收集引擎设置。所有偏好须在依赖安装、音频处理或 API 上传前收齐，存入当前任务的 `transcription_intake`。用户已经说清的答案直接复用，不重复询问。

### 开场问卷

通过当前 Agent 的问答能力，按顺序一次呈现所有适用的核心选项：

1. 音频原语言：中文 / 英文 / 自动识别 / 用户填写的其他语言。
2. 字幕版本：原语言 / 翻译 / 双语。
3. 语义断句复核：启用 / 跳过。
4. 调用 `srt-enhancer` 润色：启用 / 跳过。
5. 转录引擎：没有显式配置且用户未指定时问；本地 Whisper / Groq API。

条件答案紧接着补问，全部收齐后才开始处理：

- 翻译或双语：问目标语言；双语再问原文与译文排列顺序。
- 选择润色：问混排规则、领域策略和术语括注。自定义规则或指定领域时同轮补问具体值；这些答案传到 `srt-enhancer`，未选择润色时不问。
- 选择 Groq 时只读取 `GROQ_API_KEY` 环境变量。未设置时，`transcribe.py` 从技能目录 `.env` 载入该变量；两处都没有时，在 Agent 会话中请求一次，收到后自动写入技能目录 `.env` 并设为仅当前用户可读写。不要回显密钥。
- 当前 Agent 无法加载 `srt-enhancer`：在转录前询问是否安装。用户同意后运行 `npx skills@latest install https://github.com/zzh-editor/Srt-Enhancer`，再用该 Agent 的技能加载机制验证；不假设安装目录。安装失败、仍无法加载或用户拒绝时把本次 `polish` 设为 false。

```text
transcription_intake = {
  source_language, output_mode, target_language, bilingual_layout,
  semantic_review, polish, enhancer_preferences, engine
}
```

`enhancer_preferences` 的字段和值见 `references/polish-workflow.md`。引擎配置、依赖、模型缓存和重下载规则见 `references/environment-setup.md`；只在需要初始化、修复或切换引擎时读取。

### Step 1：准备音频

根据已选引擎，**只读对应文件**，按其中命令创建 `<output_dir>/tmp`、准备音频并运行转录：

- Groq API → `references/transcription-groq.md`
- 本地 Whisper → `references/transcription-local.md`

### Step 2：转录与自动后处理

运行所选引擎一次。显式语言传给 `--language`；自动识别时省略该参数。Groq 返回的检测语种用于选择后处理路径；自动识别为英文时仍运行英文专用断句。语言为英文且引擎为 Groq 时，读取 `references/english-segmentation.md`。

`transcribe.py` 会按固定顺序执行转录 → refine → cleanup，并生成 `raw.srt`、`raw.srt.words.json` 等 sidecar。不要重复独立运行 refine 或 cleanup；算法细节只在排错时读 `references/segmentation-internals.md`。

### Step 3：语义复核（可选）

读取 `transcription_intake.semantic_review`：false 时跳过；true 时读取并执行 `references/semantic-review-workflow.md`，直接复用转录生成的 words JSON，不重新转录。没有 intake 时先补齐问卷，不在转录后重复询问。
