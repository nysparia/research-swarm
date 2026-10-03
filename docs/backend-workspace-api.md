# 研究工作台后端接口

Tailwind 前端工作台已接入本页的后端能力。服务仍限本机同源，所有路径以 `/api/tasks/<taskId>` 开头（设置接口除外）。每个任务有独立的需求、工作台 SQLite、引擎和实验文件。原任务数据可直接打开，新工作台投影按需生成。

## Conversation-only v2 契约

新流程的设计边界与验收标准见 [v2 契约与兼容说明](backend-v2-contract.md)。任务创建时显式指定 `workflowVersion: "conversation_only_v2"`；不指定时保持 legacy。已有 legacy 调用不要求增加这些新字段，前端默认流程不变。

v2 沿用 `/api/tasks` 和任务详情、`messages`、`workbench`、`report`、`events`、`events/stream`、`export` 等入口；只有旧接口不能表达研究契约的操作才新增接口。所有任务路径仍以 `/api/tasks/<taskId>` 为前缀。

| 方法与路径 | 契约 |
|---|---|
| POST `/messages` | `{text,intent?,brief?}`；intent 为 `ask`、`revise_scope`、`next_round`。尚无 Run 时默认整理草稿，有 Run 时默认 ask。显式 `brief` 用于无模型时提供精确草稿，仍须来源绑定、校验和确认；ask 不接受 brief。 |
| GET `/brief` | 返回草稿、确认版本、理解/澄清信息。Markdown 只是只读投影。 |
| POST `/brief/confirm` | `{expectedBriefVersion}`；确认具体版本，不自动启动。 |
| POST `/research/start` | `{expectedBriefVersion,requestId}`；只允许当前已确认版本。requestId 同键同参重放，同键异参冲突。 |
| POST `/start` | v2 的兼容启动入口，走同一个启动门；`expectedRevision` 可映射为 Brief 版本，仍须已确认并提供 requestId。legacy 保持旧行为。 |
| GET `/research` | 当前 Run、阶段、执行节点、研究产物及异常信息。 |
| POST `/research/actions` | `{runId,revision,action}`；action 为 pause/resume/retry/cancel，revision 为 Run 版本。副作用结果不明时只有显式 retry 且 `acknowledgeUnknownSideEffects:true` 才允许重试。 |
| GET `/exceptions` | 当前任务的类型化研究异常。 |
| POST `/exceptions/<id>/resolve` | `{runId,revision,runRevision,requestId,optionId}`；revision 为异常版本，runRevision 为运行版本。宿主按异常提供 dismiss/revise_scope/cancel_run；外部条件可修复时另提供 retry。retry 只关闭异常并进入暂停，仍需显式恢复；不包含提权或提交密钥。 |

v2 不允许 `/document`、`/draft`、`/research-choice` 或其他旧写路径绕过契约。旧编辑器仍属于 legacy 界面，不代表 v2 接受 Markdown 写契约。未确认契约时，调用旧 `/start` 也不能启动研究。旧 `/actions/pause|resume|retry|cancel` 可沿用，服务端映射当前 Run 与版本并经过同一动作守卫；不明副作用仍需显式确认。旧消息中的 `startNewRound:true,expectedRevision` 可映射为 next_round，仍检查版本和终态。

报告 `ready:true` 表示可导出，不表示研究要求全部满足；`acceptance.status` 为 met/partial/unmet，`objectiveResults` 给出逐项目标的回答、来源和未满足条件。`allowLocalExperiment/allowReplication` 仅许可验证；明确要求实测或复现结果时使用 `evidencePolicy.experimentRequired/replicationRequired`，且对应 allow 必须开启。否则只在验证规划提出必要方案时执行，不会仅因许可为 true 就强制做实验。

`events` 继续保留 `events/nextCursor/more` 分页形状，并附加 `cursorKind: "domain_event"`；v2 游标属于本任务领域事件，不是 Workbench 产物版本。任务版本不同不影响 URL，但不得跨任务复用游标或混淆版本字段。

执行政策使用 `allowLocalExperiment` 和 `allowCodeInspection` 等明确字段；`allowExperiment` 仅作为输入别名归一化为 `allowLocalExperiment`。当前 v2 尚未开放代码检查能力，`allowCodeInspection:true` 明确返回 `capability_unavailable`，不会静默忽略或声称已检查代码。原始模型输出与未知字段也不能获得执行授权。

### v2 最短调用顺序

