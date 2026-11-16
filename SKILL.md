---
name: video-transcribe
description: "将本地音视频转录为 SRT，或翻译已有 SRT；不用于下载 URL 媒体或润色已有字幕。"
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Question, Task, Skill, WebSearch]
version: 3.0.3
---

# video-transcribe

## When to Use

用于本地音频/视频转录、生成 SRT，或按请求翻译已有 SRT。英文近义请求包括 `transcribe audio`、`transcribe video`、`generate subtitles`、`generate SRT` 和 `convert to SRT`。字幕润色与竖屏重断句交给 `srt-enhancer`。

## 任务路由

- 本地音频或视频：先读取 `references/transcription-workflow.md` 完成开场问卷，再按已收集的语言、输出版本、引擎、语义复核和润色设置处理；已回答的偏好不得在后续检查点重复询问。
- 转录润色：读取开场问卷记录；用户选择润色后读取 `references/polish-workflow.md` 并调用 `srt-enhancer`，复用已收集的设置。
- 翻译：本地媒体流程按开场选定的字幕版本进入；已有 SRT 流程仍只在用户要求“翻译”或“重写”时读取 `references/translation-workflow.md`。两条路由不重复询问已明确的语言或字幕模式。
- 已有 `.srt` 且要求翻译：跳过环境初始化、音频转录和润色，把源 SRT 复制到 `tmp/final.srt`，直接进入翻译流程。
- 已有 `.srt` 且要求润色、清理或竖屏断句：转交 `srt-enhancer`，不走本技能的 SRT 翻译入口。

## 固定流程

本地媒体：开场问卷 → 环境与引擎准备 → 音频处理和转录 → 可选语义复核 → 按预选设置润色/翻译 → 输出 SRT。

已有 SRT 翻译：验证输入 → 判定方向与模式 → 翻译和术语检查 → 输出 SRT。

各阶段的具体操作和检查点见上方对应参考文件。不要跳过用户确认，也不要把不同路由的步骤混在一起。

## 输出与边界

- 只处理本地音频、视频或已有 SRT，不从 URL 下载媒体。
- 本地媒体任务开始处理前，先收齐缺失的字幕偏好；显式提供的答案直接复用。润色选择及其设置传给 `srt-enhancer`，不可重复提问。
- 处理后仍需用户审核实际润色 diff；开场选择润色不等于批准未知修改。
- 普通转录和翻译保留原编号、条目数及时间轴；跨条重排只调整译文落在哪个相邻条目，不合并或拆分条目。
- 输出独立 `.srt` 到输入文件同目录；文件命名和临时文件清理见 `references/output-workflow.md`。
- 不输出烧录视频，不在对话、命令或日志中打印 API 密钥。

## 按需读取

| 任务 | 读取内容 |
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
| 排查断句、对齐或清洗算法 | `references/segmentation-internals.md` 
