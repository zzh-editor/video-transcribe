# SRT 字幕翻译规则（游戏留学 · 中译英）

方向：简体中文 → 英文。默认全英、不带中文括注。仅此方向可用；英译中请看姊妹文件 `游戏留学SRT翻译规则.md`。

面向游戏美术 / 3D 制作 / 绑定（Rigging）/ 技术美术 / 游戏留学分享类中文视频，产出忠实、可读、术语准确、符合英文 SRT 规范的英文字幕。本文件是翻译的唯一规范，SKILL.md 不再重复细则。

---

## 〇 硬约束（最高优先级，违反即失败）

- **序号与时间轴** `HH:MM:SS,mmm --> HH:MM:SS,mmm` 原样保留，一字不改；只替换文本行。
- **条目数严格一致**：输入多少条就输出多少条，序号一一对应。禁止合并（多条塞进一条）或拆分（一条拆成多条）。
- **单条最多两行**，折行在语义边界，不在词中截断。单行理想 ≤42 字符（时间短的条更短），超长宁可折两行，不删信息点。
- **不留空字幕**：原文仅为语气词/口癖时，给最贴近的自然英文（`OK`/`So`）或在不丢信息前提下省略，不得留空行。
- 纯文本，不加 Markdown；输入已有的 `<i>` 等标签原样保留；输出 UTF-8。

## 一 跨条组织（核心规则）

中文字幕按语义断句，一句长话常被切到连续多条。**条目数与时间轴是硬约束，各条内的词序不是**：

- 连续几条合起来构成完整意思时，按**英文语序**在几条之间分配内容：允许调换顺序、允许倒装。例如中文「跟着一条非常合理、非常高要求的时间线去规划」→ `follow a very reasonable, very demanding timeline` 可把时间线名词按英文语序分到不同条，不必逐条死守中文词序。
- 允许为语句通顺微调措辞。禁止增删信息点、禁止改变逻辑、禁止动时间轴、禁止增减条数。
- 重排后每条单独显示尽量自足通顺，连续几条合起来整体连贯；避免孤立的名词残肢与悬垂分词。

## 二 术语（英文优先，去中文括注）

- **剥离括注**：输入常见 `术语(中文)` / `中文(术语)`，去括号与中文，只留规范英文；连带粘连助词（`的`/`一个`）一并清理。
- **纠正 ASR 拼写与大小写**：不照搬错误（`jointt→joint`）；专名按官方写法（`Maya`、`PySide6`、`GitHub`）；代码/命令/路径（`polyCube`、`mcn.newJoint`）原样保留，不改大小写。全片同一术语写法统一。
- **留学高频词**：作品集 `portfolio`、演示片段 `reel`/`demoreel`、美术测试 `art test`、录取 `offer`、评改 `critique`、绑定 `rigging`、技术美术 `tech art`、特效 `VFX`、概念设计 `concept art`、管线 `pipeline`、拆解 `breakdown`。院校名输出英文官方名（`Sheridan`、`SCAD`、`Ringling`…）。标题不加书名号（`Overwatch`）。
- **行业术语表**（游戏留学内容必用行业表达，领域挂钩见 §六；下表左列的概念在对应语境一律用「必用」列，禁用右列机翻字面译）：

  | 中文概念 | 必用行业表达 | 机翻常见误区（禁用） |
  |---------|-------------|-------------------|
  | 大厂 | `major studio` / `AAA studio` | `big studio`、`large studio`、`big company` |
  | 招聘方 | `recruiters` / `hiring teams` | `HR department`、笼统的 `HR` |
  | 校招发 offer | `new-grad offer`（正式校招） | `school recruitment offer` |
  | 实习转正 offer | `return offer` | `internship-to-full-time offer` |
  | 校招（求职通道） | `campus recruiting` | 逐字直译后无法解 |
  | 求职 | `job search` | 硬造 `job-hunting` 短语（口语偶用可） |
  | 求职作品集 | `portfolio`（语境已明时不加定语） | `job-hunting portfolio`（冗余） |
  | 求职时间线 | `timeline` | `plan`、`schedule` |
  | 中厂 | `mid-sized studio` | `mid-size company` |
  | 外包 | `freelance` / `freelance work` / `freelance opportunities` | `outsourcing`（指委外业务，非个人接活） |
  | 秋招 / 春招 | `fall recruiting` / `spring recruiting` | `autumn recruiting`、`spring recruitment` |
  | 岗位 / 工种 | `role` | `job track`（行业不这么说） |
  | 正式/专业（作品集语境） | `polished` / `professional` | `formal` |
  | 找到（offer/职位语境） | `land` | `get`、`find` |
  | 长期实习 | `long-term internship` | `very long internship` |
  | 课外利用时间 | `outside class` / `on your own` | `privately`（语义不精确） |
  | 贴合实际（“合理”） | `realistic` | `reasonable`（“讲道理”语境才用） |