1. `POST /api/tasks`，请求 `{ "workflowVersion": "conversation_only_v2" }`，取得 taskId。
2. `POST /api/tasks/<taskId>/messages`，请求 `{ "text": "研究 Jev 的架构与原理，不研究成本，不做本机实验。" }`。理解模型须先配置；未配置时只保留原文并明确澄清项，不执行研究。
3. `GET /api/tasks/<taskId>/brief`，审阅 draft.content、计划和权限，等待 status 为 requirements_ready。下列示例的 1 须替换为实际草稿版本。
4. `POST .../brief/confirm`，请求 `{ "expectedBriefVersion": 1 }`。此时仍没有启动研究。
5. `POST .../research/start`，请求 `{ "expectedBriefVersion": 1, "requestId": "由客户端为本次启动生成的唯一ID" }`。重试同一次启动保持相同 requestId。
6. 使用原有任务详情、`events`/SSE、`report` 和 `export` 查看实际结果。先检查 report.acceptance，而不是仅看 ready 或运行 completed。

v2 当前并行节点上限为 1；多个阶段/审阅角色不代表默认使用不同模型。该流程已提供后端接口，不等于旧界面已具备 Brief 确认按钮；旧前端创建任务仍默认 legacy。

## 模型输出协议与诊断

模型临时传输错误使用结构化 `statusCode/retryAfterSeconds/retryExhausted/nextRetryAt`。408、429、500、502、503、504 及网络超时最多重试三次，默认 5/15/45 秒并附不超过 20% 抖动；遵守 `Retry-After`，累计等待最多 300 秒。JSON 格式纠错仍独立限制一次，同一模型请求含两类恢复最多五次 HTTP 调用。401/403 和参数错误不自动重试。

同一 Settings 中，相同地址与凭据共享接口冷却；429/503 后串行探测，连续两次成功才恢复原并发。等待可取消，暂停或关闭服务后不继续请求；传输重试保留本节点已有工具结果，裁判/红队服务不可用不会转换成科学判定或实验重跑。v2 的模型预算在实际请求前逐次扣除。

节点 `modelWait` 包含 `reason/retryNumber/maxRetries/nextRetryAt/statusCode/kind`（cooldown/probe）；`executionSummary` 包含 `status/running/waitingProvider/failed/blocked/ready/completed`。等待不计入推理运行数；有独立工作时为 partially_blocked，无剩余可执行工作时为 blocked。前端自行计算倒计时，服务器只持久化状态转换。工作区在 researching 和 failed 阶段持续同步，错误消息按节点版本去重。

`POST /actions/retry` 接受 `{nodeId,expectedNodeVersion}`；版本可选以兼容旧调用，新界面总是提交。拒绝过期或重复重试，仅重试该节点及其原执行阶段；聚合复核复用已完成子实验。服务重启后仍等待显式恢复，旧等待状态不自动启动请求；恢复时遵守已保存的最早重试时间。

当前默认工作台新启动的普通研究增加候选选题协议；不迁移历史任务，不改变 `conversation_only_v2`。需求编译保存 `topicMode: explore|direct|delegate` 与逐字引用用户指令的 `topicIntent`，无有效依据时采用 explore。

- `project.researchCycle.topicCandidates`：3–5 个候选，每项包含 `id/title/question/researchGap/rationale/evidenceIds/minimalStudy/feasibility/limitations`。标题、问题和 ID 不可重复，引用必须是非空实际证据；语义差异由提示词约束，格式校验不能证明研究新颖性。
- `project.researchCycle.topicSelection`：`id/nodeId/status` 与确定后的 `mode/selectedTopic/sourceCandidateIds/actor/at`；候选单选保存 `candidateId`，自定义与编辑保存原始 `customText`，编辑另存 `baseCandidateId`。`cycle.topic` 只在确定后提供给后续研究。
- `POST /topic-selection`：`{expectedRevision,mode:'candidate',candidateId}`、`{expectedRevision,mode:'custom',customText}` 或 `{expectedRevision,mode:'edited',baseCandidateId,customText}`，返回 TaskDetail。旧版本、重复提交与无效 ID 被拒绝；确认与恢复调度在引擎同一命令中提交。
- `POST /topic-discussion`：`{expectedRevision,text}`，返回 TaskDetail；数字/明确选择或自定义前缀调用选题操作，候选问题列出已有方向，换一批仅重跑选题节点，其他问题进入携带候选上下文的异步讨论。讨论本身不确认课题。
- explore 完成候选后置为 `stage:topic_selection/status:awaiting_topic`，引擎 `waiting_user`；继续按钮不能跳过选题。direct 只细化用户课题，delegate 在候选中返回 `topicRecommendation:{candidateId,reason}`，由宿主记录代选结果。两者不等待额外确认。
- 自定义/编辑课题保留候选来源关联，但其 `selectedTopic.evidenceIds` 初始为空，后续必须核对旧文献是否适用于新问题；不把原候选证据自动当作新课题证据。所有状态沿用 SQLite 快照和审计持久化。

