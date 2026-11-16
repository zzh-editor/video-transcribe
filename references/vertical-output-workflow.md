## Step 7.5：竖屏字幕输出

仅当用户在输出阶段选择“先输出竖屏字幕再清理”时读取。先通过当前 Agent 的技能加载机制加载 `srt-enhancer`，再从加载结果确定 skill root；下方脚本路径均相对于该 root，不假设安装目录。

1. 以 `tmp/final.srt` 为输入，逐条生成语义切分计划：每行不超过 12 字，尽量保持 4–12 字，按动宾、主谓、连接词或短语语义边界切；无需拆分的短条目不写入计划。
2. 若 `tmp/raw.srt.words.json` 与源 SRT 条目时间对应，可传给 `--words-json`。代码会在词文本无法和当前字幕对齐时回退到按字数比例估时。
3. 将输出保存为独立的 `<输入文件名>_<输出语种>_竖屏.srt`；双语语种码固定为 `<source_language>_<target_language>`。
4. 逐条验证输出可解析、编号连续、每行不超过 12 字、时间范围合法；去掉分块间格式空白后，输出文本必须与 `tmp/final.srt` 一致。展示 3–5 条样例和文件路径。
5. 所有校验通过后才清理 `tmp/`。计划无效、文本不一致或时间轴异常时保留原 SRT 和 `tmp/`，不要声称竖屏输出成功。

示例命令（在 enhancer skill root 下执行）：

```bash
python3 scripts/vertical.py "<output_dir>/tmp/final.srt" \
  --splits "<output_dir>/tmp/vertical_splits.json" \
  --words-json "<output_dir>/tmp/raw.srt.words.json" \
  -o "<output_dir>/<source>_<output_language>_竖屏.srt"
```

不提供 `--words-json` 时，脚本按 chunk 字数在每条源字幕内部重排时间。不得覆盖常规输出 SRT。
