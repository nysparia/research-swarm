# macOS 界面转写说明

## 设计依据

本次改造以 Apple 官方 macOS 桌面规范和公开示例图为依据，不沿用项目原有的视觉参考。核对时，Apple 设计资源页提供的是 macOS 27 UI 套件；没有把它误标为“macOS 18”。实现为 React 网页，不是 AppKit 或 SwiftUI 原生程序，也不宣称像素级复现系统的折射渲染。

实际查看的视觉参考包括 [macOS 官方 UI 套件预览](https://developer.apple.com/design/resources/images/thumbnails/Thumbnail-UIKit-macOS27_2x.png) 和 [Finder 工具栏结构图](https://developer.apple.com/tutorials/images/com.apple.HIG/toolbars-mac-window-anatomy@2x.png)。前者用于校准设置表单、标签、开关、字段和分组背景，后者用于校准窗口层级、侧栏、标题位置与分组工具栏。参考图片、Apple 字体和 SF Symbols 文件没有打包进产品。

规范依据为 [Designing for macOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos)、[Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars)、[Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars)、[Split views](https://developer.apple.com/design/human-interface-guidelines/split-views) 和 [Materials](https://developer.apple.com/design/human-interface-guidelines/materials)。

## 官方组件图片对照

本轮进一步进入了 Apple 资源页链接的 [Apple macOS 27 UI Kit / Components](https://www.sketch.com/s/57153a31-3379-4737-8ac6-dbfd6525f052/symbols)，实际查看 Light 分组中的 Buttons、Segmented Controls、Text Fields、Toggles - Switches、Menus、Popovers、Sidebars 与 Titlebars and Toolbars，不只参考规范文字和套件封面。

按库中标注的尺寸，常规按钮、文本框和分段控件使用 24px 高度，主要行动采用 Large 的 28px 高度，早期侧栏按 Medium 32px 校准，最终按用户页面参考统一为 36px 行节奏；常规开关采用 54×24px。中文标签所需宽度自适应；颜色、圆角和阴影按图片目测校准，不冒称导出了完整官方设计变量。库中的液态折射效果根据用户要求以 CSS 毛玻璃替代。

视觉修正包括扁平灰色侧栏选中态、内容区紧凑分段控件、白色内凹字段及蓝色焦点环、胶囊形而非圆形的开关滑块、紧凑设置表单，以及蓝底白字的菜单悬浮选中态。新增 `NativeControls.tsx`，在实际工作区使用可键盘操作的 `MacSegmentedControl` 和 `MacSwitch`；`native-controls.css` 在基础窗口样式之后加载，集中管理按图片校准的控件层。

曾从公开库的 Download Assets 按钮下载导出 ZIP；该文件只含 Library Preview 和指针 SVG，不是包含全部控件的 Sketch 源文档。组件依据来自实际打开、放大和截取的公共组件图。参考下载和官方截图仅作本轮对照，不进入产品运行资源。随项目的 `docs/apple-components.json` 保留各组件路径与观察到的尺寸。

## 最终页面与玻璃层统一

用户最后提供的桌面聊天应用截图成为整体布局参考：大面积白色阅读区、安静的灰阶侧栏、中央问题提示、底部悬浮输入。按明确要求删除顶部流程条，但保留四 part 的业务与页面状态。没有复刻示例中的品牌、系统菜单、假窗口按钮或不存在的功能。

`TaskNavigation.tsx` 重组导航，统一新建、搜索、资料、实验、任务选择和模型设置入口。`GlassChrome.tsx` 提供共享的 GlassButton、GlassGroup 和中性背景延伸层；`glass-system.css` 管理统一材质、边缘、阴影和覆盖范围。模型设置里原有蓝色星形图块改为与入口一致的灰阶设置符号。

工作区切换与产物类型切换共用 `MacSegmentedControl` 的 glass 变体，参考 [Light / Over-glass](https://www.sketch.com/s/57153a31-3379-4737-8ac6-dbfd6525f052/symbols?g=Segmented%2520Controls%252FLight%252FOver-glass)，采用 XL 的 36px 高度；字段等内容区控件保留常规尺寸。继续讨论、导出和返回过程移入同一操作组，不再散落在不同风格的卡片中。

毛玻璃集中于侧栏、悬浮输入、工具栏、菜单和弹层。页面保持中性灰白，没有鲜艳彩色壁纸。输入框测量自身高度并为滚动对话自动预留空间，避免长草稿遮挡消息；菜单和弹层使用统一的 backdrop-filter 材质，减少透明度模式提供实色回退。

## 对应架构

`Workspace.tsx` 继续负责原有任务状态、API、草稿隔离和人工确认流程。四个界面状态仍然是新建、需求整理、研究运行和研究产物；顶部流程提示已按用户要求完全删除，不再显示阶段编号或进度连线。

`WorkspaceChrome.tsx` 提供产品标记、底部悬浮输入首页和工作区切换。工具栏在主窗口顶部按功能分组；侧栏承担任务导航，对话与文档／研究画布构成内容分栏。次级检查、材料、模型配置继续复用已有可访问弹窗与抽屉。

`PaneDivider.tsx` 将 macOS 的细分隔线交互转写成可拖动、可键盘操作的 React 组件；`splitPaneState.ts` 统一计算百分比、最小宽度和最大宽度。分栏尺寸是本窗口的显示偏好，不发送到后端，也不重建文档编辑器。方向键微调，Shift 加方向键扩大步长，Home／End 调整到边界，Enter 或双击恢复默认。

`AppearanceControl.tsx` 提供玻璃染色与减少透明度选项。它们只存在于当前页面内存，不修改模型设置或研究数据。尊重系统的减少动态效果偏好，并在支持时读取初始减少透明度偏好。

`macos.css` 是本次的主要视觉层，替代入口中的 `reference.css` 与 `production.css`，保留共享基础布局及领域组件。采用系统字体栈、冷白内容面、中性半透明侧栏、细描边和有限的蓝色强调。玻璃主要用于导航与浮动控件，研究正文和节点内容不做透镜畸变。采用系统字体回退，不分发 Apple 专有字体。

`ui.tsx` 提供统一按钮、图标、状态和输入框。按钮支持 ref、正确的默认 type、忙碌状态及键盘焦点。聊天输入保留中文输入法保护、Enter 发送、Shift + Enter 换行。Cmd／Ctrl + K 聚焦对话，Cmd／Ctrl + 逗号打开模型设置，Cmd／Ctrl + 反斜杠切换侧栏；文档编辑器和弹层内不抢占这些快捷键。

## 运行与验证

原有启动脚本和后端保持不变。Windows 可按项目原有方式运行 `start-research.cmd`。前端开发使用 `npm ci` 后运行 `npm run dev`；生产构建使用 `npm run build`；状态与分栏计算测试使用 `npm run test:frontend`。

浏览器回归脚本为 `scripts/check_macos_ui.py`。安装 Python Playwright 和 Chromium 后，可在完成生产构建的项目根目录运行 `python scripts/check_macos_ui.py --dist dist --output ./ui-checks`。脚本启动临时静态服务，并拦截所有研究 API，以隔离的合成资料检查界面，不调用真实模型，也不写入真实研究任务。

截图属于实际生产构建的浏览器渲染，不是另做的静态设计稿；其中研究主题、消息、节点和报告均为测试资料，不代表真实科研结果。测试覆盖输入法、消息发送、文档保存与版本参数、节点引用、暂停／继续、工作区切换、导出入口、外观设置、分栏调整、键盘操作和窄屏布局。实际模型连通性与完整科研执行链路不在此次浏览器夹具测试范围内。

Vite 仍提示主 JavaScript 包大于 500 kB；构建可完成，该性能提示没有被隐藏。此次没有引入新的运行时依赖，也没有推送或提交到 GitHub。
