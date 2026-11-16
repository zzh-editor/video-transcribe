## Step 7: 输出

复制到输入文件同目录。文件名中的语言代码使用最终字幕语种，不要误用音频原语言：原文用 `source_language`，仅翻译用 `target_language`，双语用 `source_language_target_language`（顺序固定为原文语种、目标语种，不受字幕上下排列影响）。用户选择自动识别时，优先使用转录引擎报告的语种代码；检测不到时再用 `auto`。

```bash
if [ -f "<output_dir>/tmp/enhanced.srt" ]; then
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<输出语种>_Enhance.srt"
else
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<输出语种>.srt"
fi
```

文件名规则：
- 调用了润色（srt-enhancer）→ `<输入文件名>_<输出语种>_Enhance.srt`
- 未调用润色 → `<输入文件名>_<输出语种>.srt`

告知用户文件路径。

**使用当前 Agent 的交互问答功能确认：**
- header: "清理临时文件？"
- options:
  - label: "是，删除 tmp/ 目录" → description: "清理中间文件（WAV、原始 SRT、润色临时文件等）"
  - label: "否，保留" → description: "保留 tmp/ 目录供调试参考"
  - label: "是，先输出竖屏字幕再清理" → description: "按竖屏流程生成独立字幕，确认文件有效后再删除 tmp/"
- multiple: false

用户选择「是，删除 tmp/ 目录」则执行 `rm -rf "<output_dir>/tmp"`。

用户选择「是，先输出竖屏字幕再清理」则先读取 `references/vertical-output-workflow.md`，竖屏文件校验通过后再删除 `tmp/`。失败时保留 `tmp/` 和原始字幕。
