import { Suspense, useState } from 'react';
import { Markdown } from '../Markdown';
import { SearchHistory } from '../SearchHistory';
import { BlurText } from '../BlurReveal';
import { ResearchGraph } from '../ConversationParts';
import type { TaskDetail } from '../taskTypes';
import type { VisualNode } from '../graphData';
import type { Artifact, ExperimentJob, ProposalReview } from './types';
import { duration, metricRows, plain, record, safeDownload, shortTime } from './state';
import { Avatar, Badge, Button, Empty, Icon } from './ui';
import { Discussion } from './Discussion';

export interface BoardProps {
  detail: TaskDetail; onProposal: (value: ProposalReview) => void; onInspect: (artifact: Artifact) => void;
  onNode: (node: VisualNode) => void; onPaper: (id: string) => void; onTab: (tab: string) => void;
}
export function AgentStrip({ detail, onNode }: Pick<BoardProps, 'detail' | 'onNode'>) {
  const nodes = (detail.state?.nodes || []).filter(n => n.active).sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running') || String(b.finishedAt || b.startedAt || '').localeCompare(String(a.finishedAt || a.startedAt || ''))).slice(0, 3);
  if (!nodes.length) return null;
  return <div className="sw-agent-strip" aria-label="Agent 当前活动">{nodes.map((node, i) => <button key={node.id} className="sw-agent" onClick={() => onNode({ id: node.id, nodeId: node.id, title: node.title, status: node.status, action: node.role, sourceKind: 'agent', active: node.active })} title={`${node.title}：${node.logs.at(-1)?.message || node.role}`}>
    <Avatar index={i} /><div><span className="sw-agent-bubble"><BlurText kind="status" text={node.logs.at(-1)?.message || node.role} /></span><small><i className={`sw-agent-dot ${node.status}`} />{node.title}</small></div>
  </button>)}</div>;
}
export function MetricDisplay({ metrics }: { metrics: unknown }) {
  const rows = metricRows(metrics);
  const groups = Array.isArray(metrics) ? metrics.map(record) : [];
  const keys = groups.length > 1 ? Object.keys(groups[0]).filter(key => groups.every(row => typeof row[key] === 'number' && Number.isFinite(row[key]))) : [];
  const [metric, setMetric] = useState(''); const selected = keys.includes(metric) ? metric : keys[0];
  const values = groups.map(row => row[selected] as number);
  const lo = Math.min(0, ...values); const hi = Math.max(0, ...values); const span = hi - lo || 1;
  if (!rows.length) return <Empty icon="lab" title="尚无可展示的实测指标">实验记录中的数值会出现在这里。未产出数据时，不生成示意结果。</Empty>;
  return <div className="sw-metrics">{keys.length > 0 ? <><div className="sw-metric-toolbar"><span>记录中的方法 / 样本对照</span><select aria-label="选择实验指标" value={selected} onChange={e => setMetric(e.target.value)}>{keys.map(key => <option key={key}>{key}</option>)}</select></div><div className="sw-bar-chart" aria-label={`实验记录指标：${selected}`}>{groups.slice(0, 12).map((row, i) => { const value = values[i]; const label = plain(row.method) || plain(row.name) || plain(row.label) || `记录 ${i + 1}`; return <div className="sw-bar-row" key={i}><span title={label}>{label}</span><div className="sw-bar-track"><i style={{ left: `${(Math.min(0, value) - lo) / span * 100}%`, width: `${Math.abs(value) / span * 100}%` }} /></div><strong>{value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 })}</strong></div>; })}</div><p className="sw-muted">展示原始记录，不自动推断显著性或优劣。</p></> : <div className="sw-metric-grid">{rows.slice(0, 6).map(row => <div key={row.name}><span>{row.name}</span><strong><BlurText kind="status" text={row.value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 })} /></strong></div>)}</div>}
    <details className="sw-details"><summary>全部指标与原始结构 · {rows.length} 项</summary><pre>{JSON.stringify(metrics, null, 2)}</pre></details>
  </div>;
}
function ArtifactFooter({ artifact, ...props }: { artifact: Artifact } & Pick<BoardProps, 'detail' | 'onProposal' | 'onInspect'>) { return <footer className="sw-card-footer"><Button icon="link" onClick={() => props.onInspect(artifact)}>{artifact.evidenceIds.length ? `${artifact.evidenceIds.length} 条证据` : '查看来源'}</Button><Discussion artifact={artifact} detail={props.detail} onProposal={props.onProposal} /></footer>; }
export function ClaimCard({ artifact, ...props }: { artifact: Artifact } & Pick<BoardProps, 'detail' | 'onProposal' | 'onInspect'>) {
  const data = artifact.content; const assessment = record(data.assessment);
  return <article className="sw-card sw-claim-card"><header><span className="sw-eyebrow">研究主张 · v{String(data.version || 1)}</span><Badge status={artifact.status} /></header><button className="sw-card-title-button" onClick={() => props.onInspect(artifact)}><h3><BlurText text={plain(data.statement) || artifact.title} /></h3></button>{plain(data.scope) && <p className="sw-muted">{plain(data.scope)}</p>}<div className="sw-claim-assessment">{plain(assessment.reason) || '还需要收集证据并完成论证。'}</div>{!artifact.evidenceIds.length && <p className="sw-no-evidence"><Icon name="link" />无证据 · 尚不能作为研究结论</p>}<ArtifactFooter artifact={artifact} {...props} /></article>;
}
export function Board(props: BoardProps) {
  const { detail, onInspect, onTab } = props;
  const artifacts = detail.workbench?.artifacts || [];
  const current = artifacts.filter(a => a.status !== 'stale');
  const jobArtifact = current.filter(a => a.kind === 'experiment_job').at(-1);
  const job = jobArtifact?.content as unknown as ExperimentJob | undefined;
  const output = current.filter(a => a.kind === 'node_output').at(-1);
  const claims = current.filter(a => a.kind === 'claim');
  const nodes = detail.state?.nodes.filter(n => n.active) || [];
  const active = nodes.filter(n => n.status === 'running');
  const unresolved = detail.state?.report?.unresolved || [];
  const activities = [...(detail.state?.activities || [])].reverse().slice(0, 4);
  const evidenceCount = new Set([...(detail.state?.evidence.map(e => e.id) || []), ...current.flatMap(a => a.evidenceIds)]).size;
  const decision = detail.state?.project.researchDecision;
  return <div className="sw-board">
    <div className="sw-board-heading"><div><span className="sw-eyebrow">RESEARCH WORKSPACE</span><h1>{detail.phase === 'completed' ? '这一轮，研究到了这里。' : '让每一个想法，都有依据。'}</h1></div><span className="sw-board-round">第 {detail.task.round || 1} 轮</span></div>
    <div className="sw-stat-row">{[[nodes.length, '研究节点'], [active.length, '正在推进'], [evidenceCount, '证据记录'], [claims.length, '研究主张']].map(([value, label]) => <div key={label}><strong><BlurText kind="status" text={String(value)} /></strong><span>{label}</span></div>)}</div>
    {decision && <section className="sw-decision"><span className="sw-eyebrow">这一步，由你决定</span><h2>{decision.question}</h2><ol>{decision.options.map((option, i) => <li key={i}><strong>{i + 1}. {option.label}</strong><p>{option.effect}</p></li>)}</ol><p className="sw-muted">在下方对话框回复选项编号或你的判断，研究会据此继续。</p></section>}
    <div className="sw-board-grid">
      <section className="sw-card sw-feature-card"><header><div><span className="sw-eyebrow">{job ? 'EXPERIMENT · 真实执行记录' : output ? 'RESEARCH · 当前研究产物' : 'RESEARCH · 当前任务'}</span><h2>{job ? '实验数据看板' : output ? output.title : detail.phase === 'retrieving' ? '正在建立研究背景' : '从主张出发，逐步寻找证据'}</h2></div>{job ? <Badge status={job.status} /> : detail.state && <Badge status={output?.status || detail.state.status} />}</header>
        {job ? <><MetricDisplay metrics={job.result?.metrics} /><div className="sw-experiment-facts"><span>尝试 {job.attempts?.length || 0} 次</span><span>耗时 {duration(job.result?.elapsedMs)}</span><span>产物 {job.result?.artifacts?.length || 0} 份</span></div></> : output ? <div className="sw-feature-copy"><Markdown text={plain(output.content.summary) || '当前产物已生成，可打开查看结构化内容与来源。'} /></div> : <div className="sw-research-map"><div className="sw-research-origin"><Icon name="spark" /><strong>{detail.task.title}</strong><span>研究问题</span></div><div className="sw-map-line" /><div className="sw-research-paths">{['形成可验证的主张', '查找已有证据', '补齐实验与论证'].map((item, i) => <div key={item}><span>0{i + 1}</span><strong>{item}</strong><p>{['明确假设与适用边界', '保留来源、支持与反证', '根据真实数据继续思考'][i]}</p></div>)}</div><p className="sw-muted">研究按证据需要推进。节点与产物会在完成后出现在看板。</p></div>}
        {jobArtifact || output ? <ArtifactFooter artifact={(jobArtifact || output)!} {...props} /> : <footer className="sw-card-footer"><span className="sw-muted">{active[0]?.title || '等待研究节点产生结果'}</span><Button icon="arrow" onClick={() => onTab('process')}>查看研究过程</Button></footer>}
      </section>
      <div className="sw-board-side">{claims[0] ? <ClaimCard artifact={claims[0]} {...props} /> : <section className="sw-card"><header><h2>等待形成主张</h2></header><p className="sw-muted">研究助手会把问题凝练为可以被验证、也可以被证伪的具体假设。</p></section>}
        <section className="sw-card sw-terminal-card"><header><div className="flex items-center gap-2"><Icon name="lab" /><h3>{job ? '最近实验记录' : '节点执行动态'}</h3></div><span className="sw-terminal-lights"><i /><i /><i /></span></header><pre>{job ? job.result?.stderr || job.result?.stdout || `状态：${job.status === 'running' ? '运行中，打开实验室查看实时输出。' : '尚未产生输出。'}\n${job.id}` : activities.slice(0, 3).map(a => `[${shortTime(a.at)}] ${a.message}`).join('\n\n') || '等待第一条执行记录…'}</pre><Button icon="arrow" onClick={() => onTab(job ? 'experiments' : 'process')}>{job ? '打开实验室' : '查看全部记录'}</Button></section>
      </div>
      <section className="sw-card sw-questions-card"><header><h2>接下来要弄清楚</h2><span className="sw-muted">{unresolved.length ? `${unresolved.length} 个未决问题` : '证据驱动下一步'}</span></header>{unresolved.length ? <ol>{unresolved.slice(0, 4).map((item, i) => <li key={i}><span>{String(i + 1).padStart(2, '0')}</span><p>{item}</p></li>)}</ol> : <p className="sw-muted">{claims.length ? '当前没有已记录的未决问题。你可以在主张旁提出质疑，或指定一个方向继续研究。' : '主张形成后，缺失的数据、实验和边界条件会集中列在这里。'}</p>}<Button icon="arrow" onClick={() => onTab('report')}>打开研究报告</Button></section>
      <section className="sw-card sw-recent-card"><header><h2>刚刚发生</h2><Button onClick={() => onTab('process')}>全部</Button></header><div className="sw-recent-list">{activities.length ? activities.map(a => <div key={a.id}><i className={a.actor === 'user' ? 'user' : ''} /><p><span>{a.actor === 'user' ? '你' : a.actor === 'AI' ? 'AI' : '系统'} · {shortTime(a.at)}</span><BlurText text={a.message} /></p></div>) : <p className="sw-muted">节点启动后，将记录每一次推进。</p>}</div></section>
    </div>
    {artifacts.some(a => a.status === 'stale') && <p className="sw-muted">{artifacts.filter(a => a.status === 'stale').length} 份历史产物已失效，可在研究过程里查看。</p>}
  </div>;
}
export function ClaimsView(props: BoardProps) {
  const claims = props.detail.workbench?.artifacts.filter(a => a.kind === 'claim' && a.status !== 'stale') || [];
  return <div className="sw-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">CLAIMS & EVIDENCE</span><h1>每条主张，都能追问到底。</h1></div><Badge status="draft" text={`${claims.length} 条主张`} /></div>{claims.length ? <div className="sw-claims-grid">{claims.map(artifact => <ClaimCard key={artifact.id} artifact={artifact} {...props} />)}</div> : <Empty title="尚未形成可验证的主张">节点产生主张后，将同时显示证据、反证和适用条件。</Empty>}</div>;
}
export function ReportView({ paper = false, ...props }: BoardProps & { paper?: boolean }) {
  const { detail } = props; const artifacts = detail.workbench?.artifacts || [];
  const artifact = paper ? artifacts.filter(a => a.kind === 'expression' && a.content.kind === 'paper' && a.status !== 'stale').at(-1) : artifacts.find(a => a.id === 'report:live');
  const markdown = paper ? plain(artifact?.content.markdown) : detail.workbench?.report.markdown || detail.state?.report.summary || '';
  return <div className="sw-module sw-report-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">{paper ? 'PAPER DRAFT' : 'LIVING REPORT'}</span><h1>{paper ? '把研究写成论文。' : '本轮研究，已经知道什么？'}</h1></div><Badge status={detail.workbench?.report.approved ? 'confirmed' : 'draft'} text={paper ? '论文草稿' : detail.workbench?.report.approved ? '用户已确认' : '待用户判断'} /></div>
    {markdown ? <article className="sw-report-paper"><div className="sw-report-metadata"><span>第 {detail.task.round} 轮</span><span>{detail.state?.evidence.length || 0} 条证据</span>{artifact && <Discussion artifact={artifact} detail={detail} onProposal={props.onProposal} label="就这份内容讨论" />}</div><Markdown text={markdown} />{artifact && <ArtifactFooter artifact={artifact} {...props} />}</article> : <Empty icon="book" title={paper ? '论文草稿尚未生成' : '报告将随研究逐步形成'}>这里只展示已生成的研究内容。你可以返回看板，查看节点和实验进展。</Empty>}
    {!!detail.state?.report.claims.length && <section className="sw-report-traces"><h2>逐条核查主张</h2>{detail.state.report.claims.map(claim => { const source = artifacts.find(a => a.kind === 'claim' && a.claimRefs.some(ref => ref.claimId === claim.claimId && ref.version === claim.claimVersion)); return <article className="sw-card" key={claim.id}><p>{claim.text}</p><div className="sw-flex-between">{source ? <Badge status={source.status} /> : <span className="sw-no-evidence">引用版本暂不可读取</span>}{source && <Button icon="link" onClick={() => props.onInspect(source)}>{source.evidenceIds.length ? '查看证据链' : '无证据 · 查看主张'}</Button>}</div>{claim.limitations && <p className="sw-muted">{claim.limitations}</p>}</article>; })}</section>}
    {!!detail.artifacts.length && <section className="sw-deliverables"><h2>研究交付物</h2><div>{detail.artifacts.map(a => <a className="sw-download-card" key={a.url} href={safeDownload(a.url)} download><Icon name="download" /><strong>{a.name}</strong><span>{a.kind}</span></a>)}</div></section>}
  </div>;
}
export function PapersView({ detail, onPaper }: Pick<BoardProps, 'detail' | 'onPaper'>) {
  const [query, setQuery] = useState(''); const papers = (detail.state?.papers || []).filter(p => `${p.title} ${p.abstract}`.toLowerCase().includes(query.toLowerCase()));
  return <div className="sw-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">REFERENCE LIBRARY</span><h1>研究背后的文献。</h1></div><label className="sw-search"><Icon name="search" /><input aria-label="检索当前论文库" placeholder="查找论文" value={query} onChange={e => setQuery(e.target.value)} /></label></div><p className="sw-muted">不必逐篇阅读。需要核查某个结论时，可以从这里进入原文与证据位置。</p>{papers.length ? <div className="sw-table-wrap"><table className="sw-table"><thead><tr><th>论文</th><th>相关性</th><th>可复现性</th><th>读取状态</th></tr></thead><tbody>{papers.map(p => <tr key={p.id}><td><button onClick={() => onPaper(p.id)}><strong>{p.title}</strong><span>{p.year} · {p.venue || '来源未标注'}</span></button></td><td>{Number.isFinite(p.score) ? p.score.toFixed(2) : '—'}</td><td>{p.reproducibility || '待评估'}</td><td><Badge status={p.workerStatus} /></td></tr>)}</tbody></table></div> : <Empty icon="search" title="还没有匹配的文献">检索结果将在这里积累。</Empty>}</div>;
}
export function ProcessView(props: BoardProps) {
  const [graph, setGraph] = useState(false); const { detail, onNode, onInspect } = props;
  if (graph) return <div className="sw-module sw-process-graph"><Button icon="back" onClick={() => setGraph(false)}>返回研究过程</Button><h1>二维需求与证据结构</h1><Suspense fallback={<Empty title="正在加载结构…" />}><ResearchGraph state={detail.state} retrieving={detail.phase === 'retrieving'} onSelect={onNode} /></Suspense></div>;
  return <div className="sw-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">RESEARCH TRACE</span><h1>看见每一步的来由。</h1></div><Button variant="outline" icon="layers" onClick={() => setGraph(true)}>打开二维结构</Button></div><SearchHistory operations={detail.state?.operations} /><div className="sw-process-columns"><section><h2>节点与执行状态</h2>{(detail.state?.nodes || []).filter(n => n.active).map(n => <button className="sw-node-row" key={n.id} onClick={() => onNode({ id: n.id, nodeId: n.id, sourceKind: 'agent', active: n.active, title: n.title, status: n.status, action: n.role })}><div><strong>{n.title}</strong><span>{n.role} · {duration(n.elapsedMs)}</span>{n.status === 'failed' && <p className="sw-failed-text">{n.logs.filter(l => l.level === 'error').at(-1)?.message || '执行失败，点击查看原因与输入输出。'}</p>}</div><Badge status={n.status} /></button>)}</section><section><h2>完整活动记录</h2><div className="sw-timeline">{[...(detail.state?.activities || [])].reverse().map(a => <article key={a.id}><span>{a.actor === 'user' ? '你' : a.actor} · {shortTime(a.at)}</span><p>{a.message}</p></article>)}</div></section></div><details className="sw-details"><summary>全部产物与历史失效记录</summary>{detail.workbench?.artifacts.map(a => <button className="sw-artifact-row" key={a.id} onClick={() => onInspect(a)}><span>{a.title}</span><Badge status={a.status} /></button>)}</details></div>;
}
