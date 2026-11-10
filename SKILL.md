---
name: video-transcribe
description: "将本地音视频转录为 SRT，或翻译已有 SRT。用于转录、生成字幕和 SRT 翻译；字幕润色与 9:16 竖屏断句交给 srt-enhancer。Also handles transcribe audio/video, generate SRT, and translate SRT."
allowed-tools: [Read, Write, Edit, Bash, Glob, Grep, Question, Task, Skill, WebSearch]
version: 3.0.3
---

# video-transcribe

## 任务路由

- 本地音频或视频：读取 `references/transcription-workflow.md`，按所选引擎完成转录及可选语义断句复核。
- 转录后润色：到原始 SRT 阶段先询问用户；用户同意后读取 `references/polish-workflow.md` 并调用 `srt-enhancer`。
- 翻译：仅在用户要求“翻译”或“重写”时读取 `references/translation-workflow.md`。用户指定语言或双语模式时照做；未指定模式时按其中的检查点询问。
- 已有 `.srt` 且要求翻译：跳过环境初始化、音频转录和润色，把源 SRT 复制到 `tmp/final.srt`，直接进入翻译流程。
- 已有 `.srt` 且要求润色、清理或竖屏断句：转交 `srt-enhancer`，不走本技能的 SRT 翻译入口。

## 固定流程

本地媒体：环境与引擎选择 → 音频处理和转录 → 可选语义复核 → 润色确认 → 按请求决定是否翻译 → 输出 SRT。

已有 SRT 翻译：验证输入 → 判定方向与模式 → 翻译和术语检查 → 输出 SRT。

各阶段的具体操作和检查点见上方对应参考文件。不要跳过用户确认，也不要把不同路由的步骤混在一起。

## 输出与边界

- 只处理本地音频、视频或已有 SRT，不从 URL 下载媒体。
- 普通转录和翻译保留原编号、条目数及时间轴；跨条重排只调整译文落在哪个相邻条目，不合并或拆分条目。
- 输出独立 `.srt` 到输入文件同目录；文件命名和临时文件清理见 `references/output-workflow.md`。
- 不输出烧录视频，不在对话、命令或日志中打印 API 密钥。

## 按需读取

| 任务 | 读取内容 |
|---|---|
| 转录本地音视频 | `references/transcription-workflow.md` |
| 用户同意润色 | `references/polish-workflow.md`，并加载 `srt-enhancer` |
| SRT 翻译 | `references/translation-workflow.md`、对应方向文档及 `references/output-workflow.md` |
| 处理失败 | `references/guardrails-and-troubleshooting.md` |
| 排查断句、对齐或清洗算法 | `references/segmentation-internals.md` 

### 低 Token 语义断句复核与流水线协同说明

- **--compact-llm-view 模式**：生成 splits.json 的同时额外写出 `<output>.llm.txt`，只保留候选段文字、段内切点标号 `[k@char]` 和合并候选，丢弃 boundary 回显与 word 级浮点时间戳。用法：`python scripts/semantic_review.py raw.srt.words.json raw.srt -o tmp/splits.json --compact-llm-view`。复核时只读 `.llm.txt`，把选中标号的 char 填进 splits.json 对应段的 `selected_cuts`；`--apply` 仍需读完整 splits.json。
- **下游协同与单次交接 workflow**：语义断句复核完成后直接以 splits.json 作为唯一交接物，不再重复轮询或回传上下文。注意 splits.json（`{idx: {safe_cuts:[...]}}`）与 Srt-Enhancer `vertical.py --splits`（`{idx: [chunk,...]}`）是两个 schema：交给竖屏断句前需把选定切点转成分块文案，或直接传 `--words-json` 让竖屏脚本自行分块。
