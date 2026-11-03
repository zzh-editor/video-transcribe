# Video Transcribe

把本地音频或视频转成可编辑的 SRT 字幕。可选本地模型或 Groq API，转写后还能复核断句、润色字幕，或按请求翻译。

在支持 Agent Skills 的 CLI 中，输入“转录”并附上文件路径即可使用。

## 快速开始

```bash
npx skills@latest install https://github.com/zzh-editor/video-transcribe
```

例如，输入“转录 `lecture.mp4`”。首次使用时选择本地模型或 Groq；之后字幕会输出到视频所在目录。

## 触发词

```text
转录 / 转录音频 / 转录视频 / 转录字幕
transcribe / transcribe audio / transcribe video
generate subtitles / generate srt / convert to srt
```

翻译是可选步骤。只有请求中包含“翻译”或“重写”时才会执行。

## 处理流程

```text
本地音视频 → 选择引擎 → 转录与断句 → 输出 SRT
                              ↓
               可选：语义复核、字幕润色、翻译
```

语义复核可调整小句断点，也可合并被识别器拆开的相邻短句。润色和翻译都按请求执行。

## 功能特性

| 功能 | 说明 |
|------|------|
| 引擎选择 | macOS Apple 芯片使用本地模型；其他平台可用本地模型，也可选 Groq API。 |
| 自动断句 | 根据语音停顿和句意整理字幕；复核时可合并明显接续的短句。 |
| 字幕润色 | 可去口癖、校正识别错误并统一中英文格式；修改会先展示给你确认。 |
| 翻译 | 请求翻译或重写时，按字幕语言选择方向。 |
| 竖屏字幕 | 可在润色后另行生成适合 9:16 视频的短行字幕。 |
| 文件输出 | 独立 SRT 保存在输入文件所在目录，不改动原音视频。 |

## 文件结构

```text
video-transcribe/
├── scripts/      # 转录与字幕处理
├── references/   # 工作流程说明
├── data/         # 中文词典
├── models/       # 本地模型缓存
└── README.md
```

## 依赖

需要 `ffmpeg` 和 Python 3.10 或更新版本。首次运行依赖安装脚本时，会优先使用已有且依赖齐全的 Python；缺少依赖时，在技能目录创建虚拟环境，不修改系统 Python。

macOS/Linux：

```bash
bash scripts/setup.sh --engine groq   # 或 local
```

Windows PowerShell：

```powershell
./scripts/setup.ps1 --engine groq     # 或 local
```

选择 Groq 需要 API Key。选择本地模型时，首次转录会下载模型文件。

润色功能由 [Srt-Enhancer](https://github.com/zzh-editor/Srt-Enhancer) 提供。

## License

[MIT](LICENSE)
