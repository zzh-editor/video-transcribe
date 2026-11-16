## Groq API 转录

使用 `whisper-large-v3`，请求顶层 segments 和字符级 words。密钥从 `GROQ_API_KEY` 环境变量读取，不从 Agent 客户端的用户目录读取。

### 准备音频

Groq 支持且不超过 25MB 的文件原样上传；超限或不支持的格式才转成 16kHz 单声道 Opus/OGG。不要手动有损转码以规避大小限制。

```bash
cp "<input>" "<output_dir>/tmp/audio.src"
```

文件超过 **25MB** 时，`_compress_audio` 按时长推算 16–128kbps 码率并自动压缩；超限时最多减半码率重编 3 次。

### 执行

用户选择自动识别时省略 `--language`；API 返回的识别语种会决定后处理分支和默认断句参数。用户选择英文时沿用 `--language en`。

```bash
"<PYTHON_EXECUTABLE>" scripts/transcribe.py "<output_dir>/tmp/audio.src" \
  --output "<output_dir>/tmp/raw.srt" \
  --language en --max-line-length 60 --max-line-ms 6000 \
  --engine groq
```

启动命令前将密钥作为 `GROQ_API_KEY` 环境变量注入。密钥不得输出到对话、命令回显或日志；使用脚本输出中的 `Python executable` 路径。也可通过 `--groq-api-key` 显式传递，但优先使用环境变量以免密钥进入命令行历史。

### Groq 后处理约束

- API 服务端处理 VAD，不做本地分片。
- 中文 words 为字符级，由 `groq_word_adapter.py` 用 jieba 聚词。
- 中文异常段按 `char_count > max_chars` 或 `duration > max_line_ms` 切分；正常段在词间隙 **≥0.80s** 时强制切分，否则只收缩静默边界。静默边缘在局部能量比低于 0.4 且可收缩至少 0.8s 时调整；首尾 padding 为 0.2s。
- `_deoverlap()` 修正相邻条目重叠，保留 20ms 安全间隙；`_absorb_isolated_crumbs()` 只把无缝衔接的孤立短词吸收到邻段，不跨明显停顿。
- 缺失 words、时间无效或覆盖率不足时按 segment 回退，不能伪造词级时间。
- `--full-segment` 是 Groq 专用调试选项，会对所有 segments 强制评分重切；默认不要启用，A/B 比较时才临时使用并验证。
- 响应形状为 `{segments, words}`；word timestamps 是顶层字符级数据，不嵌在 segment 中。
- 默认导出 `<输出srt>.words.json`；需要自定义路径时才传 `--export-refined`。
- 每次运行还写 `<输出srt>.cleanup.json`，记录清理动作；无动作时内容为 `[]`。
- 英文路径跳过中文密度启发式，保留黑名单和重复环检测；`audio_align.py` 收缩句尾静默。