未绑定 `claimId` 的节点（包括背景、文献、凝练课题）回传阶段字段、来源引用、候选观察与缺口；`structured.evidenceRelations` 必须省略或为空。`claims` 在这些阶段仅表示有来源的候选观察，不是持久化主张的判断。模型误填关系时要求重新表达，保留反证、限制和实际来源，不能静默删除后通过。

绑定主张的节点由宿主指定主张 ID 和版本；关系中省略身份时继承绑定，显式越权或旧版本被拒绝。同一主张版本和证据最多有一条 `support`；正反信息并存须由模型解释为一条 `mixed`，可另附 `qualify + unresolved` 表达条件。不同主张分别判定方向。执行器、引擎接收和独立裁判共用关系校验器。

结构纠错最多两次，计入节点调用预算；错误重复且没有进展时提前停止。纠错返回错误码、字段路径和可独立检查的错误列表，关系冲突附证据 ID、下标与方向。缺少阶段字段时回传该阶段的完整结构示例。纠错不能删除已识别的反证来源引用；内容是否充分、结论是否成立仍由证据复核决定。

GET `/nodes/<编码后的nodeId>/history` 中的新执行记录可附 `diagnosticRef`、`attemptCount`、`validationErrors`。`diagnosticRef` 是任务 `runtime` 下的本地相对路径 `diagnostics/<执行编号摘要>.json`，不作为可下载实验产物开放。文件包含每次执行器/裁判调用的请求、原始响应、模型标识和校验结果；密钥与认证字段脱敏。provider 内部传输/格式重试仍由 provider 管理，此处的 attemptCount 指执行器/裁判可见的调用次数，不是 HTTP 请求数。

诊断记录标记 `eligibleAsEvidence: false`，不进入论文库、证据库或研究结果正文。旧历史没有这些字段，读取时保持兼容，无需迁移。失败、暂停和重试不删除历史诊断；既有研究结果仍按版本管理。

## 草稿、研究范围与结果

| 方法与路径 | 行为 |
|---|---|
| GET `/workbench` | 一次读取 plan、draft、artifacts、report，revision 为事件游标 |
| GET `/plan` | 实际采用的能力组合、模块、理由及来源 |
| POST `/plan` | `{text, expectedRevision}`，研究开始前更新能力建议 |
| GET `/draft` | Markdown 对应的稳定条目 ID、内容、来源、锁定状态及文档版本 |
| POST `/draft` | `{expectedRevision, operations}`，编辑条目；研究中返回影响提案 |
| POST `/draft/proposals` | 同一载荷，始终先预览影响 |
| GET `/artifacts/index` | 当前产物，含 Claim、节点输出、实验、材料及持续更新的报告 |
| GET `/artifact/<编码后的artifactId>?revision=N` | 指定版本；省略 revision 获取最新版本 |
| GET `/artifacts/<编码后的artifactId>/versions` | 该产物的全部已保存版本 |
| GET `/report` | 尚未结束研究也可读取当前报告、证据与缺口 |

草稿操作示例：

```json
{"expectedRevision":3,"operations":[{"op":"update","id":"条目ID","changes":{"content":"仅比较 CPU 延迟，报告原始测量。"}}]}
```

操作支持 `add`（block、可选 afterId）、`update`（id、changes）、`remove`（id）。用户编辑默认锁定，后续模型整理保留其内容。需求整理同时选择 `review`、`investigation`、`reproduction`、`experimentation`、`paper_preparation` 的组合；模型输出经过白名单校验，失败时明确标记为 deterministic 建议。单纯检索或复现论文不会自动开启论文写作。

每项产物有稳定 ID、版本、源节点、Claim 版本、证据位置和依赖。失效及历史产物保留。报告汇集实际状态，缺少证据不会因报告生成而获得支持判断。

## 在产物上询问和改变研究

```json
{"kind":"ask","text":"这个结果有哪些限制？","target":{"artifactId":"claim:实际ID","revision":2,"selection":{"quote":"原文中的选区"}}}
```

POST `/interactions` 后以 GET `/interactions/<id>` 查询回复。`ask` 为异步解释，不改需求、不暂停研究、不调用实验工具。回答记录所依据的产物版本；回复期间产物变化时标记 stale。研究中普通聊天也走这条路径；现有明确的研究决策回答保留原行为。