- **技术术语精确**：`damping force`（不用 `friction force`）、`stiffness`/`damping` 成对参数、`ODE`（非 PDE）、RK4 是 `general-purpose numerical method`（非 `general solution`）、位移 `displacement`（非 rest/length）、`rest position`、回复力 `restoring force`。公式原样保留且符号准确：`μ` 不写 `mu`、`dt²` 不省平方、如 `F = -k(p-τ) - μV`；变量先定义再使用。

## 三 语言（贴近课堂英文）

- 自然口语化，短顺、一眼可读，不用书面长句。语序按英文习惯调整，不硬套中文流水句。
- **碎片句补全主谓**：中文靠语境省略主谓（「实习经验大于3个月」），英文必须补全成完整句（`Ideally, the internship should last more than three months.`）。不得输出缺主谓的碎片断句（`Internships longer than 3 months.` 这类悬垂名词短语）。
- **连接词自然**：中文「那/然后/其实」→ `so`/`and`/`well`/`actually`，该用就用，不硬换 `therefore`/`thus`；`so` 开句是英文课堂常态。一条内不堆两个连接词；`and` 只表并列，不做机械串联。
- **口癖删、铺垫留**：删占位口癖（那个/就是/怎么说呢）；保留教学铺垫（`you can think of it as`、`what is X`、`So what does this correspond to`），不把课堂讲法删成论文腔。
- **主语明确且保第一人称**：自称一律 `I`，不改 `we` 或抽象主语；指向观众用 `you`，不混用 `we`（「我们应该…」→ `you should`，除非说话人把自己算进去）；避免悬垂分词（`Drawing on…`/`When working with…` 后必须接对主语）。
- **标点**：完整句以句号收尾，问句必须 `?`；不用中文标点，不堆分号/破折号，一条至多一处从句逗号。句首大写，专名按官方大小写。
- 数字、单位、版本号、公式、代码、路径保留原值，不译不改。
- **去冗余**：语境已蕴含的定语删掉（`job-hunting portfolio` → `portfolio`）；堆叠的程度词（`very very`）收敛为一个；`craft`/`stuff` 等填充词删。

## 四 输出

- 输出完整 .srt：全部序号、时间轴、英文文本，结构与输入一一对应。
- 只输出字幕本身，不加前言、总结或元语言。默认全英，不含任何中文字符。

## 五 人工校正原则（源自实际精校样本的校对逻辑）

以下五条是机器译文与人工精校的主要差距来源。翻译时按此校准，译后对照逐条自检：

