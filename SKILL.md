---
name: video-transcribe
description: "将本地音视频转录为 SRT，或翻译已有 SRT；不用于下载 URL 媒体或润色已有字幕。"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Question, Task, Skill, WebSearch]
version: 3.1.0
---

# video-transcribe

## When to Use This Skill

用于本地音频/视频转录、生成 SRT，或按请求翻译已有 SRT。English triggers include `transcribe audio`, `transcribe video`, `generate subtitles`, `generate SRT` and `convert to SRT`. 字幕润色与竖屏重断句交给 `srt-enhancer`。

## Outcome

交付一份通过校验的独立 `.srt`，保存在输入文件同目录，原文件不覆盖。

## Fixed Workflow

Keep this order; preferences must be complete before processing, and approval before writing any enhanced output. 下面 5 步是跨文档的流程，references 用独立编号 Step 0–7.5（0 润色问卷、1 准备音频、2 转录与后处理、3 语义复核、4 润色、5 基线字幕、6 翻译、7 输出、7.5 竖屏）；references 里出现的「回到 Step 3」等指它自己的编号。

1. **Collect preferences before processing.** 本地媒体先读 `references/transcription-workflow.md` 完成开场问卷（原语言、字幕版本、语义复核、润色、引擎）；显式答案直接复用，后续检查点不得重复询问。已有 `.srt` 且要求翻译：跳过环境初始化与转录，把源 SRT 复制到 `tmp/final.srt` 后进入 `references/translation-workflow.md`。已有 `.srt` 且要求润色、清理或竖屏断句：转交 `srt-enhancer`，不走本技能的 SRT 翻译入口。
2. **Prepare engine and transcribe.** 引擎初次设置、缺依赖或切换时读 `references/environment-setup.md`。Groq 走 `references/transcription-groq.md`，本地 Whisper 走 `references/transcription-local.md`；Groq 识别为英文时读 `references/english-segmentation.md`。
3. **Semantic review (optional).** 用户启用时读 `references/semantic-review-workflow.md`，复用转录生成的 words JSON，不重新转录。
4. **Polish or translate per collected settings.** 润色读 `references/polish-workflow.md` 并调用 `srt-enhancer`，复用已收集的混排、领域和括注设置；翻译读 `references/translation-workflow.md` 与对应方向文档。开场选择润色不等于批准未知修改，实际 diff 仍须用户审核。
5. **Output and verify.** 读 `references/output-workflow.md`；用户选择竖屏后再读 `references/vertical-output-workflow.md`，并经技能加载器加载 `srt-enhancer`。

## Non-Negotiable Constraints

- 只处理本地音频、视频或已有 SRT，从不下载 URL 媒体；不输出烧录视频。
- 转录和翻译保留原编号、条目数及时间轴；跨条重排只调整译文落在哪个相邻条目，不合并或拆分条目。
- 清理临时文件等不可逆操作先经用户确认。
- 不在对话、命令或日志中打印 API 密钥。

## Completion Criteria

- 输出独立 `.srt` 到输入文件同目录；编号、条目数、时间轴与基线一致。
- 未经用户审核不写入任何润色修改；润色或翻译互相独立，不因转录请求自动触发。
- 汇报用事实，不用报告腔。

**NO:** “本次转录管线运行流畅，字幕质量显著提升，任务圆满完成。”

**OK:** “66 分钟音频分 3 片转录，247 条字幕；74 处润色经确认应用，3 个低置信词保留原文。”

## On-demand References

| Need | Read |
|---|---|
| 转录本地音视频 | `references/transcription-workflow.md` |
| 引擎初次设置、缺依赖或切换 | `references/environment-setup.md` |
| Groq API 转录 | `references/transcription-groq.md` |
| 本地 Whisper 转录 | `references/transcription-local.md` |
| Groq 英文断句 | `references/english-segmentation.md` |
| 用户启用语义复核 | `references/semantic-review-workflow.md` |
| 用户同意润色 | `references/polish-workflow.md`，并加载 `srt-enhancer` |
| 输出阶段生成竖屏字幕 | 先读 `references/output-workflow.md`；用户选择竖屏后再读 `references/vertical-output-workflow.md`，并通过技能加载器加载 `srt-enhancer` |
| SRT 翻译 | `references/translation-workflow.md`、对应方向文档及 `references/output-workflow.md` |
| 处理失败 | `references/guardrails-and-troubleshooting.md` |
| 排查断句、对齐或清洗算法 | `references/segmentation-internals.md` |