请求可带 `showInConversation:true` 与 `scope:"overview"|"node"`；节点范围同时指定 `nodeId`。用户消息、助手占位与最终回复持久化到主对话，包含引用版本。询问按任务串行处理，状态包括 running、queued、completed 和 failed，后续询问可以读取先前已完成的讨论。历史版本可询问，修改研究必须使用当前版本。

节点状态投影使用 `node_state:<nodeId>` 稳定 ID，包含该节点的输入、输出、日志、状态与所属关系，不以实时耗时触发版本增长。读取历史任务、工作台或作业日志不会隐式启动研究引擎、领取实验队列或探测 GPU；推进需要显式操作。

POST `/research-choice` 接收 `{decisionId,expectedRevision,optionIndex?,note?}`，校验当前决策身份及引擎版本。完成态新一轮使用 POST `/messages` 的 `{text,startNewRound:true,expectedRevision:文档版本}`；普通产物讨论不触发新轮次。

`challenge`、`deepen`、`revise` 返回持久化提案。`revise` 另须提供完整 `replacement`，避免将一条修改指令误写成科学主张。涉及多个分支的报告要明确选择所属 nodeId。质疑启动核查，不把用户质疑当作证伪证据。

提案含 affectedNodeIds、affectedArtifactIds、文档版本和引擎版本。确认使用：

```json
{"expectedRevision":提案的revision,"confirmed":true}
```

POST `/proposals/<id>/apply`（草稿也可 `/draft/apply` 加 proposalId）。上下文过期须重新预览，重复确认同一提案不会重复执行。修改局部 Claim 或已归属的需求只失效受影响依赖；全局约束、增删需求或旧数据无法归属时保守重建整个研究范围，影响预览会明确列出。

## 持久化事件与用量

- GET /api/tasks/{taskId}/concept-search：需求侧概念参考运行记录，只读、不启动搜索、不创建科学证据。
- 概念搜索为内置能力。部署方通过状态目录 config.local.json 的 conceptSearch.apiKey 或 TAVILY_API_KEY 提供凭据；公开配置仅返回 conceptSearch:{provider,ready}，POST /api/settings 拒绝所有 conceptSearch 修改。任务 document.conceptUnderstanding 含进度、文档版本和核心待澄清项，/start 在核心概念未明确时拒绝启动。详见 [概念搜索](concept-search.md)。

- GET `/events?after=0&limit=100`：events、nextCursor、more。
- GET `/events/stream`：SSE，支持 Last-Event-ID 或 after；事件名 research，定期心跳，连接约 25 秒后重连。事件来自实际投影和操作记录，客户端按事件 ID 去重。
- GET `/usage`：调用数、失败数、服务商实际返回的输入/输出 token 及分角色统计。缺失 usage 单独计数，实际金额保持未知，不伪造价格或账单。
- POST `/api/provider/models/deepseek`：`{apiKey?}` -> `{models:[{id}]}`。固定查询 `https://api.deepseek.com/models`，15 秒超时、响应上限 1 MB；不保存输入密钥、不改变配置、不计入推理用量。只有已保存 main 为官方 OpenAI 兼容端点（根路径或 `/v1`）时允许省略 Key。自定义端点密钥不复用。
- POST `/api/settings/deepseek`：`{apiKey?, model}`。模型必须显式选择并通过当前 Key 的官方模型列表校验，地址固定为官方；已有独立 judge/redteam 连接保留，空角色采用 shared 复核。`/api/setup` 接受相同字段，另做连接测试；测试失败回滚整个配置，留空复用官方 Key 时也执行相同事务。
- POST `/api/settings` 支持 `providerRouting: "shared_main" | "per_role"`。shared_main 下三个角色持续使用 main，但保留各角色原配置和原复核策略；per_role 下恢复角色配置和原策略。旧配置默认 per_role。两种模式中密钥更换仍须通过对应 provider 字段提交。
- GET `/api/settings` 的 `providerConfigurations` 返回各角色保存的脱敏配置，`providers` 返回当前有效连接；`providerRouting` 返回连接模式，`reviewPolicy` 返回当前有效复核策略。前端编辑使用 providerConfigurations，不将继承值保存为独立配置。

shared 策略下未另配置的 judge/redteam 使用 main；shared_main 路由下所有复核角色使用 main。共享时提示与上下文隔离，结果标记 `independent=false`、`reviewLevel=same_model`。这不代表独立科学验证。原始数据、协议、脚本及哈希检查始终保留。

## 实验材料与作业

POST `/materials` 支持以下三种输入之一：

