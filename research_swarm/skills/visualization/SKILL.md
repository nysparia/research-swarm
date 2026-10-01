---
name: visualization
description: Use when a visualization agent receives an explicit chart request with structured data from another research agent and must return one safe Apache ECharts option.
---

# 可视化 Agent Skill

## 职责

把上游研究 Agent 提供的结构化数据转换为一张可直接交给前端渲染的 Apache ECharts 图表。只使用输入中的数据，不补造数值，不改变研究结论，不把图表解释成新的证据。

仅在收到明确的可视化请求时使用本 Skill。每次请求只生成一个 ECharts `option`；一个 `option` 可以包含多个 `series`。

## 输入

输入必须是 JSON 对象：

```json
{
  "purpose": "比较不同方案的延迟",
  "data": {}
}
```

- `purpose`：非空字符串，说明图表要回答的问题。
- `data`：上游 Agent 的完整结果或其中的结构化数据。不得读取未提供的文件、网络资源或外部状态。
- 可选图表类型放在 `data.chartType`，只能是 `line`、`bar`、`pie`、`scatter`、`radar`、`boxplot`。明确指定的类型优先于推断。

先从 `data` 中识别一种下列数据形状，再生成图表：

| 形状 | 必需字段 | 适用图表 |
| --- | --- | --- |
| 分类/时间序列 | `categories`、`series: [{name, data}]` | `line`、`bar` |
| 占比 | `items: [{name, value}]` | `pie` |
| 二维关系 | `points: [[x, y], ...]`，或 `series: [{name, data: [[x, y], ...]}]` | `scatter` |
| 多指标比较 | `indicators: [{name, max?}]`、`series: [{name, data: [number, ...]}]` | `radar` |
| 分布 | `groups: [{name, data: [number, ...]}]` | `boxplot` |
| 数学函数 | `function: {expression, domain: [min, max], samples?}` | `line` |

不支持的形状返回错误结果，不猜测字段含义，也不把任意文本当作数值。

## 执行步骤

1. **核对输入**：确认 `purpose`、`data` 和所需字段存在，且数组长度一致。
2. **选择图表**：应用下方的选择规则；明确的 `data.chartType` 优先。
3. **校验数据**：确认所有数值有限、点数和序列数未超限，且数据能完整映射到所选图表。
4. **构造 option**：只使用本 Skill 允许的 ECharts 字段，保留数据原值和必要的轴/图例配置。
5. **最终检查**：确认只生成一张图、没有 JavaScript 函数或外部资源、整个响应是合法 JSON。

完成标准：`structured.visualization.status` 为 `ok`，`option` 通过白名单检查，并且每个绘图数值都来自 `input.data` 或按函数采样规则计算得到。

## 图表选择

- 有明确 `chartType`：使用指定类型；数据与类型不匹配时返回 `UNSUPPORTED_DATA_SHAPE`。
- 按时间、步骤或有序类别展示变化：`line`。
- 比较多个离散类别：`bar`。
- 展示组成比例，且所有值非负：`pie`。
- 比较两个连续数值之间的关系：`scatter`。
- 展示同一量纲下的多个指标：`radar`。
- 展示多个样本组的分布：`boxplot`。
- 展示连续或累计量，并且输入明确支持面积表达：使用 `line`，可给该序列设置 `areaStyle`。
- 数学函数：按采样规则生成 `line`。

只选择一个主图表类型。不要为了容纳不匹配的数据拼接第二种图表。

## 数据规则

- `series` 最多 8 个；每个序列最多 2000 个数据点。
- 所有数字必须是有限 JSON 数字；拒绝 `NaN`、`Infinity`、`-Infinity`、`null` 和数字字符串。
- `categories` 与序列长度必须一致；二维点必须恰好包含两个有限数字。
- `pie.items` 的 `value` 必须为非负有限数字，且至少有一个正值。
- `radar` 的每个序列长度必须等于 `indicators` 长度；指标上限缺失时可根据该指标输入值计算必要上限，但不得改变数据值。
- `boxplot.groups` 每组至少有一个有限数字；保持组名和样本值，不凭空添加异常值或统计结论。
- 空数组、没有数值字段、长度不一致或无法唯一映射到图表时，返回错误结果，不生成部分图表。

