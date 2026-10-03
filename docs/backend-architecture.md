# 后端结构与阅读入口

后端使用 Python 标准库 HTTP 服务、线程和 SQLite。保持扁平模块，不引入额外服务框架。接口契约见 [后端工作台 API](backend-workspace-api.md)。

## 新契约流程（conversation_only_v2）

新任务默认仍是 legacy；显式选择 v2 后，由以下模块独立执行，不创建旧 Engine，也不使用 researchCycle/researchDecision 控制研究。

| 模块 | 职责 |
|---|---|
| `v2_intent.py` | 对话理解、草稿和确认前计划预览，不执行研究 |
| `v2_contracts.py` | Brief、输出及复现目标校验，统一阶段/工具权限门 |
| `v2_store.py` | 任务级 research.sqlite；草稿、确认、Run、节点、异常、事件和调用账本的事务事实源 |
| `v2_app.py` | API 用例、只读问答、版本绑定与旧入口适配 |
| `v2_scheduler.py` | 显式启动、租约、取消及过期结果保护 |
| `v2_pipeline.py`、`v2_prompts.py` | 阶段职责、证据复核、必要验证和逐目标报告 |
| `v2_tools.py` | 冻结权限下的检索及 Run 独立实验环境，调用级凭据与哈希检查 |
| `v2_exceptions.py` | 宿主受理真正异常；模型的 blocking 字段不构成暂停授权 |
| `v2_projection.py` | 纯 TaskDetail/Workbench 兼容投影，不改变科研事实 |
| `v2_legacy.py`、`scientific_validation.py` | 只读历史导入及新旧流程共用的科学校验 |

Brief 确认后才能创建 Run。`allowLocalExperiment/allowReplication` 是许可，不是执行指令：可选验证需要验证规划明确提出必要性；`evidencePolicy.experimentRequired/replicationRequired` 表示用户明确要求的交付。报告的 `ready` 只表示可导出，`acceptance.status` 单独标明 met/partial/unmet，均不代表外部科学验证。

v2 同 Run 的工具共享受管 venv，同一节点 attempt 连续调用共享工作文件，每次调用独立保留凭据；重试使用新 attempt。结果不明的副作用不自动重放。当前执行上限为一个并行节点，代码检查和材料导入尚未开放，会明确拒绝；不声称已支持任意论文复现或操作系统沙箱。

## 既有模块职责与依赖

| 模块 | 负责什么 |
|---|---|
| `server.py` | HTTP 校验、响应、静态前端与 CLI 启动；保留旧导入入口 |
| `workspace.py` | 多任务生命周期、需求与对话、共享设置、任务运行时装配 |
| `workspace_backend.py` | 草稿、产物问答、影响提案，以及材料/作业接口 |
| `runtime.py` | 单任务 `ResearchApplication`：装配文献库、Runner、Engine，管理检索授权和预算 |
| `engine.py`、`research_cycle.py` | 研究调度、阶段推进、暂停、失效与恢复 |
| `runner.py`、`providers.py` | 节点执行、模型连接与调用计量 |
| `claims.py`、`claim_runtime.py`、`semantic_review.py` | 主张版本、证据关系和复核；展示层不判定科学结论 |
| `library.py`、`sources.py`、`retrieval.py` | 任务文献库、来源隔离与外部检索 |
| `experiment_jobs.py`、`local_tools.py`、`process_scope.py` | 实验队列、工具、执行凭据与进程生命周期；不是安全沙箱 |
| `store.py` | 引擎状态、检查点、节点输出及失效历史 |
| `workbench_projection.py`、`workbench_store.py`、`workspace_events.py` | 展示投影、产物版本、可重放事件和 SSE |
| `exports.py` | 研究结果 ZIP；工作区额外附加需求、对话和交互记录 |

入口是 `python -m research_swarm`，由 `server.main()` 创建 `WorkspaceApplication`。工作区直接依赖运行时与导出模块，不反向依赖 HTTP 模块。`server` 重导出的 `ResearchApplication`、`export_bundle` 等仅为旧调用者兼容保留，新代码应从所属模块导入。

## 三条主路径

- **开始研究**：HTTP 请求交给 `WorkspaceApplication.post()`；检查需求版本后，`_run()` 通过 `_ensure_app()` 准备任务独立文献目录和运行时，再发送 `start-autonomous`。`retrieving` 是启动阶段名，不代表必然先做一次联网预检索。
- **执行节点**：Engine 调用工作区包装后的 Runner。包装负责加入当前计划、执行设置、材料、实验队列和 task/node 用量归属；Runner 执行后由引擎接收结果。共享 Settings、mutation lock 和 runner 包装通过运行时构造参数传入，不在创建后改写 Engine 内部字段。包装每次读取 `app.runner`，保留运行时替换 Runner 的行为。
- **问答与调整**：`ask` 解释指定版本的产物，不推进研究。`revise/challenge/deepen` 先预览影响，确认时复核版本并提交引擎命令；旧产物不会因此消失。重复应用同一提案不会重复执行。

单任务运行时保留默认 `gated` 模式及旧命令入口；当前工作区创建的运行时采用 `autonomous`。配置/恢复读取、线程启动和关闭行为不因模块拆分而改变。

## 状态放在哪里

默认根目录为 `.research-state/`，可通过 `--state-dir` 覆盖。

- 工作区共享：`config.local.json`（敏感配置）、`provider-usage.sqlite`（按任务/节点归属的计量）。
- `tasks/<id>/conversation.json`：对话、需求和历史、计划、交互、提案等用户意图记录。
- `tasks/<id>/runtime/swarm.sqlite`：科研状态及执行历史。
- `tasks/<id>/workbench.sqlite`：展示快照、不可变产物版本和事件。
- `tasks/<id>/source/tasks/<id>/data/paper_research.sqlite`：任务独立文献库，PDF 位于同级 `papers/`。
- `tasks/<id>/runtime/jobs.sqlite3`、`runs/`、`materials/`：实验作业、执行尝试和导入材料。

普通任务详情可只读恢复引擎快照，不创建研究 worker；工作台读取可能同步并持久化展示投影。部分旧历史/PDF 接口仍会经过运行时工厂，不应把所有 GET 都理解为无副作用。

## 不要混用这些版本

| 字段 | 归属与用途 |
|---|---|
| `document.revision` | 需求编辑、开始研究时的并发检查 |
| Engine `state.revision` | 研究状态与影响提案的并发检查 |
| 节点 / Claim `version` | 本次执行或证据关系所属的科学对象版本 |
| `artifact.revision` | 某一展示产物的内容版本；不同于源对象版本 |
| Workbench `revision` | 最近事件 ID，用作重放游标 |
| 任务 `token` | 异步需求整理/启动的代际检查，阻止旧结果覆盖新需求 |

修改代码时保留工作区、运行时和引擎已有锁顺序与事务边界。引擎恢复后保持暂停；实验 `retry` 从头执行，`resume` 必须有真实、已校验的断点。来源检查、模型复核、进程成功和用户确认均不等于科学验证。

## 回归入口

在项目根目录运行 `python -X utf8 -m unittest discover -s tests -v`。结构与装配见 `test_runtime.py`，HTTP 见 `test_server.py`，工作区交互与并发见 `test_workspace_backend.py`、`test_workspace_races.py`；实验和证据规则有各自测试。测试使用临时状态及模型替身，工程通过不代表真实科研效果已验证。