```json
{"name":"data.csv","text":"x,y\n1,2\n"}
```

也可使用 base64，或显式 sourcePath 复制本机文件。每个文件上限 50 MiB；HTTP JSON 请求总上限仍为 2 MiB，大材料使用 sourcePath。固定版本仓库用 `repository:{url,commit}`（完整 40 位 commit），拒绝 URL 中的凭据。拒绝路径穿越、符号链接、Windows junction 及保留名称 metadata.json（大小写均受限）。GET `/materials` 返回不可变材料记录与 SHA-256；用户必须明确导入，模型不能自行选择宿主文件。

GET/POST `/execution-settings` 保存节点执行预算和选中的材料，写入示例：

```json
{"expectedRevision":0,"maxTimeoutSeconds":7200,"materialIds":["实际材料ID"],"resources":{"cpuCores":2,"gpuCount":0}}
```

运行节点时须先暂停再改设置。超时预算范围 1–86400 秒，默认仍为 180 秒；工具实际请求在预算内。GPU 请求核对本机 NVIDIA 检测结果，CPU 数不能超过可用逻辑核。线程参数和 GPU 可见设备是资源请求约束，不是 OS 配额或安全沙箱。

POST `/jobs` 可显式提交作业，蜂群节点的 `python_run` 也使用同一任务队列：

```json
{"code":"from pathlib import Path\nprint(Path('inputs/data.csv').read_text())","timeoutSeconds":600,"materialIds":["实际材料ID"],"resources":{"cpuCores":2,"gpuCount":0},"checkpoint":{"path":"checkpoint.json","resumeCode":"# 显式读取 checkpoint.json 后继续的 Python 代码"}}
```

材料复制到 `inputs/<name>`，同一作业不允许重名输入。同一节点、版本、轮次和执行令牌的连续工具调用共享工作目录，便于先生成数据再分析；新的执行令牌及显式 retry/resume 使用独立目录，续跑只复制声明的输入和已校验断点。每任务一个本机执行队列，依赖安装与执行共享锁。保存实际脚本、stdout/stderr、退出码、环境快照、输入哈希、原始产物和每次尝试的执行凭据。进程退出成功仅表示执行完成；独立提交作业不会自动为 Claim 提供支持证据，节点实验仍经过原有数据核验。节点版本或研究轮次变化后，旧作业卡片标记失效，但真实执行状态和凭据保留。

| 方法与路径 | 行为 |
|---|---|
| GET `/jobs`、`/jobs/<id>` | 队列及每次尝试，包含真实状态和错误 |
| GET `/jobs/<id>/logs?stream=stdout&offset=0&limit=65536` | 按字节游标读取 stdout/stderr |
| GET `/jobs/<id>/files?path=编码后的已登记路径` | 只下载该作业已登记、哈希未变的凭据/脚本/环境/产物 |
| POST `/jobs/<id>/actions` | `{action:"cancel"或"retry"或"resume",expectedRevision:N}` |

`retry` 从头执行，保留旧尝试；`resume` 必须有实际生成且哈希匹配的 checkpoint 和显式 resumeCode。没有断点不能声称续跑。服务正常停止时运行作业记为 interrupted，重启时修复遗留 running 状态；已排队作业会继续，interrupted 作业等待显式处理。全局暂停取消本任务排队和执行中的作业。恢复的旧执行凭据进入历史，不能冒充当前节点有效结果。

脚本日志总量 4 MiB，最多保留 30 个产物、每个 50 MiB。隔离目录、venv 和清理进程不提供恶意代码安全隔离；Windows 使用 Job Object，Linux 使用进程组，刻意脱离进程组的程序不能保证由此管理。

## 兼容与交付边界

旧 task detail 增加 workbench 字段，已有路由和前端保留。最终研究 ZIP 额外包含工作台、局部对话和修改提案，节点执行产物按原有审计导出。独立作业可逐项下载。

工程测试验证真实小进程、数据路径、暂停、断点恢复、版本冲突和证据门；不代表已经完成真实论文复现、训练实验或有效科学贡献。新布局尚未接入这些接口。

2026-10-02 本次工程验收：Windows Python 3.11 全套 407 项，跳过 17 项；WSL Linux Python 3.12 全套 407 项，跳过 1 项，均无失败。既有前端测试 58 项通过。独立复审检查了真实进程连续执行、重试/续跑隔离、启动期间编辑、定向重跑及投影溯源。另通过真实本机 HTTP 完成材料导入、执行求和、日志读取、哈希校验下载和事件回放，未调用付费模型。本轮没有完成真实论文或 GPU 训练验收。
