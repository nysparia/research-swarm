import { ResearchBoard } from './ResearchBoard';
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
import { representativeAgents } from './designState';

export interface BoardProps {
  detail: TaskDetail; onProposal: (value: ProposalReview) => void; onInspect: (artifact: Artifact) => void;
  onNode: (node: VisualNode) => void; onPaper: (id: string) => void; onTab: (tab: string) => void; onExport?: () => void; onContinue?: () => void;
}
export function AgentStrip({ detail, onNode }: Pick<BoardProps, 'detail' | 'onNode'>) {
  return <div className="sw-agent-strip" aria-label="Agent 当前活动">{representativeAgents(detail.state?.nodes || []).map(({node, role}) => <button key={node.id} className="sw-agent" title={node.title + '：' + (node.logs.at(-1)?.message || node.role)} onClick={() => onNode({ id: node.id, nodeId: node.id, sourceKind: 'agent', active: node.active, title: node.title, status: node.status, action: node.role })}>
    <span className="sw-agent-bubble"><BlurText kind="status" text={node.logs.at(-1)?.message || node.title} /></span><Avatar index={role === '实验' ? 1 : role === '文献' ? 2 : 0} /><small><i className={'sw-agent-dot ' + node.status} />{role}</small>
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
export function Board(props: BoardProps) { return <ResearchBoard {...props} />; }
export function ClaimsView(props: BoardProps) {
  const claims = props.detail.workbench?.artifacts.filter(a => a.kind === 'claim' && a.status !== 'stale') || [];
  return <div className="sw-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">CLAIMS & EVIDENCE</span><h1>每条主张，都能追问到底。</h1></div><Badge status="draft" text={`${claims.length} 条主张`} /></div>{claims.length ? <div className="sw-claims-grid">{claims.map(artifact => <ClaimCard key={artifact.id} artifact={artifact} {...props} />)}</div> : <Empty title="尚未形成可验证的主张">节点产生主张后，将同时显示证据、反证和适用条件。</Empty>}</div>;
}
export function ReportView({ paper = false, ...props }: BoardProps & { paper?: boolean }) {
  const { detail } = props; const artifacts = detail.workbench?.artifacts || [];
  const artifact = paper ? artifacts.filter(a => a.kind === 'expression' && a.content.kind === 'paper' && a.status !== 'stale').at(-1) : artifacts.find(a => a.id === 'report:live');
  const markdown = paper ? plain(artifact?.content.markdown) : detail.workbench?.report.markdown || detail.state?.report.summary || '';
  const claims = detail.state?.report.claims || [];
  return <div className="sw-result-layout"><article className="sw-result-document">
    <header><h1>{paper ? '论文草稿' : '本轮研究结果'}</h1><p>{paper ? '从研究问题，到有依据的表达' : '结论、证据与下一步'}</p></header>
    {markdown ? <><div className="sw-report-metadata"><span>第 {detail.task.round} 轮</span><Badge status={detail.workbench?.report.approved ? 'confirmed' : 'draft'} text={detail.workbench?.report.approved ? '用户已确认' : '候选结果 · 待审阅'} />{artifact && <Discussion artifact={artifact} detail={detail} onProposal={props.onProposal} label="就这份内容讨论" />}</div><Markdown text={markdown} /></> : <Empty icon="book" title={paper ? '论文草稿尚未生成' : '研究结果正在汇集'}>新结果和证据会逐步写入这里。</Empty>}
    {!!claims.length && <section className="sw-result-evidence"><h2>证据与适用边界</h2>{claims.map(claim => {const source=artifacts.find(a=>a.kind==='claim' && a.claimRefs.some(ref=>ref.claimId===claim.claimId && ref.version===claim.claimVersion));return <div key={claim.id}><p>{claim.text}</p>{source && <Button icon="link" onClick={()=>props.onInspect(source)}>{source.evidenceIds.length ? '查看证据链' : '无证据 · 查看主张'}</Button>}{claim.limitations && <small>{claim.limitations}</small>}</div>;})}</section>}
    {!!detail.state?.report.unresolved.length && <section className="sw-result-unresolved"><h2>未决问题</h2><ol>{detail.state.report.unresolved.map((item,i)=><li key={i}>{item}</li>)}</ol></section>}
  </article><aside className="sw-result-delivery"><h2>研究交付</h2><p>本轮已保存的产物，供你核查与使用。</p><div className="sw-delivery-list">{detail.artifacts.length ? detail.artifacts.map(item=><a href={safeDownload(item.url)} download key={item.url}><Icon name="book"/><div><strong>{item.name}</strong><small>{item.kind}</small></div><Icon name="download"/></a>) : <p>交付文件形成后会列在这里。</p>}</div><section><h2>由你判断下一步</h2><p>查看已有证据，再决定接受当前结果，或继续补充研究。</p><Button variant="primary" onClick={()=>props.onTab('claims')}>审阅主张与证据</Button><div><Button variant="outline" icon="download" onClick={props.onExport}>导出研究档案</Button><Button variant="outline" onClick={props.onContinue}>继续研究</Button></div></section></aside></div>;
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
