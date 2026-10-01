import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import type { TaskDetail } from '../taskTypes';
import type { ResearchNode } from '../types';
import type { VisualNode } from '../graphData';
import type { Artifact } from './types';
import { Avatar, Icon } from './ui';
import { chartSeries, protocolStrings } from './boardState';
import { plain, record, statusLabels } from './state';
import { buildAgentStructure, initialStructureView, layoutAgents, type AgentCardData, type AgentEdge, type AgentPosition, type AgentStructureData, type StructureView } from './agentStructureState';
import './agentStructure.css';

export interface AgentStructureProps {
  detail: TaskDetail;
  onNode: (node: VisualNode) => void;
  onInspect: (artifact: Artifact) => void;
  onDiscuss: (node: ResearchNode, artifact?: Artifact) => void;
}
const stepNames: Record<string, string> = {
  synthesis: '总 agent', background: '研究背景', literature: '文献核对', topic: '凝练课题',
  hypothesis: '主张验证', data_request: '数据需求', data_source: '寻找证据',
  experiment_design: '实验设计', experiment_execution: '执行实验', argument: '重新论证',
};
const brief = (value: unknown) => plain(value).replace(/^#+\s*/gm, '').trim();
const visualNode = (node: ResearchNode): VisualNode => ({ id: node.id, nodeId: node.id, parentId: node.parentId, title: node.title, status: node.status, sourceKind: 'agent', active: node.active, action: node.logs.at(-1)?.message || node.role });

function MetricChart({ metrics }: { metrics: unknown }) {
  const series = useMemo(() => chartSeries(metrics), [metrics]);
  const [selected, setSelected] = useState('');
  const current = series.find(item => item.key === selected) || series[0];
  if (!current) return null;
  const lo = Math.min(0, ...current.points.map(point => point.value)), hi = Math.max(0, ...current.points.map(point => point.value));
  const span = hi - lo || 1;
  return <div className="sa-metrics">
    <select aria-label="选择节点实测指标" value={current.key} onChange={event => setSelected(event.target.value)}>{series.map(item => <option value={item.key} key={item.key}>{item.key}</option>)}</select>
    <div className="sa-metric-bars" aria-label={`${current.key} 实测记录`}>{current.points.slice(0, 3).map((point, index) => <div className="sa-metric-row" key={`${point.label}:${index}`} title={`${point.label}: ${point.value}`}><span>{point.label}</span><div><i style={{ left: `${(Math.min(0, point.value) - lo) / span * 100}%`, width: `${Math.abs(point.value) / span * 100}%` }} /></div><strong>{point.value.toLocaleString('zh-CN', { maximumSignificantDigits: 5 })}</strong></div>)}</div>
  </div>;
}

function NodeBody({ data, detail, inspect, adjust }: { data: AgentCardData; detail: TaskDetail; inspect: () => void; adjust: () => void }) {
  const { node, step, claim, protocol, jobArtifact, evidence, outputArtifact, relationCounts, problem, unresolvedEvidenceCount } = data;
  const structured = record(outputArtifact?.content.structured), demand = record(structured.dataDemand || node.input.dataDemand);
  const metric = plain(demand.metric), latest = node.logs.at(-1)?.message || '';
  const subject = metric || claim?.statement || node.title;
  if (problem) return <div className="sa-node-problem"><strong><Icon name="lab" />执行问题</strong><p title={problem}>{problem}</p><button onClick={inspect}>查看原因与日志 <Icon name="arrow" /></button></div>;
  if (node.id === 'central' || !node.parentId && step === 'synthesis') {
    const claims = detail.state?.claimGraph?.claims.filter(item => !item.archived) || [];
    const active = detail.state?.nodes.filter(item => item.active && !item.input.superseded) || [];
    return <><p className="sa-node-subject" title={detail.task.title}>{detail.task.title}</p><div className="sa-counts"><div><strong>{claims.length}</strong><span>主张</span></div><div><strong>{active.filter(item => item.status === 'running').length}</strong><span>运行中</span></div><div><strong>{active.length}</strong><span>节点</span></div></div></>;
  }
  if (claim) return <><p className="sa-node-subject" title={claim.statement}>{claim.statement}</p><button className="sa-assessment" onClick={inspect}><span className={`sa-assessment-dot is-${claim.assessment.status}`} />{statusLabels[claim.assessment.status] || claim.assessment.status}<Icon name="chevron" /></button><div className="sa-evidence-counts" aria-label="当前主张版本的证据关联"><span title="正向 support 关系">正向 {relationCounts.for}</span><span title="反向 support 关系">反向 {relationCounts.against}</span><span title={`细化关系 ${relationCounts.qualify}；混合或未定关系 ${relationCounts.mixed}`}>细化 {relationCounts.qualify}</span></div></>;
  if (step === 'experiment_design' || Object.keys(protocol).length && step !== 'experiment_execution' && node.kind !== 'experiment') {
    const metrics = protocolStrings(protocol.metrics), baselines = protocolStrings(protocol.baselines);
    return <><div className="sa-field"><span>数据</span><button title={plain(protocol.dataset) || '待明确数据集'} onClick={adjust}>{plain(protocol.dataset) || '待明确'}<Icon name="edit" /></button></div><div className="sa-field"><span>对照</span><span title={baselines.join(' / ')}>{baselines.join(' / ') || '待设计'}</span></div>{typeof protocol.replicates === 'number' && <div className="sa-field"><span>重复</span><span>{protocol.replicates} 次</span></div>}<div className="sa-metric-pills">{(metrics.length ? metrics.slice(0, 2) : ['待测指标']).map(item => <span key={item} title={item}>{item}<b aria-label="尚无实测值">—</b></span>)}</div></>;
  }
  if (step === 'experiment_execution' || node.kind === 'experiment' || jobArtifact) {
    const job = record(jobArtifact?.content), result = record(job.result), measured = jobArtifact?.status === 'completed' && job.status === 'completed' && chartSeries(result.metrics).length > 0;
    const status = plain(job.status) || node.status;
    const progress = Math.max(0, Math.min(100, Number.isFinite(node.progress) ? node.progress : 0));
    return <>{measured ? <MetricChart metrics={result.metrics} /> : <><p className="sa-node-subject" title={subject}>{subject}</p><div className="sa-execution-status"><span>{statusLabels[status] || status}</span>{status === 'running' && progress > 0 && <span>{Math.round(progress)}%</span>}</div>{status === 'running' && <div className={`sa-execution-progress ${progress <= 0 ? 'is-indeterminate' : ''}`} role="progressbar" aria-label="节点执行进度" aria-valuemin={0} aria-valuemax={100} {...(progress > 0 ? { 'aria-valuenow': progress } : {})}><i style={{ width: progress > 0 ? `${progress}%` : '28%' }} /></div>}<p className="sa-node-log" title={plain(result.stdout) || latest}>{plain(result.stdout).trim().split('\n').at(-1) || latest || '等待执行'}</p></>}<button className="sa-inline-action" onClick={inspect}><Icon name="book" />{measured ? '查看数据与输出' : '查看运行日志'}</button></>;
  }
  if (step === 'data_request') return <><p className="sa-node-subject" title={metric || node.title}>{metric || node.title}</p><div className="sa-required-value"><span>所需数据</span><strong>—</strong></div><p className="sa-node-excerpt" title={plain(demand.purpose)}>{plain(demand.purpose) || '等待明确数据与验收条件'}</p></>;
  if (step === 'data_source' || step === 'literature') {
    const assessment = record(structured.dataAssessment);
    const abstract = evidence.filter(item => /abstract/i.test(item.type)).length;
    const experiment = evidence.filter(item => /experiment|reproduction/i.test(item.type)).length;
    const full = evidence.filter(item => /full|pdf/i.test(item.type)).length;
    const categories = [
      { label: '摘要证据', icon: 'book', count: abstract }, { label: '全文证据', icon: 'link', count: full },
      { label: '实验证据', icon: 'lab', count: experiment }, { label: '其他来源', icon: 'folder', count: evidence.length - abstract - full - experiment },
      { label: '未定位证据', icon: 'search', count: unresolvedEvidenceCount },
    ];
    const shown = categories.filter(item => item.count > 0);
    return <><div className="sa-source-row" title={categories.filter(item => item.count).map(item => `${item.label} ${item.count}`).join(' · ')}><Icon name="link" /><span>已定位证据</span><strong>{evidence.length}</strong></div>{shown.slice(0, 2).map(item => <div className="sa-source-row" key={item.label}><Icon name={item.icon} /><span>{item.label}</span><strong>{item.count}</strong></div>)}{shown.length === 0 && <p className="sa-node-excerpt">尚未关联可定位的证据</p>}{typeof assessment.sufficient === 'boolean' ? <button className="sa-source-assessment" title={plain(assessment.reason)} onClick={inspect}>{assessment.sufficient ? '已有证据可供论证' : '现有数据不足'}<Icon name="chevron" /></button> : <button className="sa-inline-action" onClick={inspect}>查看来源 <Icon name="arrow" /></button>}</>;
  }
  if (step === 'background') {
    const background = record(structured.background), fields = protocolStrings(background.relatedFields);
    return <><div className="sa-source-row"><Icon name="book" /><span>问题背景</span><strong>{plain(background.context) ? '已整理' : '待展开'}</strong></div><div className="sa-source-row"><Icon name="target" /><span>研究边界</span><strong>{plain(background.boundaries) ? '已整理' : '待明确'}</strong></div><div className="sa-source-row"><Icon name="layers" /><span>相关领域</span><strong>{fields.length}</strong></div><button className="sa-inline-action" onClick={inspect}>查看边界 <Icon name="arrow" /></button></>;
  }
  if (step === 'topic') {
    const topic = record(structured.researchTopic);
    return <><p className="sa-node-subject" title={plain(topic.question) || node.title}>{plain(topic.title) || '正在凝练课题'}</p><div className="sa-source-row"><Icon name="link" /><span>关联证据</span><strong>{evidence.length}</strong></div><div className="sa-source-row"><Icon name="layers" /><span>候选下一步</span><strong>{protocolStrings(structured.nextResearch).length}</strong></div><button className="sa-inline-action" onClick={inspect}>查看课题 <Icon name="arrow" /></button></>;
  }
  const summary = brief(outputArtifact?.content.summary) || brief(node.input.description);
  return <><p className="sa-node-excerpt is-large" title={summary || node.title}>{summary || node.title}</p><div className="sa-node-records"><span><Icon name="link" />{evidence.length} 条证据</span>{outputArtifact ? <button onClick={inspect}>查看产出 <Icon name="arrow" /></button> : <span>{node.status === 'running' ? '正在处理' : '待产出'}</span>}</div></>;
}

function edgePath(edge: AgentEdge, source: AgentPosition, target: AgentPosition) {
  if (edge.type === 'return') {
    const x1 = source.x + source.width - 12, y1 = source.y + 18, x2 = target.x + target.width - 12, y2 = target.y + target.height - 8;
    return `M${x1},${y1} C${x1 + 34},${y1 - 40} ${x2 + 34},${y2 + 40} ${x2},${y2}`;
  }
  if (edge.type !== 'decompose') {
    const x1 = source.x + source.width, y1 = source.y + source.height / 2, x2 = target.x, y2 = target.y + target.height / 2;
    return `M${x1},${y1} C${x1 + 26},${y1} ${x2 - 26},${y2} ${x2},${y2}`;
  }
  const x1 = source.x + source.width / 2, y1 = source.y + source.height, x2 = target.x + target.width / 2, y2 = target.y;
  const mid = (y1 + y2) / 2;
  return `M${x1},${y1} V${mid} H${x2} V${y2}`;
}

function StructureCanvas({ detail, onNode, onInspect, onDiscuss, data }: AgentStructureProps & { data: AgentStructureData }) {
  const viewport = useRef<HTMLDivElement>(null), initialized = useRef(false);
  const [layout, setLayout] = useState(() => layoutAgents(data.cards.map(item => item.node)));
  const [view, setView] = useState<StructureView>({ x: 0, y: 0, scale: 1 });
  const [selected, setSelected] = useState(''), [panning, setPanning] = useState(false);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [showMap, setShowMap] = useState(false);
  const drag = useRef<{ pointer: number; x: number; y: number; view: StructureView } | null>(null);
  const marker = useId().replaceAll(':', ''), layoutRef = useRef(layout);
  layoutRef.current = layout;
  const signature = data.cards.map(item => `${item.node.id}|${item.node.parentId || ''}`).sort().join(';');
  useLayoutEffect(() => { setLayout(previous => layoutAgents(data.cards.map(item => item.node), previous)); }, [signature]);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => {
      const next = { width: element.clientWidth, height: element.clientHeight }; setSize(next);
      if (!initialized.current && next.width && next.height && layoutRef.current.nodes.length) { initialized.current = true; setView(initialStructureView(layoutRef.current, next)); }
    };
    measure(); const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, [Boolean(layout.nodes.length)]);
  useEffect(() => {
    const element = viewport.current; if (!element) return;
    const wheel = (event: WheelEvent) => {
      if ((event.target as Element).closest('select, input, textarea')) return;
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        const bounds = element.getBoundingClientRect(), anchor = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
        setView(current => zoomAt(current, Math.exp(-event.deltaY * .006), anchor));
      } else setView(current => ({ ...current, x: current.x - event.deltaX, y: current.y - event.deltaY }));
    };
    element.addEventListener('wheel', wheel, { passive: false }); return () => element.removeEventListener('wheel', wheel);
  }, []);
  const byId = new Map(data.cards.map(item => [item.node.id, item]));
  const positions = new Map(layout.nodes.map(node => [node.id, node]));
  const activeEdges = data.edges.filter(edge => positions.has(edge.source) && positions.has(edge.target));
  const fit = () => { const scale = Math.max(.03, Math.min(1, (size.width - 48) / Math.max(1, layout.width), (size.height - 48) / Math.max(1, layout.height))); setView({ x: (size.width - layout.width * scale) / 2, y: (size.height - layout.height * scale) / 2, scale }); };
  const focus = (id: string) => { const node = positions.get(id); if (!node) return; setSelected(id); const scale = Math.max(.9, view.scale); setView({ x: size.width / 2 - (node.x + node.width / 2) * scale, y: size.height / 2 - (node.y + node.height / 2) * scale, scale }); };
  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest('article, button, select, input, a')) return;
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, view }; setPanning(true); event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => { const current = drag.current; if (!current || current.pointer !== event.pointerId) return; setView({ ...current.view, x: current.view.x + event.clientX - current.x, y: current.view.y + event.clientY - current.y }); };
  const pointerUp = () => { drag.current = null; setPanning(false); };
  const rootId = layout.nodes.find(node => node.id === 'central')?.id || layout.nodes.find(node => !node.parentId)?.id || '';
  return <section className="sa-structure" aria-label="完整 agent 研究结构">
    <div className={`sa-viewport ${panning ? 'is-panning' : ''}`} ref={viewport} tabIndex={0} aria-label="拖动浏览结构，Ctrl 加滚轮缩放" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onLostPointerCapture={pointerUp} onKeyDown={event => {
      if (event.target !== event.currentTarget) return;
      const shifts: Record<string, [number, number]> = { ArrowLeft: [64, 0], ArrowRight: [-64, 0], ArrowUp: [0, 64], ArrowDown: [0, -64] };
      if (shifts[event.key]) { event.preventDefault(); const [x, y] = shifts[event.key]; setView(current => ({ ...current, x: current.x + x, y: current.y + y })); }
      else if (event.key === '+' || event.key === '=' || event.key === '-') { event.preventDefault(); setView(current => zoomAt(current, event.key === '-' ? 1 / 1.2 : 1.2, { x: size.width / 2, y: size.height / 2 })); }
      else if (event.key === '0') { event.preventDefault(); fit(); }
    }}>
      {data.cards.length ? <div className="sa-world" style={{ width: layout.width, height: layout.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
        <svg className="sa-edges" width={layout.width} height={layout.height} aria-label="需求下派与结果回传"><defs><marker id={`${marker}-down`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M1 1L7 4L1 7" /></marker><marker id={`${marker}-return`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M1 1L7 4L1 7" /></marker></defs>{activeEdges.map(edge => <path key={`${edge.source}:${edge.target}:${edge.type}`} d={edgePath(edge, positions.get(edge.source)!, positions.get(edge.target)!)} className={`sa-edge is-${edge.type} ${edge.source === selected || edge.target === selected ? 'is-selected' : ''}`} markerEnd={`url(#${marker}-${edge.type === 'return' ? 'return' : 'down'})`} data-source={edge.source} data-target={edge.target} data-relation={edge.type}><title>{edge.type === 'return' ? '结果回传' : edge.type === 'decompose' ? '需求下派' : '关联'}：{edge.reason}</title></path>)}</svg>
        {layout.nodes.map((position, index) => {
          const item = byId.get(position.id); if (!item) return null;
          const { node } = item, inspect = () => { setSelected(node.id); if (item.jobArtifact) onInspect(item.jobArtifact); else if (item.claimArtifact && !item.problem) onInspect(item.claimArtifact); else onNode(visualNode(node)); };
          const discuss = () => { setSelected(node.id); onDiscuss(node, item.discussionArtifact); };
          return <article key={node.id} className={`sa-agent-card ${selected === node.id ? 'is-selected' : ''} ${item.problem ? 'has-problem' : ''}`} style={{ left: position.x, top: position.y, width: position.width, height: position.height }} data-agent-id={node.id} data-agent-step={item.step} data-agent-status={node.status} aria-label={node.title} onFocusCapture={() => { const left = position.x * view.scale + view.x, top = position.y * view.scale + view.y; if (left < 0 || top < 0 || left + position.width * view.scale > size.width || top + position.height * view.scale > size.height) focus(node.id); }}>
            <header><button className="sa-agent-heading" title={node.title} onClick={() => { setSelected(node.id); onNode(visualNode(node)); }}><Avatar index={index} small /><span>{stepNames[item.step] || node.role.replace(/\s*agent$/i, '') || node.title}</span></button><span className={`sa-status is-${node.status}`} title={statusLabels[node.status]} aria-label={statusLabels[node.status]}>{node.status === 'completed' ? <Icon name="check" /> : node.status === 'failed' || item.problem ? <span>!</span> : <i />}</span></header>
            <div className="sa-agent-body"><NodeBody data={item} detail={detail} inspect={inspect} adjust={discuss} /></div>
            <footer><button onClick={discuss} aria-label={`从这里深入：${node.title}`}>从这里深入 <Icon name="arrow" /></button></footer>
          </article>;
        })}
      </div> : <div className="sa-empty"><Icon name="layers" /><span>{detail.phase === 'retrieving' ? '检索完成后，研究节点会出现在这里' : '研究结构尚未生成'}</span></div>}
    </div>
    {data.cards.length > 0 && <><div className="sa-locator"><Icon name="search" /><select aria-label="定位研究节点" value={selected} onChange={event => focus(event.target.value)}><option value="">{data.cards.length} 个节点</option>{data.cards.map(({ node, step }) => <option key={node.id} value={node.id}>{stepNames[step] || node.role} · {node.title}</option>)}</select></div><div className="sa-map-controls"><button title="定位总 agent" aria-label="定位总 agent" onClick={() => focus(rootId)}><Icon name="target" /></button><button title="显示结构缩略图" aria-label="显示结构缩略图" aria-pressed={showMap} onClick={() => setShowMap(value => !value)}><Icon name="layers" /></button></div><div className="sa-zoom-controls"><button aria-label="缩小结构" title="缩小" onClick={() => setView(current => zoomAt(current, 1 / 1.2, { x: size.width / 2, y: size.height / 2 }))}>−</button><button aria-label="适应完整结构" title="适应完整结构" onClick={fit}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4" /></svg></button><button aria-label="放大结构" title="放大" onClick={() => setView(current => zoomAt(current, 1.2, { x: size.width / 2, y: size.height / 2 }))}>＋</button></div>{showMap && <div className="sa-minimap" aria-label="完整结构缩略图"><svg viewBox={`0 0 ${layout.width} ${layout.height}`} role="img" aria-label="点击节点定位">{layout.nodes.map(node => <rect key={node.id} x={node.x} y={node.y} width={node.width} height={node.height} tabIndex={0} role="button" aria-label={`定位：${byId.get(node.id)?.node.title || node.id}`} className={selected === node.id ? 'selected' : ''} onClick={() => focus(node.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focus(node.id); } }} />)}<rect className="sa-map-window" x={-view.x / view.scale} y={-view.y / view.scale} width={size.width / view.scale} height={size.height / view.scale} /></svg></div>}</>}
  </section>;
}

function zoomAt(view: StructureView, factor: number, anchor: { x: number; y: number }): StructureView {
  const scale = Math.max(.03, Math.min(1.8, view.scale * factor)), ratio = scale / view.scale;
  return { x: anchor.x - (anchor.x - view.x) * ratio, y: anchor.y - (anchor.y - view.y) * ratio, scale };
}

export function AgentStructure(props: AgentStructureProps) {
  const data = useMemo(() => buildAgentStructure(props.detail), [props.detail]);
  return <StructureCanvas key={props.detail.task.id} {...props} data={data} />;
}
