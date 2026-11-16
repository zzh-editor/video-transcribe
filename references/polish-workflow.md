## Step 0: 润色条件问卷

本地媒体流程在转写前已经询问并记录润色选择。到此读取 `transcription_intake.polish`：

- `false`：跳过本文件所有润色步骤，以 `raw.srt` 为基线继续；不要再问是否润色。
- `true`：读取并使用 `transcription_intake.enhancer_preferences`，不要重复询问已回答的混排、术语括注或领域策略。
- 没有 `transcription_intake`：表示不是本地媒体问卷路由，保留下方原有的独立确认流程。

开场问卷中若用户选择润色但当前 Agent 无法加载 `srt-enhancer`，应在开始转录前询问是否安装。用户同意后执行 `npx skills@latest install https://github.com/zzh-editor/Srt-Enhancer`，再用该 Agent 的技能加载机制确认能够加载；不假设安装器的目录布局。安装失败、仍无法加载或用户拒绝时，把 `polish` 记为 `false` 并以 `raw.srt` 为基线继续。

### 独立调用时的确认

仅在没有 `transcription_intake` 时先尝试通过当前 Agent 加载 `srt-enhancer`，再用交互问答询问是否安装或调用润色，沿用原有选项：

- 未安装：询问安装或跳过；用户同意后安装，失败则跳过。
- 已安装：询问调用润色或跳过。
- 用户跳过时，以 `raw.srt` 为基线继续。

## Step 4: [可选] 润色

- 前置条件：`transcription_intake.polish=true`，且当前 Agent 能加载 `srt-enhancer`。
- 读取已加载 skill root 下的 `SKILL.md` 和 `references/caller-mode.md`，传入绝对的 `input_srt`、`output_srt`、`scratch_dir` 与问卷中的 `enhancer_preferences`。脚本路径均相对该 skill root，不推断安装目录。不要再次询问润色偏好。
- enhancer 依自己的流程检测领域、查询术语、运行确定性流水线、展示 diff，并等待用户确认。不得绕过最终审核。
- 读取回复末尾的 `CALLER_STATUS`。只有 `APPROVED` 或 `NO_CHANGES` 才能把 `output_srt` 用作基线；必须校验文件存在、条目数、编号和时间戳与 `raw.srt` 一致。
- `REJECTED`、`FAILED` 或 `INVALID_CALL` 时继续使用 `raw.srt`。拒绝是用户选择，不要报告成执行失败。

## 🔴 CHECKPOINT 🛑 STOP: 润色完成验证

srt-enhancer 执行完毕后，**必须逐项确认以下内容**，任一项未通过则回退到 raw.srt：

| 验证项 | 如何确认 | 未通过处理 |
|--------|---------|-----------|
| 领域与术语步骤已完成 | 领域报告已返回；table-match 项未联网，unmatched 项有校准结果或标记 | 发现缺项时回退到 `raw.srt` 并报告具体阶段 |
| 审核状态有效 | `APPROVED` 或 `NO_CHANGES` | `REJECTED` 时按用户决定使用 raw.srt；`FAILED` / `INVALID_CALL` 时报告原因并回退 |
| 输出文件有效 | `output_srt` 存在，条目数、编号和时间戳与 `raw.srt` 一致 | 回退到 `raw.srt` |

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
