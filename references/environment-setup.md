## 环境与模型

运行优先使用调用方提供或执行环境可用的 Python；缺包时才在技能目录创建或复用虚拟环境，不向系统 Python 全局安装包。支持 macOS、Linux 和 Windows。不要推断用户使用的 Agent 客户端。

```
<video-transcribe skill root>/
├── venv/          ← 缺少依赖时创建或复用的虚拟环境
├── models/        ← Whisper 模型缓存
└── scripts/bootstrap.py ← 跨平台依赖检测与安装
```

Python 要求 3.10 或更新版本。依赖缺失时，bootstrap 会检查当前执行环境的 Python，优先复用依赖齐全的运行时，否则修复或创建技能虚拟环境，只安装当前引擎缺少的包。Whisper 模型约 1.6GB，首次使用时自动下载至 `models/` 目录（`HF_HOME` 指向此目录）。

## 引擎配置

可选配置文件路径由 `VIDEO_TRANSCRIBE_CONFIG` 环境变量显式提供。未设置时不读取或写入客户端配置；本次任务所需引擎从问卷答案传入。

- 配置文件只保存非敏感设置，例如 `engine` 和 `groq_model`；不保存 API Key。
- Groq 密钥从 `GROQ_API_KEY` 环境变量获取；没有密钥时在开始上传前安全收集，并只传给当前任务进程。
- 配置文件损坏或 `engine` 不合法时明确报错，不回退到隐式默认值。
- API Key 不得出现在自然语言回复、命令回显或日志中。

## 初始化

收齐偏好后运行所选引擎的初始化：

- macOS/Linux：`bash scripts/setup.sh --engine <engine>`
- Windows PowerShell：`./scripts/setup.ps1 --engine <engine>`

`<engine>` 使用 `groq` 或 `local`。不传 `--engine` 时，只有设置了 `VIDEO_TRANSCRIBE_CONFIG` 才能从该文件读取引擎；否则会报错要求显式选择。使用输出中的 `Python executable` 路径运行后续脚本。如果执行环境没有 Python 3.10+，停止并提示用户安装 Python，不能声称已创建虚拟环境。

## 强制本地模型重新下载

只有用户明确要求重新下载时才执行：

```bash
rm -rf models/
bash scripts/setup.sh --engine local
```

Whisper 模型在首次转录时自动下载至 `models/`。不扫描或迁移用户目录中的其他模型缓存。