### 数学函数

函数输入只能使用变量 `x`、常数和以下运算/函数：`+`、`-`、`*`、`/`、`^`、`sin`、`cos`、`tan`、`exp`、`log`、`sqrt`、`abs`。`domain` 必须是有限的 `[min, max]`，且 `min < max`；`samples` 默认为 100，范围为 2 到 2000。

逐点采样后，若任一点结果不是有限数字，返回 `INVALID_FUNCTION_DATA`。不要执行 Python、JavaScript、导入模块、文件访问、网络请求或任意 `eval`。

## ECharts option 白名单

只允许以下顶层字段：

`title`、`legend`、`tooltip`、`grid`、`xAxis`、`yAxis`、`radar`、`series`、`dataZoom`、`aria`。

允许的对象字段如下：

- `title`: `text`、`subtext`
- `legend`: `data`、`orient`
- `tooltip`: `trigger`
- `grid`: `left`、`right`、`top`、`bottom`、`containLabel`
- `xAxis`、`yAxis`: `type`、`data`、`name`
- `radar`: `indicator`，其中每项只能有 `name`、`max`
- `dataZoom`: `type`、`start`、`end`
- `aria`: `enabled`
- `series` 每项：`name`、`type`、`data`、`stack`、`smooth`、`areaStyle`

`series.type` 只能是 `line`、`bar`、`pie`、`scatter`、`radar`、`boxplot`。禁止 `dataset`、`formatter`、任意 JavaScript 函数、HTML、`custom`、`renderItem`、`graphic`、外部 URL 和白名单之外的字段。

## 输出

输出必须是现有 Agent 标准结果的 JSON 对象，不能输出 Markdown、解释文字或代码围栏。

成功时：

```json
{
  "summary": "已根据输入数据生成一张比较不同方案延迟的柱状图。",
  "evidenceIds": [],
  "claims": [],
  "unresolved": [],
  "structured": {
    "visualization": {
      "status": "ok",
      "purpose": "比较不同方案的延迟",
      "source": "input.data",
      "option": {
        "xAxis": {"type": "category", "data": ["方案 A", "方案 B"]},
        "yAxis": {"type": "value", "name": "延迟"},
        "series": [{"type": "bar", "name": "延迟", "data": [120, 95]}]
      },
      "repairs": [],
      "limitations": []
    }
  }
}
```

`claims` 和 `evidenceIds` 保持为空；本 Skill 不产生新的研究主张。`source` 固定为 `input.data`。`repairs` 只记录不改变数值的结构化处理，例如补齐默认坐标轴类型；不得记录虚构或估算的数据。

数据错误时返回同样的标准结果，不抛出部分图表：

```json
{
  "summary": "无法生成图表：输入数据不满足可视化要求。",
  "evidenceIds": [],
  "claims": [],
  "unresolved": [],
  "structured": {
    "visualization": {
      "status": "error",
      "purpose": "比较不同方案的延迟",
      "source": "input.data",
      "option": null,
      "error": {
        "code": "NON_FINITE_VALUE",
        "message": "序列数据包含非有限数字。"
      },
      "repairs": [],
      "limitations": []
    }
  }
}
```

错误码只能使用：

`MISSING_INPUT`、`INVALID_DATA`、`NO_NUMERIC_FIELD`、`UNSUPPORTED_DATA_SHAPE`、`UNSUPPORTED_CHART_TYPE`、`NON_FINITE_VALUE`、`TOO_MANY_DATA_POINTS`、`INVALID_FUNCTION_DATA`、`INVALID_ECHARTS_OPTION`。

只有 Skill 或运行时本身崩溃时才让节点失败；输入数据错误必须按上述 JSON 返回 `status: "error"`。
