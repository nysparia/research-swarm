# 对话式论文研究工作区 v3

左侧为科研任务列表，新任务从一个输入框开始。自然语言问题形成候选选题和可编辑需求；明确后启动检索与自主研究。工作区以 17 类研究看板和多终端矩阵为默认视图，保留可拖动 3D 图、论文、实验与论点专注视图。节点负责真实工作，终端、曲线、代码和产物均来自本次执行。用户可在选题、方法、实验取舍和论文写作中参与；选择写入后续上下文与审计。新一轮消息返回需求准备。论文阅读和详细溯源是可选详情。
## API

- GET `/api/tasks` → `{tasks:TaskSummary[]}`；POST `/api/tasks` `{}` → TaskDetail（独立空任务）。
- GET `/api/tasks/:id` → TaskDetail；轮询1.5秒，后台运行持久化，不伪造进度。
- POST `/api/tasks/:id/messages` `{text:string}` → TaskDetail。异步生成或更新MD；完成后发起下一轮，运行时先暂停，保留历史结果。用户消息与agent回应持久化。
- POST `/api/tasks/:id/document` `{markdown:string,expectedRevision:number}` → TaskDetail。保存用户版本后异步AI润色，编辑竞争校验；保留原始版本及修改理由。用户结束一段编辑自动保存/润色，不能覆盖未提交键入。
- POST `/api/tasks/:id/start` `{expectedRevision:number}` → TaskDetail。用户显式开始，检索、建图、自主研究自动运行，无需用户读论文或筛选资料；运行时由具体研究取舍产生决策暂停，交付草稿另行人工确认。
- POST `/api/tasks/:id/actions/{pause,resume,retry,impact,intervene}`：沿用engine动作。POST `/api/tasks/:id/deepen` 同原deepen。返回TaskDetail（impact只返回影响对象）。
- GET `/api/tasks/:id/nodes/:nodeId/history` → `{executions:[...]}`（旧API结构在实现核对）。GET `/api/tasks/:id/papers/:paperId/pdf`；GET `/api/tasks/:id/export` → ZIP（完成即可导出，候选不伪造为人工认可）。
- GET/POST `/api/settings` 全局模型设置；POST `/api/provider/test` 测试。配置窗口保留为边栏底部小按钮。

TaskSummary `{id,title,phase,updatedAt,round}`。
TaskDetail `{task:TaskSummary,phase:'empty'|'requirements'|'retrieving'|'researching'|'completed'|'failed',document:{markdown,revision,polishing,polishedFrom:number|null,source:'model'|'local',questions:string[],error:string|null},messages:Message[],state:Snapshot|null,error:string|null,artifacts:{name,kind,url}[],runs:RunSummary[],modelReady:boolean}`。polishedFrom标识AI结果基于哪一个用户版本，前端不能只凭source推断是不是自己的润色。
Message `{id,role:'user'|'assistant'|'system',content,at,kind?:'requirements'|'progress'|'result'}`。RunSummary `{round,at,summary,mode}`。
Snapshot同contract.md，不展示固定9阶段和四检查点。`report.ready=true`为生成完成，`approved`仍false；不能将模型完成等同用户确认。研究内的plan/execute/aggregate仅是节点调度阶段，不是用户向导步骤。

## 3D结构图

真实WebGL/Three.js三维渲染，可旋转/缩放/拖动节点。节点来自state.nodes，连线来自decompose/return/compare和切面父子关系。可先显示检索形成的完整结构，再动态加入AI子任务；不可只有主节点和随机装饰点。鼠标悬浮显示名称、状态、当前动作；点击展开日志/输入/输出/证据、从此节点深入研究。状态颜色简洁，不加光效/粒子/自动旋转。保留可访问节点搜索/列表入口作为补充。

## 专用执行边界

自然语言需求由配置模型产出MD和结构化需求/检索式；未配置模型时明确本地草稿和资料核验，不宣称AI创新。研究开始后模型可在受限预算内继续提出子任务/补充检索，用户只需要阅读结果。每个新任务建立独立原检索程序工作区，不污染原ai-access任务和不同科研任务；已有课题可作为独立迁入任务。

## 论文与看板 API

- `GET /api/tasks/:id` 增加 `paper`：选题、七个版本化章节、建议、用户贡献、待决策、候选论点、真实实验凭据、覆盖及未决问题。
- `POST .../paper/topic` `{topicId, expectedRevision, note}`：选题进入需求；`POST .../paper/budget` `{tier: compact|team|swarm}`：仅准备阶段可调整。
- `POST .../paper/sections/:sectionId` `{markdown, expectedRevision, acceptSuggestion?, suggestionId?}`：乐观并发控制；用户正文不被模型覆盖。采纳建议时同时保留其来源节点、版本与轮次。
- `POST .../paper/decision` `{decisionId, optionIndex, note, expectedRevision}`：必须先查看 `/actions/impact` 返回的状态版本；执行选择后重新验证相关分支。暂停由调度器直接执行，重试不可绕过。
- `POST .../paper/approve` `{expectedRevision, acknowledgeIssues}`：确认草稿版本，绑定对应来源状态；不代表科学结论自动成立。
- `GET .../paper/{manuscript,export}`：研究中也可下载明确标为草稿的 Markdown / ZIP。ZIP 含论文源稿、文献、贡献账本与执行材料。
- `GET .../paper/terminal?execution=ID`：仅登记过的 stdout / stderr，分别最多读取 120 KB 尾部。
- `GET .../paper/preview?path=...`：仅登记的本课题 runs 文件，限制 4 MiB，提供静态图片、代码、CSV / JSON；不运行 HTML 或 SVG。
- `GET .../paper/live-chart?execution=ID`：仅当前未结束、同版本和同轮次的执行可读取其工作目录 `research-dashboard.json`。未改动的历史文件、半写入 JSON 不展示为本次结果；结束后使用凭据中归档的不可变文件。

图表文件格式见 `research_swarm/paper_prompts.py`，只显示脚本实际写入的有限数值，不从模型文字估计或填充缺测。最大 8 张图，每图 6 序列，每序列 500 个点。完整科学作图仍由实验脚本保存 PNG 和原始数据。

大规模预算默认 8 路并发 / 240 任务 / 8 次迭代；实际专业角色按论文问题产生。新用户上下文和 researchChoices 随每次节点执行保存，后来的编辑不会改变历史判断依据。