1. **行业用语优于平实词**：同一概念优先用行业标准说法，不用口语/书面直译。`big studio` → `major studio`；`校招 offer` → `new-grad offer`/`return offer`（按场景）；`HR` → `recruiters`/`hiring teams`（指具体人群时）；`外包` → `freelance work`；`实习转正` → `return offer`。
2. **跨条重排以英文语序为本**：中文断句生成的条目边界不对英文词序设限。连续几条构成完整句时，按英文语序分配内容，各条可调序、可倒装、可移动动词位置，使每条单独自足、合起来连贯，不留孤立名词残肢或悬垂分词。示例：中文「你想去的游戏工作室项目组里面，有哪一些工种是合适你的」两行，精校为 `You should figure out which roles` / `on the game teams you want to join are the best fit for you.`（动词上移到前条）。
3. **补全碎片句、删冗余**：中文省略主谓的碎片（`小于3个月不算`）译成英文时补主谓，不给悬垂名词短语；语境已蕴含的定语（`job-hunting portfolio` → `portfolio`）、堆叠程度词、填充词（`craft`/`stuff`）删掉。
4. **统一上下文措辞**：同一概念全片用同一英文词（`major studio` 不一会 `big` 一会 `major`）；人称不混（观众 `you`、自称 `I`，不无故 `we`）。隐含逻辑用连接词显式化（`所以只有…才有含金量` → `only … count` / `because`）。
5. **保持口语课堂语气**：保留说话人节奏（`so`/`well` 开句、`right?`/`really`），不翻译腔、不书面化。课堂性铺垫（`you can think of it as`、`So what does this correspond to`）保留而非删成论文句。

## 六 领域挂钩的术语验证（翻译完成后、输出前必做）

术语把关结合 video-transcribe 管线先前检测到的领域（srt-enhancer 的 domain_scanner 输出，如 `gaming`/`maya`/`python`/`ai-3d`/`unreal` 等）：

### 1. 制定译文专属禁用词表

根据检测到的领域，从 §二 行业术语表中选取适用条目，列出一组**本片禁止出现的常见挪用词**。gaming 求职内容至少包含：

```
big studio / big company、large studio、
HR department（指人时）、school recruitment offer、
internship-to-full-time offer、autumn recruiting、
job-hunting portfolio、mid-size company（指中厂时）、
outsourcing（指接外包活时）、job track（指岗位时）、
privately（指课外时）、very long internship、formal portfolio
```

### 2. 逐条扫描译文

- 对译文全文跑禁用词扫描（grep 或逐条目过目），命中即在译文中替换为行业词（`major studio`、`recruiters`、`return offer`、`new-grad offer`、`fall/spring recruiting`、`portfolio`、`mid-sized studio`、`freelance`、`role`、`outside class`、`long-term internship`、`polished professional portfolio`）。
- 术语首用全名、后续可省略（`portfolio` 不必反复 `job-hunting portfolio`）。
- 例句：`so why are long internships at big studios so important?` → `…at major studios…`；`big-studio HR will first pick out a batch of the best students` → `major studios often extend return offers to summer interns first`。

### 3. 不确定时问、不臆造

- 某词是否该用行业说法拿不准（如语境刁钻或领域不在 §二 表中）→ 保持行业通用英文并标注「❗ 术语存疑」放入输出说明，不silently赌一个。
- 领域信息缺位（润色被跳过、未跑 domain_scanner）→ 触发重跑领域检测：对 `tmp/final.srt` 直接执行 srt-enhancer 的 `domain_scanner.py` 取领域，再回第 1 步。

## 附 自检清单

- [ ] 条数与序号 = 输入，时间轴原样，无合并/拆分
- [ ] 单条最多两行，折行在语义边界，未为凑行删信息
- [ ] 跨条重排后连续几条读起来连贯，无孤立残肢、无悬垂分词
- [ ] 无中文字符、无中文括注，术语统一、拼写已纠正
- [ ] 第一人称 I、观众 you 不混，so/and 自然，句号/问号齐全
- [ ] 无碎片句：每条主谓完整，不输出 `Internships longer than 3 months.` 式悬垂短语
- [ ] 术语验证已执行：按领域禁用词表扫描过译文，`big studio`/`job-hunting portfolio`/`privately` 等已替换为行业词