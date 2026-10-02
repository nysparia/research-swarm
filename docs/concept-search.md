# 需求阶段的概念搜索

## 使用方法

概念搜索是需求撰写阶段的默认能力，普通用户无需启用、配置或提供 Tavily Key。用户界面只展示查询进度、来源参考和必要的澄清问题，没有 Tavily 开关、密钥输入框或清除按钮。

部署方在当前状态目录的 config.local.json 中配置 conceptSearch.apiKey；默认路径为 .research-state/config.local.json，与 LLM Key 共用文件但字段独立。使用 --state-dir 时跟随指定目录。只合并这一个字段，保留已有 LLM 配置；实际 Key 不应进入源码或前端资源。也可在服务启动环境中提供 TAVILY_API_KEY。修改部署配置后重启服务，所有任务自动共用该凭据。

非空文件密钥优先于环境变量；文件字段缺失、空白或为 null 时回退到 TAVILY_API_KEY。旧配置中的 enabled 在加载时被忽略并从规范化配置移除，后续保存设置时写回，不会因为旧值为 false 而停用能力。公开 ready 仅表示凭据齐全，不代表连接验证成功。

常见问题，例如「多 Agent 一定比单 Agent 更好吗」，理解相关概念即可直接写需求，结论由后续论文与实验研究取得。遇到不认识的术语时，模型申请理解术语，宿主组合定义查询。例如「jev 相比 LLM 有何优势」只查询 jev 的含义，不查询比较答案、不自动纠正为相似缩写。用户已经提供充分定义时无需搜索。

核心研究对象仍不明确时，系统保存草稿并在对话中询问。请补充全称、定义或明确所指对象后再开始研究。网页匹配不等于已经识别；多义、无结果、接口不可用都不能靠猜测解除阻塞。普通文档编辑或切换模式不会静默清除已存在的核心疑问。非核心背景缺口不阻止研究。

缺少部署凭据或调用失败时，提示「概念搜索服务暂不可用」，不要求普通用户配置密钥。常见问题继续生成需求；不能确定核心研究对象时保留草稿并等待澄清。本地资料核验模式仍保持离线，默认能力不意味着每个问题都联网。

## 处理边界

- 只允许真正进入需求准备的消息触发搜索，包括显式开始下一轮与明确修改需求后返回准备阶段。
- Markdown 自动保存、草稿条目编辑、研究中的产物讨论、研究决策回答与正式研究节点不触发 Tavily。
- 本地资料核验模式不调用模型或 Tavily。概念客户端不注册为研究工具。
- 常见问题维持一次逻辑模型调用；陌生概念最多一批查询，再调用一次需求模型。模型服务自身的格式修复/连接重试仍遵循原有规则。
- 模型必须明确返回动作和概念判断字段；缺少这些字段时保留输入并报错，不将未经判断的草稿标为可开始研究。技术领域中的斜线分隔会规范化为空格，仍拒绝完整比较问题和 URL 查询。
- API 的 answer、原始网页和图片均不请求；返回片段视为不可信数据，不能执行其中指令或把宣传、效果数字写成科研事实。
- 标准库客户端固定使用官方 HTTPS Search 端点，禁止重定向，不接受自定义服务器地址。

固定预算：最多 3 个术语、总计 3 次 HTTP 请求（包括重试）、每次 4 条结果，单请求 socket 超时最多 10 秒，搜索截止预算 30 秒。截止时间在请求及读取阶段检查；已发出的请求受 socket 超时约束。429、短暂网络失败和部分 5xx 至多重试一次，Retry-After 超过剩余预算则停止。配置 basic/general、关闭 auto_parameters，开启 include_usage；缺少 usage 不推测费用。

同任务内成功且非空的响应缓存 24 小时，以术语、领域和查询策略版本区分；失败和过期操作不复用。缓存只复用来源片段，当前语境仍重新解释。搜索和模型调用前后检查任务 token 与文档 revision；过期响应不会覆盖新文档或继续推进旧模型调用。

## API 与存储

GET /api/settings 仅返回 conceptSearch:{provider:"tavily",ready:布尔值}，不返回 enabled、hasKey 或密钥。POST /api/settings 不接受任何 conceptSearch 字段（包括开关、密钥和清除操作）；工作区和旧版服务入口统一拒绝并提示刷新旧界面。正常修改 LLM 设置会保留服务端 Tavily 配置，保存设置不调用 Tavily。

TaskDetail.document.conceptUnderstanding 为可选字段，含 status、revision、blockers，搜索发生时另有 runId；完成结果还含 unresolved 与 resolved。旧任务不补发搜索。status 包括 checking/searching/drafting/ready/needs_clarification/interrupted/failed。前端显示进度，后端 /start 也检查核心 blockers。

GET /api/tasks/{id}/concept-search 只读取 purpose=requirement_understanding、eligibleAsEvidence=false 和 runs。没有通用搜索执行接口。每个运行记录包含输入文档版本、应用版本、查询、状态、来源片段、时间、请求次数、每次响应实际 usage 和是否过期。

记录位于该任务 conversation.json 的 conceptSearchRuns 中；导出时单列 concept-understanding.json。概念参考没有科学 evidence ID，不进入论文库、ClaimGraph、judge/redteam 证据载荷或引用关系。概念 reference ID 仅用于对应片段。引用定义时宿主检查实际参考 ID 和逐字片段，但这不能证明模型理解正确或来源真实。

密钥只保存于本机 config.local.json 或读取环境变量；不发给模型，不进入任务档案、导出或实验子进程。Tavily 收到术语和简短领域提示，所选需求模型收到用户需求及返回的片段。不要分享本机状态目录。隐藏配置入口不改变本机文件的访问权限。

## 验证与已知局限

工程测试：python -X utf8 -m unittest discover -s tests -p '*concept*.py' -v；前端运行 npm run test:frontend 和 npm run build。包括默认可用、私有凭据优先级、旧配置迁移、用户接口拒绝写入、LLM 配置更新保留 Tavily Key、不联网入口、请求预算、澄清门控、跨任务缓存隔离、过期结果、记录导出及提示词中的来源隔离规则。

benchmarks/concept-cases.json 提供 16 条标注样例，覆盖常见概念、刻意陌生术语、多义缩写和用户定义。运行 python -X utf8 scripts/evaluate_concepts.py 默认仅检查/列出样例，不调用服务，结果标为 not_run。显式添加 --live --state-dir .research-state --output work/concept-evaluation.json 才调用已配置主模型，可能产生模型费用；评估器不调用 Tavily。

报告分别记录误搜索、漏搜索、歧义猜测和调用错误，并保留逐例响应。该评估测试概念判定协议，不模拟真实搜索成功率，不证明最终 Markdown 没有语义错误。真实模型可能过度自信或错误消歧；宿主只能约束调用范围、预算与来源锚点，仍需要用实际模型结果持续校准。离线测试不能冒充真实模型或 Tavily 连通性验收。
