---
description: "载入科研蜂群前端的 UX 审计快照：界面结构、交互流程、视觉系统、缺陷清单"
---

以下是「科研蜂群」（research-swarm）前端 UX 的一次完整审计快照，采集于 2026-10-03，代码基线为 `582c5e2`「合并最新 UI 与契约驱动后端」。

本次任务请求：$ARGUMENTS

## 使用方式

1. 先把下面「缺陷清单」逐条与当前代码核对，确认行号与结论仍然成立；代码可能已变动，**以当前代码为准**，审计仅作线索。
2. 未给出具体请求时，输出这份 UX 的现状摘要，并按缺陷清单给出修复优先级建议，不要直接改代码。
3. 修复时保持本文件「六、可及性」一节列出的行为，不要在清理过程中把已有设计意图一起删掉。
4. 该项目风格偏严谨：空态、证据缺失、未复核等状态都要有专门文案，禁止用 mock 数据或通用占位符填充。

## 一、技术底座

- React 18.3.1 + Ant Design 6 + CodeMirror 6 + react-markdown；无路由，单屏 `Workspace` 按研究阶段切换主体
- 样式几乎全是手写 CSS，Tailwind 仅剩 3 个工具类在用（`mt-7`、`w-full`）与 2 处 `@apply`；`workbench.css` 声明的 `--color-research: #2864eb` 从未被引用。README 宣称的「Tailwind CSS」名不副实
- 样式层叠顺序（后者胜出）：`reset` → `conversation.css` → `workbench.css` → `agentStructure.css` → `markdown-editor.css` → `macos.css` → `native-controls.css` → `glass-system.css`
- 没有桌面外壳：无 Tauri/Electron，仅用 26px 圆角 + 大阴影模拟 macOS 窗口，无红黄绿灯与标题栏拖拽区
- 开发用 `vite --host 127.0.0.1`（4382，代理 `/api` 到 4381）；构建产物输出 `../dist` 并随仓库提交；前端测试 `node --test frontend/tests/*.test.mjs` 全是纯逻辑测试，没有 DOM/组件测试

## 二、四个界面状态（`frontend/src/workbench/Workspace.tsx:161`）

| 状态 | 内容 |
|---|---|
| 入口页 | 「你想研究什么？」+ 780px 对话坞 + 三枚建议 chip（探索问题／梳理文献／复现论文）+ 隐私说明脚注 |
| 准备期 | 左对话 ｜ 右 CodeMirror「研究需求.md」（撤销/查找/预览/1100ms 防抖自动保存）+ 概念理解面板；开始按钮受三重门控（概念待澄清、文档为空、草稿未保存或润色中） |
| 研究期 | 左对话 `clamp(280px, 42%, calc(100% - 321px))` ｜ 右「研究过程 / 研究成果」分段切换 |
| 成果期 | 报告以 820px 纸面渲染；论文草稿仅在真实产物存在时渲染 |

左侧固定 `TaskNavigation`（246px）、顶部 58px `sw-shell-header`；覆盖层包括产物审阅、影响预览、资料库、节点抽屉、论文抽屉、笔记、工具抽屉（1000px）、历史与检查点（520px）。

## 三、核心交互

- **实时更新靠轮询**：`frontend/src/taskApi.ts:68` 每 1.5s 全量重拉整个 `TaskDetail`（消息/节点/产物/主张图/证据/活动/运行），靠单调 `sequence` 丢弃乱序响应；任务列表 10s 一次。`frontend/` 内零 `EventSource`
- 辅助轮询：`Discussion` 气泡 1800ms、`JobLogs` 1800ms（截取末尾 64KB）
- 后端已有可续传事件流：`research_swarm/workspace_events.py`（25s 窗口、5s 心跳、`Last-Event-ID` 游标），前端从未接入
- 主循环：入口输入 → 建任务 → 准备期改需求 → 「开始研究」→ 检索 → 右侧画布长出节点 → 「从这里深入」把节点+版本带入主对话 → deepen 产出 proposal → 「查看调整影响」弹窗（“N 个任务、M 份产物将受影响”，研究推进后按钮失效并提供「重新计算影响」）→ 检查点通知 → 签认（勾选声明 + 签名）→ 成果页 → 导出 ZIP / 开始下一轮
- 右侧「研究过程」是可拖拽缩放的二维 agent 森林图，卡片按研究步骤分型展示实验条件/指标/证据/日志，边分需求下派/结果回传/关联三型，带小地图与节点定位下拉

## 四、视觉系统

- 品牌蓝同时存在三个值：`#007aff`（AntD + `macos.css`）、`#0088ff`（`native-controls.css`）、`#2864eb`（未使用的 Tailwind token）
- 状态色板（`workbench.css:18`）：进行中 `#2765d4`/`#edf3ff`；已完成 `#288362`/`#eef8f3`；失败 `#b85145`/`#fdf0ed`；待确认 `#9d7629`/`#fcf5e7`；失效 `#8d6e77`/`#f4edf0`
- 三层材质是明确的设计规则：半透明导航 / 不透明阅读面 / 悬浮玻璃控件（`macos.css:9-10`）
- 字号阶梯：根 14/1.65、消息 14/1.95、卡片 13/1.45、入口 h1 29px（负字距 -.035em）、报告 h1 25px
- 动效：入场 420ms、面板切换 240ms（透明度 .3 + translateY 4px + scale .994）、运行环 900ms 线性、进度条 1.5s 交替；交互过渡 140/180ms，按下 `scale(.96)`
- **`BlurReveal` 逐字模糊入场**：内容 850ms 按 `min(i*25,240)ms` 错峰，状态 280ms 按 `min(i*12,72)ms`；超过 240 个 token 降级为整块动画；始终渲染一份仅供屏幕阅读器的副本

