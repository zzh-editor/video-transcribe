## Step 7: 输出

复制到输入文件同目录：

```bash
if [ -f "<output_dir>/tmp/enhanced.srt" ]; then
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<语言>_Enhance.srt"
else
    cp "<output_dir>/tmp/final.srt" "<输入文件目录>/<输入文件名>_<语言>.srt"
fi
```

文件名规则：
- 调用了润色（srt-enhancer）→ `<输入文件名>_<语言>_Enhance.srt`
- 未调用润色 → `<输入文件名>_<语言>.srt`

告知用户文件路径。

**用 Question 工具弹窗询问用户：**
- header: "清理临时文件？"
- options:
  - label: "是，删除 tmp/ 目录" → description: "清理中间文件（WAV、原始 SRT、润色临时文件等）"
  - label: "否，保留" → description: "保留 tmp/ 目录供调试参考"
  - label: "是，先输出竖屏字幕再清理" → description: "对 tmp/final.srt 调用 srt-enhancer 竖屏管线生成 9:16 竖屏字幕，再删除 tmp/ 目录"
- multiple: false

用户选择「是，删除 tmp/ 目录」则执行 `rm -rf "<output_dir>/tmp"`。

用户选择「是，先输出竖屏字幕再清理」则执行竖屏输出流程，完成后删除 tmp/ 目录。

### Step 7.5: [可选] 输出竖屏字幕

对 `tmp/final.srt` 调用 srt-enhancer 的竖屏管线（见 srt-enhancer「输出竖屏字幕」章节），为 9:16 竖屏视频重断句。竖屏字幕每行 4-12 字、按语义边界断句；若字幕文本与词级时间完全对齐，则按真实词边界分配内部切点，否则回退到按字数比例估时。

**用 Skill 工具加载 srt-enhancer 技能**，对 `tmp/final.srt` 执行：

1. AI 逐条读取文本，生成语义断句计划（每行 ≤12 字、断在语义边界），写入 `tmp/vertical_splits.json`
2. 运行 `scripts/vertical.py tmp/final.srt --splits tmp/vertical_splits.json -o "<输入文件目录>/<输入文件名>_<语言>_竖屏.srt"`
3. 校验输出：断句数增加、每行 ≤12 字、时间轴总跨度不变，并向用户展示 3-5 条断句样例
4. 删除 `tmp/vertical_splits.json`（临时文件）

输出文件名规则：`<输入文件名>_<语言>_竖屏.srt`（润色与否不影响竖屏命名）。完成后执行 `rm -rf "<output_dir>/tmp"`。
