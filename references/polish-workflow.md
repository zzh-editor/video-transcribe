## 🔴 CHECKPOINT 🛑 STOP: 润色确认

检查 `~/.config/opencode/skills/srt-enhancer/` 是否存在。将结果告知用户。

srt-enhancer **不存在** → **用 Question 工具弹窗询问用户：**
- header: "安装 srt-enhancer？"
- description: "srt-enhancer 未安装，是否自动安装以启用润色功能？"
- options:
  - label: "是，安装" → description: "执行 npx skills@latest install ..."
  - label: "否，跳过" → description: "以 raw.srt 为基线继续"
- multiple: false

用户选「是」→ 执行 `npx skills@latest install https://github.com/zzh-editor/Srt-Enhancer`。安装成功则进入 Step 4，失败则跳过润色。

用户选「否」→ 跳过润色，以 raw.srt 为基线继续。

srt-enhancer **已存在** → **用 Question 工具弹窗询问用户：**
- header: "调用 srt-enhancer 润色？"
- description: "是否对原始 SRT 进行去口癖、ASR 纠错、混排规范化等润色处理？"
- options:
  - label: "是，调用润色" → description: "进入 Step 4 润色流程"
  - label: "否，跳过润色" → description: "以 raw.srt 为基线继续"
- multiple: false

## Step 4: [可选] 润色

srt-enhancer 位于 `~/.config/opencode/skills/srt-enhancer/`，提供去口癖、ASR 纠错、的/得/地修正、标点清理、中英文混排空格规范化。

### 必须执行的子步骤

用 Skill 工具加载 srt-enhancer 技能后，**必须按顺序执行以下子步骤**，不可跳过：

| 子步骤 | srt-enhancer 对应 | 本流程约束 | 说明 |
|--------|-------------------|-----------|------|
| ① 领域检测 | Step 2.5 `domain_scanner.py` | 无 | 自动检测字幕领域（Maya/Python/Gaming/AI-3D 等），为联网搜索提供 `search_context` |
| ② AI 构建 config + 联网校准术语 | Step 3 | 无 | **必须执行联网搜索**：对 correction-table.md 未匹配的术语，用 `"{term}" + "{search_context}"` 联网校准权威写法。生成的 config 和 terminology_overrides **在对话中直接展示**（非 Question 弹窗），征询用户意见 |
| ③ enhance.py 流水线 | Step 4 | 无（srt-enhancer 已不包含断句步骤） | 保留 normalize → terminology → spacing → finalize |
| ④ AI 复核 + 置信度评分 | Step 5-6 | 无 | 书名号标记 + 置信度评分 |
| ⑤ 用户确认 diff | Step 6 | 无 | 组织 diff 审核表**直接在对话中输出**（非 Question 弹窗），然后简短提问确认。用户确认后将增强结果写入 `tmp/enhanced.srt` |

### 不执行的步骤

- **跳过 Step 7「Generate Output File」**：输出由本流程 Step 5-7 接管

### 调用模板

```
加载 srt-enhancer 技能，将 tmp/raw.srt 作为输入。必须执行：
1. 运行 domain_scanner.py 检测领域
2. AI 构建 config 并对未匹配术语执行联网校准
3. 运行 enhance.py
4. AI 复核 + 置信度评分 + diff 审核
5. 用户确认后，将增强结果写入 tmp/enhanced.srt

跳过 Step 7（Generate Output File），返回本流程。
```

## 🔴 CHECKPOINT 🛑 STOP: 润色完成验证

srt-enhancer 执行完毕后，**必须逐项确认以下内容**，任一项未通过则回退到 raw.srt：

| 验证项 | 如何确认 | 未通过处理 |
|--------|---------|-----------|
| 领域检测已执行 | srt-enhancer 报告了检测到的领域（如 `general`/`maya`/`python`） | 重新执行 domain_scanner.py |
| 联网校准已执行 | diff 审核表中有「联网校准」类型的修改项，或术语表已全部匹配无需联网 | 检查 config 中 domain 是否正确，确保 search_context 非空 |
| diff 审核表已展示 | 用户已确认或逐条审核 diff 表 | 跳过润色，以 raw.srt 为基线 |
| tmp/enhanced.srt 存在 | `ls tmp/enhanced.srt` 成功 | 跳过润色，以 raw.srt 为基线 |

确认全部通过后进入 Step 5。

## Step 5: 准备基线字幕

将润色或原始的 SRT 统一为 `tmp/final.srt`：

```bash
if [ -f "tmp/enhanced.srt" ]; then
    cp tmp/enhanced.srt tmp/final.srt
else
    cp tmp/raw.srt tmp/final.srt
fi
```

`tmp/final.srt` 将作为翻译和最终输出的输入。