## 五、状态与反馈

- 空态文案全部定制（如「检索完成后，研究节点会出现在这里」「尚无实测值」「无可定位的证据」），全项目零 mock、零 TODO
- 错误三通道：`ErrorNote`（`role="alert"` + 重试）、单条消息失败就地重试、抽屉内 antd `Alert`；传输层错误给出具体中文文案（超时／无法连接／已提交但读不到回复）
- 状态词表（`workbench/state.ts:61-66`）：已取消／超时／已中断／已失效／等待你确认／已被后续版本替代
- Toast 极少（仅节点抽屉成功失败、检查点通知），其余全部就地展示
- 动效降级：四重 `prefers-reduced-motion` 防护 + JS 取消在途动画；`prefers-reduced-transparency`、`prefers-contrast: more`、`@supports not (backdrop-filter)` 均有处理

## 六、可及性（高于同类，勿破坏）

- 快捷键：`Ctrl+K` 聚焦输入框、`Ctrl+,` 模型设置、`` Ctrl+` `` 折叠侧栏、`Esc` 关闭并归还焦点；弹窗/抽屉打开时全部抑制
- 分隔条是完整 `role="separator"`：左右箭头 2%、Shift 5%、Home/End、Enter 复位、双击复位
- 中文输入法安全（`isComposing` + `keyCode!==229`）；CodeMirror 查找面板文案全汉化
- 全局 3px focus ring；折叠侧栏加 `inert`；画布键盘可平移缩放；`onFocusCapture` 在 Tab 到屏幕外卡片时自动回中
- 响应式：≥1600 加宽、1180 侧栏转浮层、760 单栏切换、430 截断标题；`pointer:coarse` 放大点击区；`env(safe-area-inset-bottom)` 适配底部安全区

## 七、缺陷清单（按优先级）

1. **SSE 已实现但前端零使用** — `research_swarm/workspace_events.py` 有可续传事件流，前端却 1.5s 全量轮询，导致整棵画布每 tick 重渲染，也是 `revealState.ts:9-20` 需要图元身份对账才能避免重播动画的根因。
2. **两个 CSS 文件从未被 import** — `workbench/production.css`（178 行，是 `sw-production` 的唯一定义处）与 `workbench/reference.css`（451 行）均无任何 import。后果：根节点 `sw-production` 类完全无效（约 66 条规则静默失效）；`Discussion` 气泡的 `.sw-local-user/.sw-local-assistant` 无样式。
3. **三处视觉回退** — `macos.css` 的设计被后加载的 `glass-system.css` 覆盖：agent 头像三色（蓝紫青）被压成同一灰色；入口建议 chip 从 12px 白底蓝框降为 10px 无框浅灰（首要引导入口最不显眼）；卡片圆角 14px 掉回 9px。
4. **研究草稿面板永久隐藏** — `Workspace.tsx:189` 用 `.sw-hidden-notebook` 渲染后又被 `macos.css:208` `display:none`，导致目标/边界/验收摘要与同步指示器不可达，`designState.ts:4-15` 每次渲染白算一遍。
5. **死代码** — `Board.tsx:41` → `ResearchBoard.tsx:44`（整个研究看板屏）、`AgentStrip`、`ReportView`、`ResultView`、`ConversationLog`、`BlurReveal` 的 `useLiveMotion`/`BlurChange` 均不可达；`conversation.css` 约 70% 规则对应已不存在的类名。
6. **无 `aria-live`** — `role="status"` 元素靠 React 增删而非原地改文案，1.5s 轮询结果无人播报；屏幕阅读器无法得知节点完成、主张生成、报告产出。agent 卡片本身不可聚焦（`<article>` 无 `tabIndex`）。
7. **无暗色模式** — `macos.css:13` 写死 `color-scheme: light`，全项目无 `prefers-color-scheme`、无 `.dark` 变体。
8. **其他不一致** — 任务不可改名/删除；头像颜色按数组位置而非稳定身份，节点启停会导致同一 agent 换色；导出文件名硬编码 `科研任务-第N轮.zip`；`Composer` 的 `onHistory` 从未传入；`App.tsx:5` 错误边界丢弃错误对象，既不记录也不上报；`sw-return-latest` 用旋转 180° 的纸飞机图标，语义与动作相反。

## 八、附带发现

`.kilo/worktrees/fate-lycra/` 中存在 `docs/apple-components.json`、`docs/ui-verification.json`、`scripts/check_macos_ui.py`，而主工作区没有——上一轮 UI 合并可能有产物留在了 worktree 未并回。