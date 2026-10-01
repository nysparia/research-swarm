# 研究工作台后端接口

当前前端保持原有布局。本页描述已实现、供后续界面接入的后端能力。服务仍限本机同源，所有路径以 `/api/tasks/<taskId>` 开头（设置接口除外）。每个任务有独立的需求、工作台 SQLite、引擎和实验文件。原任务数据可直接打开，新工作台投影按需生成。

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

`challenge`、`deepen`、`revise` 返回持久化提案。`revise` 另须提供完整 `replacement`，避免将一条修改指令误写成科学主张。涉及多个分支的报告要明确选择所属 nodeId。质疑启动核查，不把用户质疑当作证伪证据。

提案含 affectedNodeIds、affectedArtifactIds、文档版本和引擎版本。确认使用：

```json
{"expectedRevision":提案的revision,"confirmed":true}
```

POST `/proposals/<id>/apply`（草稿也可 `/draft/apply` 加 proposalId）。上下文过期须重新预览，重复确认同一提案不会重复执行。修改局部 Claim 或已归属的需求只失效受影响依赖；全局约束、增删需求或旧数据无法归属时保守重建整个研究范围，影响预览会明确列出。

## 持久化事件与用量

- GET `/events?after=0&limit=100`：events、nextCursor、more。
- GET `/events/stream`：SSE，支持 Last-Event-ID 或 after；事件名 research，定期心跳，连接约 25 秒后重连。事件来自实际投影和操作记录，客户端按事件 ID 去重。
- GET `/usage`：调用数、失败数、服务商实际返回的输入/输出 token 及分角色统计。缺失 usage 单独计数，实际金额保持未知，不伪造价格或账单。
- POST `/api/settings/deepseek`：`{apiKey?, model?}`，官方地址、复用已存 main Key、显式开启 shared 复核策略。首次界面配置 `/api/setup` 同时验证连接。

shared 策略下未另配置的 judge/redteam 使用 main，提示与上下文隔离，结果标记 `independent=false`、`reviewLevel=same_model`。这不代表独立科学验证。原 independent 策略保留，只有明确单 Key 配置才切换。原始数据、协议、脚本及哈希检查始终保留。

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
