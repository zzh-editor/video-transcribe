## 环境与模型

运行优先使用调用方提供或执行环境可用的 Python；缺包时才在技能目录创建或复用虚拟环境，不向系统 Python 全局安装包。支持 macOS、Linux 和 Windows。不要推断用户使用的 Agent 客户端。

```
<video-transcribe skill root>/
├── venv/          ← 缺少依赖时创建或复用的虚拟环境
├── models/        ← Whisper 模型缓存
└── scripts/bootstrap.py ← 跨平台依赖检测与安装
```

Python 要求 3.10 或更新版本。依赖**全部装进技能目录的虚拟环境**（`venv/`），不依赖也不污染系统 Python 的 site-packages：首次运行时创建并装齐当前引擎所需依赖，之后复用。执行 Python 过新导致可选 VAD 依赖（onnxruntime 等）无匹配 wheel 时会打印诊断并降级为词间隙启发式，用 3.10-3.13 的 Python 重建 venv 可恢复 VAD 防护。Whisper 模型约 1.6GB，首次使用时自动下载至 `models/` 目录（`HF_HOME` 指向此目录）。

## 引擎配置

可选配置文件路径由 `VIDEO_TRANSCRIBE_CONFIG` 环境变量显式提供。未设置时不读取或写入客户端配置；本次任务所需引擎从问卷答案传入。

- 配置文件只保存非敏感设置，例如 `engine` 和 `groq_model`；不保存 API Key。
- Groq 只读取 `GROQ_API_KEY` 环境变量。`transcribe.py` 在变量未设置时从技能目录 `.env` 载入；两处都没有时，在 Agent 会话中请求一次，收到后自动写入 `.env` 并限制文件权限。该文件是明文机密，已加入 `.gitignore`；跨设备复制技能目录时需通过安全渠道携带，不能提交或发布。
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
