import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Empty, Modal, Popover, Select, Space, Spin, Splitter, Tag, Tooltip } from 'antd';
import { ApartmentOutlined, CodeOutlined, DownloadOutlined, ExpandOutlined, FileTextOutlined, FolderOpenOutlined, LineChartOutlined, PushpinOutlined, SettingOutlined } from '@ant-design/icons';
import { ResearchGraph } from './ConversationParts';
import { Markdown } from './Markdown';
import { BlurChange, BlurText } from './BlurReveal';
import { api, messageOf, taskPath } from './taskApi';
import { artifactCurrent, changedLines, chartGeometry, focusArtifacts, linkedSections, type ArtifactPreview, type MeasurementChart } from './deskState';
import { executionEntries, protocolValue } from './paperState';
import type { PaperSection } from './paperTypes';
import type { ResearchArtifact, TaskDetail } from './taskTypes';
import type { VisualNode } from './graphData';
import type { ResearchNode } from './types';
import './desk.css';

const panels = [
  { id: 'graph', name: '蜂群结构', icon: <ApartmentOutlined /> }, { id: 'chart', name: '实验图表', icon: <LineChartOutlined /> },
  { id: 'code', name: '实验代码', icon: <CodeOutlined /> }, { id: 'paper', name: '论文正在形成', icon: <FileTextOutlined /> },
  { id: 'assets', name: '产物架', icon: <FolderOpenOutlined /> }, { id: 'agents', name: '专业团队', icon: <ApartmentOutlined /> },
  { id: 'question', name: '研究问题与选题', icon: <FileTextOutlined /> }, { id: 'hypotheses', name: '假设与论点', icon: <LineChartOutlined /> },
  { id: 'methods', name: '候选方法', icon: <CodeOutlined /> }, { id: 'baselines', name: '基线对比', icon: <LineChartOutlined /> },
  { id: 'data', name: '数据与划分', icon: <FolderOpenOutlined /> }, { id: 'experiments', name: '实验调度', icon: <CodeOutlined /> },
  { id: 'ablation', name: '消融与稳健性', icon: <LineChartOutlined /> }, { id: 'statistics', name: '统计复核', icon: <LineChartOutlined /> },
  { id: 'evidence', name: '证据积累', icon: <FolderOpenOutlined /> }, { id: 'review', name: '独立评审与缺口', icon: <FileTextOutlined /> },
  { id: 'decisions', name: '你的判断与影响', icon: <PushpinOutlined /> },
];
const colors = ['#3975c6', '#4c9e88', '#b57c39', '#8c6bbb', '#c26777', '#4f96a8'];

function useFilePreview(detail: TaskDetail, artifact: ResearchArtifact | undefined) {
  const [result, setResult] = useState<{ key: string; value?: ArtifactPreview; error?: string }>({ key: '' });
  const key = artifact?.path || '';
  useEffect(() => {
    if (!key) return;
    let active = true;
    const read = async () => {
      try { const value = await api<ArtifactPreview>(taskPath(detail.task.id, '/paper/preview?path=' + encodeURIComponent(key))); if (active) setResult({ key, value }); }
      catch (error) { if (active) setResult({ key, error: messageOf(error) }); }
    };
    void read();
    const timer = artifact?.status === 'running' ? window.setInterval(() => { void read(); }, 2500) : null;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [detail.task.id, key, artifact?.status]);
  return { value: result.key === key ? result.value : undefined, error: result.key === key ? result.error : undefined,
    loading: Boolean(key && (result.key !== key || (!result.value && !result.error))) };
}

export function ResearchDesk({ detail, onNode, onEdit, onView }: {
  detail: TaskDetail; onNode: (node: VisualNode) => void; onEdit: (section: PaperSection) => void; onView: (view: string) => void;
}) {
  const [focused, setFocused] = useState<string | null>(null);
  const [filePath, setFilePath] = useState(''); const [chartPath, setChartPath] = useState(''); const [pinnedChart, setPinnedChart] = useState<ResearchArtifact | null>(null);
  const [visible, setVisible] = useState(() => { try { const saved = JSON.parse(localStorage.getItem('research-desk-panels') || 'null'); if (Array.isArray(saved) && saved.some(id => panels.some(p => p.id === id))) return saved.filter(id => panels.some(p => p.id === id)); } catch { /* default */ } return panels.map(p => p.id); });
  const [maximized, setMaximized] = useState<string | null>(null); const [galleryFile, setGalleryFile] = useState<ResearchArtifact | null>(null);
  const [liveChart, setLiveChart] = useState<{ id: string; value: ArtifactPreview } | null>(null);
  const [compact, setCompact] = useState(false); const host = useRef<HTMLDivElement>(null);
  const allNodes = detail.state?.nodes.filter(node => node.active) || [];
  const runs = executionEntries(detail.state);
  const latest = runs.find(run => run.current && run.execution.tool === 'python_run');
  const focusId = focused || latest?.execution.nodeId || allNodes.find(node => node.status === 'running')?.id || null;
  const focusNode = allNodes.find(node => node.id === focusId);
  const files = focusArtifacts(detail.artifacts, focusId);
  const nodeRuns = runs.filter(run => !focusId || run.execution.nodeId === focusId);
  const codes = files.filter(file => /\.(py|r|js|ts)$/i.test(file.name));
  const selectedCode = codes.find(file => file.path === filePath) || codes[0];
  const chartFiles = files.filter(file => /(?:research-dashboard\.json|\.png|\.jpg|\.jpeg|\.webp|\.csv|metrics[^/]*\.json)$/i.test(file.name));
  const selectedChart = pinnedChart || chartFiles.find(file => file.path === chartPath) || chartFiles.find(file => file.name === 'research-dashboard.json') || chartFiles[0];
  const code = useFilePreview(detail, selectedCode); const chart = useFilePreview(detail, selectedChart); const gallery = useFilePreview(detail, galleryFile || undefined);
  const evidenceIds = [...new Set([...nodeRuns.flatMap(run => run.execution.evidenceIds || []), ...(focusNode?.output?.evidenceIds || [])])];
  const sections = linkedSections(detail.paper?.sections || [], focused ? focusId : null, evidenceIds);
  const activeRun = nodeRuns.find(run => run.current && run.execution.status === 'running' && run.execution.tool === 'python_run');
  const previousCode = useRef({ text: '', nodeId: '' }); const [lineChanges, setLineChanges] = useState<Set<number>>(new Set());
  useEffect(() => {
    const element = host.current; if (!element) return;
    const observer = new ResizeObserver(entries => setCompact(entries[0].contentRect.width < 820)); observer.observe(element); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!code.value?.text) return;
    const previous = previousCode.current;
    setLineChanges(previous.nodeId === focusId ? changedLines(previous.text, code.value.text) : new Set());
    previousCode.current = { nodeId: focusId || '', text: code.value.text };
  }, [code.value?.sha256, focusId]);
  useEffect(() => {
    if (!activeRun) return;
    let active = true; let inflight = false;
    const read = async () => {
      if (inflight) return; inflight = true;
      try { const value = await api<ArtifactPreview>(taskPath(detail.task.id, '/paper/live-chart?execution=' + encodeURIComponent(activeRun.id))); if (active && !value.pending) setLiveChart({ id: activeRun.id, value }); }
      catch { /* Partial JSON during an atomic file replacement is retried on the next poll. */ }
      finally { inflight = false; }
    };
    void read(); const timer = window.setInterval(() => { void read(); }, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [detail.task.id, activeRun?.id]);
  const currentLive = !pinnedChart && activeRun && liveChart?.id === activeRun.id ? liveChart.value : undefined;
  const plotted = currentLive || chart.value;
  const focus = (id: string | null) => { setFocused(id); setFilePath(''); setChartPath(''); };
  const selectFromGraph = (node: VisualNode) => { if (node.nodeId) focus(node.nodeId); else onNode(node); };
  const openFocus = () => { if (focusNode) onNode({ id: `agent:${focusNode.id}`, nodeId: focusNode.id, title: focusNode.title, status: focusNode.status, active: focusNode.active, action: focusNode.logs.at(-1)?.message || focusNode.title, sourceKind: 'agent' }); };
  const togglePanels = (values: string[]) => { if (!values.length) return; setVisible(values); try { localStorage.setItem('research-desk-panels', JSON.stringify(values)); } catch { /* optional */ } };
  const openArtifact = (artifact: ResearchArtifact) => {
    if (/\.(py|r|js|ts)$/i.test(artifact.name)) { focus(artifact.nodeId || null); setFilePath(artifact.path || ''); if (!visible.includes('code')) togglePanels([...visible, 'code']); }
    else if (artifact.name === 'research-dashboard.json' || /\.(png|jpg|jpeg|webp)$/i.test(artifact.name)) { focus(artifact.nodeId || null); setPinnedChart(null); setChartPath(artifact.path || ''); if (!visible.includes('chart')) togglePanels([...visible, 'chart']); }
    else setGalleryFile(artifact);
  };
  const paneBody = (id: string) => {
    if (id === 'assets') return <section className="desk-assets"><header><FolderOpenOutlined /><strong>产物架</strong><span>{focusId ? '当前节点' : '全部实验'} · {files.filter(file => !/stdout|stderr|receipt/.test(file.name)).length} 份文件</span><Button type="text" size="small" onClick={() => onView('experiments')}>实验协议与结果</Button></header><div>{files.filter(file => !/stdout|stderr|receipt/.test(file.name)).slice(0, 24).map(file => <button className={file.path === selectedChart?.path || file.path === selectedCode?.path ? 'linked' : ''} key={file.url} onClick={() => openArtifact(file)}><span>{file.name.split('.').at(-1)?.toUpperCase()}</span><strong>{file.name}</strong><small>{artifactCurrent(file, detail) ? '本轮产物' : '历史产物'}</small></button>)}{!files.length && <p>模型、图表、代码和原始数据生成后，会自动归入这个实验。</p>}</div></section>;
    if (id === 'graph') return <Suspense fallback={<div className="desk-empty"><Spin /><p>加载研究结构</p></div>}><ResearchGraph state={detail.state} retrieving={detail.phase === 'retrieving'} onSelect={selectFromGraph} /></Suspense>;
    if (!['code', 'chart', 'paper'].includes(id)) return <ResearchBoard kind={id} detail={detail} onFocus={focus} onView={onView} />;
    if (id === 'code') return <div className="desk-code-content"><Select aria-label="查看实验代码版本" value={selectedCode?.path} onChange={setFilePath} placeholder="代码执行时自动出现" options={codes.map((file, i) => ({ value: file.path, label: `${file.name} · ${i === 0 ? '最新' : '历史执行'}${file.status === 'running' ? ' · 执行中' : ''}` }))} />{code.loading ? <div className="desk-empty"><Spin /></div> : code.error ? <Alert type="warning" title={code.error} /> : code.value?.text ? <div className="code-source" data-blur-owned="true">{code.value.text.split('\n').map((line, index) => <div className={`code-line ${lineChanges.has(index) ? 'changed-line' : ''}`} key={index}><span aria-hidden="true">{index + 1}</span><code>{line || ' '}</code></div>)}</div> : <div className="desk-empty"><CodeOutlined /><p>实际运行的脚本将在这里出现。<br />可以跟随实验，查看每次修改。</p></div>}{code.value?.truncated && <span className="desk-caption">预览已截断，可下载完整代码。</span>}{selectedCode && <div className="desk-file-footer"><span>{selectedCode.status === 'running' ? '本机正在执行' : artifactCurrent(selectedCode, detail) ? '本轮执行版本' : '历史执行版本'}</span><a href={selectedCode.url}>下载脚本</a></div>}</div>;
    if (id === 'chart') return <div className="desk-chart-content"><div className="chart-picker"><Select aria-label="选择实验图表" value={selectedChart?.path} onChange={value => { setPinnedChart(null); setChartPath(value); }} placeholder="等待真实测量数据" options={chartFiles.map(file => ({ value: file.path, label: file.name }))} /><Tooltip title={pinnedChart ? '取消固定，恢复联动' : '固定这份图表，切换节点时保留'}><Button aria-label="固定图表" type={pinnedChart ? 'primary' : 'text'} disabled={!selectedChart} icon={<PushpinOutlined />} onClick={() => setPinnedChart(pinnedChart ? null : selectedChart || null)} /></Tooltip></div>{currentLive && <Tag color="processing">实验进行中 · 暂存测量，未验收</Tag>}{chart.loading && !currentLive ? <div className="desk-empty"><Spin /></div> : chart.error && !currentLive ? <Alert type="warning" title={chart.error} /> : plotted ? <ArtifactContent value={plotted} /> : <div className="desk-empty"><LineChartOutlined /><p>节点生成真实数据后，图表将在这里更新。<br />未测量时保持空白，不预填结果。</p></div>}{selectedChart && <div className="desk-file-footer"><span>{pinnedChart ? '已固定 · ' : ''}{artifactCurrent(selectedChart, detail) ? '关联本轮实验' : '历史产物，需核验适用性'}</span><a href={selectedChart.url}>下载原始产物</a></div>}</div>;
    return <div className="desk-paper-content">{sections.length ? sections.map(section => <article key={section.id}><div><h3>{section.title}</h3><Space size={2}>{section.stale && <Tag color="orange">待重验</Tag>}<Button type="text" size="small" onClick={() => onEdit(section)}>一起修改</Button></Space></div><BlurChange value={section.revision}><Markdown text={section.markdown.length > 1800 ? section.markdown.slice(0, 1800) + '\n\n…' : section.markdown || section.suggestion?.markdown || ''} /></BlurChange><p className="desk-caption">{section.author === 'user' ? '你的文字受保护' : 'AI 候选正文'} · {section.evidenceIds.length} 条证据关联{section.suggestion ? ' · 有新修改建议' : ''}</p></article>) : <div className="desk-empty"><FileTextOutlined /><p>{focusId ? '这个节点还没有关联论文章节。' : '研究节点将逐步贡献论文章节。'}<br />有证据的结果会出现在对应段落。</p></div>}<Button className="all-manuscript" type="link" onClick={() => onView('paper')}>查看 / 编辑完整论文</Button></div>;
  };
  const pane = (id: string) => { const panel = panels.find(p => p.id === id)!; return <section className={`desk-pane desk-pane-${id}`} key={id}><header><div>{panel.icon}<strong>{panel.name}</strong>{id === 'code' && selectedCode?.status === 'running' && <Tag color="processing">执行中</Tag>}</div><Button type="text" size="small" aria-label={`放大${panel.name}`} icon={<ExpandOutlined />} onClick={() => setMaximized(id)} /></header><div className="desk-pane-body">{paneBody(id)}</div></section>; };
  return <div className={`research-desk ${compact ? 'compact-desk' : ''}`} ref={host}>
    <div className="desk-focus-bar"><div><span className="focus-dot" /><strong>联动工作台</strong><Tooltip title="选择节点后，图表、代码、论文与产物一起聚焦；默认跟随最新实验。"><Select aria-label="关注的研究节点" value={focused || 'auto'} onChange={value => focus(value === 'auto' ? null : value)} options={[{ value: 'auto', label: '跟随最新研究' }, ...allNodes.map(node => ({ value: node.id, label: node.title }))]} /></Tooltip></div><Space><Button type="text" size="small" disabled={!focusNode} onClick={openFocus}>节点详情 / 介入</Button><Popover trigger="click" placement="bottomRight" content={<Checkbox.Group options={panels.map(p => ({ label: p.name, value: p.id }))} value={visible} onChange={values => togglePanels(values as string[])} />}><Button type="text" size="small" icon={<SettingOutlined />}>面板</Button></Popover></Space></div>
    {focusNode && <div className="desk-context-line"><span>{focusNode.role}</span><strong><BlurText kind="status" text={focusNode.title} /></strong><span><BlurText kind="status" text={focusNode.logs.at(-1)?.message.slice(0, 150) || '等待研究节点的下一步工作'} /></span></div>}
    <div className="desk-wall-label"><strong>{visible.length} 个研究看板</strong><span>每块看板来自真实节点和产物 · 拖动面板右下角调整大小</span></div>
    {compact ? <div className="desk-mobile-surfaces"><div className="desk-grid board-wall">{visible.map(pane)}</div><TerminalWall detail={detail} onFocus={focus} /></div> :
      <Splitter className="desk-surfaces"><Splitter.Panel defaultSize="72%" min="42%"><div className="desk-boards-scroll"><div className="desk-grid board-wall">{visible.map(pane)}</div></div></Splitter.Panel><Splitter.Panel defaultSize="28%" min={250}><TerminalWall detail={detail} onFocus={focus} /></Splitter.Panel></Splitter>}

    <Modal title={panels.find(p => p.id === maximized)?.name} open={Boolean(maximized)} width="90vw" footer={null} onCancel={() => setMaximized(null)} destroyOnHidden><div className="maximized-desk-panel">{maximized && paneBody(maximized)}</div></Modal>
    <Modal title={galleryFile?.name} open={Boolean(galleryFile)} width="85vw" onCancel={() => setGalleryFile(null)} footer={<Button icon={<DownloadOutlined />} href={galleryFile?.url}>下载原文件</Button>} destroyOnHidden><div className="artifact-modal-content">{gallery.loading ? <Spin /> : gallery.error ? <Alert type="error" title={gallery.error} /> : gallery.value && <ArtifactContent value={gallery.value} />}</div></Modal>
  </div>;
}

function ResearchBoard({ kind, detail, onFocus, onView }: { kind: string; detail: TaskDetail; onFocus: (id: string) => void; onView: (view: string) => void }) {
  const nodes = detail.state?.nodes.filter(node => node.active) || [];
  const statusName: Record<string, string> = { pending: '待运行', running: '运行中', completed: '完成', failed: '失败', waiting_user: '等待用户' };
  const nodeCards = (items: ResearchNode[]) => items.length ? <div className="board-node-list">{items.map(node => <button key={node.id} onClick={() => onFocus(node.id)}><div><strong>{node.title}</strong><Tag color={node.status === 'running' ? 'processing' : node.status === 'failed' ? 'error' : 'default'}>{statusName[node.status]}</Tag></div><span>{node.role}</span><p><BlurText kind="status" text={node.output?.summary?.slice(0, 200) || node.logs.at(-1)?.message.slice(0, 160) || '等待需求分解与执行'} /></p></button>)}</div> : <div className="desk-empty"><p>团队分解到这项工作时，<br />负责人、进展和产物会出现在这里。</p></div>;
  if (kind === 'agents') return nodeCards([...nodes].sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running')));
  if (kind === 'question') { const topic = detail.paper?.topics.find(t => t.id === detail.paper?.selectedTopicId); return <div className="board-prose"><h3>{topic?.title || detail.task.title}</h3><p>{topic?.question || detail.document.markdown.replace(/^#+\s*/gm, '').slice(0, 500)}</p>{topic && <><Tag>可证伪假设</Tag><p>{topic.hypothesis}</p><Tag>首个验证</Tag><p>{topic.firstExperiment}</p></>}</div>; }
  if (kind === 'hypotheses') return <div className="board-prose">{detail.paper?.claims.length ? detail.paper.claims.map(claim => <article className="board-claim" key={claim.id}><Tag color={claim.evidenceIds.length ? 'blue' : 'orange'}>{claim.evidenceIds.length ? `${claim.evidenceIds.length} 条关联证据` : '待验证 / 无证据'}</Tag><p>{claim.text}</p><Button size="small" type="link" onClick={() => claim.nodeId && onFocus(claim.nodeId)}>关注来源节点</Button></article>) : <p className="muted">研究团队提出可检验的主张后，逐条在这里跟踪。</p>}<Button type="link" onClick={() => onView('claims')}>完整论点与证据</Button></div>;
  if (kind === 'experiments') return nodeCards(nodes.filter(node => node.kind === 'experiment'));
  if (kind === 'statistics') return nodeCards(nodes.filter(node => node.input.specialist === 'statistics' || /统计|置信|显著性/.test(node.title)));
  if (kind === 'ablation') return nodeCards(nodes.filter(node => ['ablation', 'robustness', 'falsification'].includes(String(node.input.specialist)) || /消融|稳健|鲁棒|反例/.test(node.title)));
  if (kind === 'methods') {
    const proposals = nodes.flatMap(node => { const p = node.output?.structured?.proposals; return Array.isArray(p) ? p.map(value => ({ nodeId: node.id, value })) : []; });
    return <>{proposals.length > 0 && <div className="board-prose">{proposals.map((proposal, index) => <article className="board-claim" key={index}><Tag>待验证候选</Tag><p>{protocolValue(proposal.value)}</p><Button type="link" size="small" onClick={() => onFocus(proposal.nodeId)}>关注方法节点</Button></article>)}</div>}{nodeCards(nodes.filter(node => ['method', 'novelty'].includes(String(node.input.specialist)) || /方法|创新/.test(node.title)))}</>;
  }
  if (kind === 'baselines' || kind === 'data') return <div className="board-prose">{detail.paper?.experiments.map(experiment => <article className="board-claim" key={experiment.nodeId}><strong>{experiment.title}</strong><p>{protocolValue(experiment.design[kind === 'baselines' ? 'baselines' : 'dataset'])}</p><Button type="link" size="small" onClick={() => onFocus(experiment.nodeId)}>联动实验</Button></article>)}{nodeCards(nodes.filter(node => node.input.specialist === (kind === 'data' ? 'dataset' : 'baseline')))}</div>;
  if (kind === 'evidence') { const evidence = detail.state?.evidence || []; return <div className="board-prose"><div className="evidence-counters"><span><strong>{detail.state?.papers.length || 0}</strong>篇论文</span><span><strong>{evidence.filter(e => e.type === 'full_text').length}</strong>页全文证据</span><span><strong>{evidence.filter(e => e.type === 'experiment').length}</strong>条实验凭据</span></div>{evidence.slice(-12).reverse().map(item => <article className="board-claim" key={item.id}><Tag>{item.type === 'experiment' ? '实验' : item.type === 'full_text' ? '正文' : '摘要 / 资料'}</Tag><p>{item.quote.slice(0, 150)}</p><small>{item.locator}</small></article>)}</div>; }
  if (kind === 'review') return <div className="board-prose"><Tag color={detail.paper?.issues.length ? 'orange' : 'default'}>{detail.paper?.issues.length || 0} 项待处理</Tag><ul>{detail.paper?.issues.slice(0, 24).map((issue, index) => <li key={index}>{issue}</li>)}</ul>{nodeCards(nodes.filter(node => node.input.specialist === 'reviewer' || /审稿|评审/.test(node.title)))}</div>;
  if (kind === 'decisions') return <div className="board-prose">{detail.paper?.pendingDecision && <Alert type="warning" title="当前等待你的判断" description={detail.paper.pendingDecision.question} />}{[...(detail.paper?.decisions || [])].reverse().slice(0, 16).map(decision => <article className="board-claim" key={decision.id}><strong>{decision.title}</strong><p>{decision.answer}</p><small>{decision.effect}</small></article>)}{!detail.paper?.decisions.length && <p className="muted">你的选择、质疑和写作会在这里留下记录，并进入团队的研究输入。</p>}</div>;
  return null;
}

function TerminalWall({ detail, onFocus }: { detail: TaskDetail; onFocus: (id: string) => void }) {
  const [limit, setLimit] = useState(12); const [expanded, setExpanded] = useState<string | null>(null); const [stream, setStream] = useState(''); const [error, setError] = useState('');
  const nodes = [...(detail.state?.nodes || [])].filter(node => node.active).sort((a, b) => Number(b.status === 'running') - Number(a.status === 'running') || (b.finishedAt || b.startedAt || '').localeCompare(a.finishedAt || a.startedAt || ''));
  const runs = executionEntries(detail.state);
  const selected = nodes.find(node => node.id === expanded);
  const run = runs.find(run => run.execution.nodeId === expanded);
  useEffect(() => {
    if (!expanded || !run) return;
    let active = true, inflight = false; setStream(''); setError('');
    const read = async () => {
      if (inflight) return; inflight = true;
      try { const output = await api<{ stdout: string; stderr: string }>(taskPath(detail.task.id, '/paper/terminal?execution=' + encodeURIComponent(run.id))); if (active) setStream(output.stdout + (output.stderr ? '\n── stderr ──\n' + output.stderr : '')); }
      catch (error) { if (active) setError(messageOf(error)); } finally { inflight = false; }
    };
    void read(); const timer = run.execution.status === 'running' ? window.setInterval(() => { void read(); }, 2000) : null;
    return () => { active = false; if (timer) window.clearInterval(timer); };
  }, [expanded, detail.task.id, run?.id, run?.execution.status]);
  return <section className="terminal-wall"><header><div><CodeOutlined /><strong>终端矩阵</strong><span>{nodes.length} 个真实 Agent 会话 · {nodes.filter(node => node.status === 'running').length} 运行中</span></div><Space><Button size="small" onClick={() => setLimit(current => current === 12 ? 24 : 12)}>{limit === 12 ? '展开 24 个终端' : '显示 12 个终端'}</Button></Space></header><div className="terminal-wall-grid">{nodes.slice(0, limit).map(node => { const process = runs.find(run => run.execution.nodeId === node.id); return <article className={`agent-terminal ${node.status}`} key={node.id}><header><span className="terminal-indicator" /><strong>{node.role}</strong><Tag color={node.status === 'running' ? 'processing' : node.status === 'failed' ? 'error' : 'default'}>{node.status === 'running' ? '运行中' : node.status === 'completed' ? '完成' : node.status === 'failed' ? '失败' : '待运行'}</Tag><Button type="text" size="small" aria-label={`放大终端 ${node.title}`} icon={<ExpandOutlined />} onClick={() => setExpanded(node.id)} /></header><button className="terminal-task-name" onClick={() => { onFocus(node.id); }}>{node.title}</button><div className="terminal-kind">{process ? `${process.execution.tool} · ${process.execution.status}` : 'Agent 工作记录'} · {(node.elapsedMs / 1000).toFixed(0)}s</div><div className="agent-terminal-lines" role="log" aria-label={`${node.title} 的实时日志`}>{node.logs.slice(-5).map(entry => <p key={entry.id} className={entry.level === 'error' ? 'error' : ''}><span>{new Date(entry.at).toLocaleTimeString('zh-CN', { hour12: false })}</span><BlurText kind="status" text={entry.message.slice(0, 400)} /></p>)}{!node.logs.length && <p>等待父节点下派具体任务。</p>}</div><footer><Button type="text" size="small" onClick={() => onFocus(node.id)}>联动看板</Button><Button type="text" size="small" onClick={() => setExpanded(node.id)}>完整日志 / 进程输出</Button></footer></article>; })}{!nodes.length && <div className="desk-empty"><p>检索后，专业 Agent 的独立终端会在这里逐个建立。</p></div>}</div>{nodes.length > limit && <Button onClick={() => setLimit(current => current + 12)}>再展开 12 个终端（另有 {nodes.length - limit} 个）</Button>}<Modal title={selected?.title} open={Boolean(expanded)} onCancel={() => setExpanded(null)} width="85vw" footer={<Button onClick={() => { if (selected) onFocus(selected.id); setExpanded(null); }}>关注这个节点</Button>} destroyOnHidden><div className="terminal-expanded-content"><h3>Agent 工作记录</h3><pre>{selected?.logs.map(entry => `[${entry.at}] ${entry.message}`).join('\n\n')}</pre>{run && <><h3>本机进程输出 · {run.execution.tool}</h3><pre>{stream || '进程暂未产生标准输出。'}</pre></>}{error && <Alert type="error" title={error} />}</div></Modal></section>;
}

function ArtifactContent({ value }: { value: ArtifactPreview }) {
  const [chartIndex, setChartIndex] = useState(0);
  const charts = value.dashboard?.charts || [];
  return <div className="artifact-content">{value.warning && <Alert type="warning" title={value.warning} />}{value.kind === 'image' ? <img className="experiment-figure" src={value.dataUrl} alt={value.name} /> : value.kind === 'chart' ? <>{charts.length > 1 && <Select aria-label="选择测量指标" value={Math.min(chartIndex, charts.length - 1)} onChange={setChartIndex} options={charts.map((c, i) => ({ label: c.title, value: i }))} />}{charts[Math.min(chartIndex, charts.length - 1)] && <MeasurementPlot chart={charts[Math.min(chartIndex, charts.length - 1)]} />}</> : value.kind === 'table' ? <><div className="data-table-wrap"><table><thead><tr>{value.columns?.map((column, i) => <th key={i}>{column}</th>)}</tr></thead><tbody>{value.rows?.map((row, i) => <tr key={i}>{value.columns?.map((_, j) => <td key={j}>{row[j] ?? ''}</td>)}</tr>)}</tbody></table></div><p className="desk-caption">原始数据共 {value.rowCount} 行{value.truncated ? ' · 当前为部分预览' : ''}</p></> : <pre className="desk-raw-file">{value.text}</pre>}{value.truncated && value.kind !== 'table' && <p className="desk-caption">预览已截断，请下载完整产物。</p>}</div>;
}

function MeasurementPlot({ chart }: { chart: MeasurementChart }) {
  const [hover, setHover] = useState<{ name: string; x: string | number; y: number } | null>(null);
  const geometry = useMemo(() => chartGeometry(chart), [chart]);
  const width = 600, height = 280, left = 67, right = 24, top = 22, bottom = 53;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const y = (value: number) => top + (geometry.max - value) / (geometry.max - geometry.min) * plotHeight;
  const x = (value: string | number) => left + (chart.kind === 'line' && geometry.numericX ? (Number(value) - geometry.xMin) / (geometry.xMax - geometry.xMin) : (geometry.categories.indexOf(String(value)) + .5) / Math.max(1, geometry.categories.length)) * plotWidth;
  const zero = y(0); const barWidth = Math.min(46, plotWidth / Math.max(1, geometry.categories.length) / (chart.series.length + 1));
  const format = (value: number) => new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 4 }).format(value);
  const totalPoints = chart.series.reduce((total, series) => total + series.points.length, 0);
  return <div className="measurement-plot"><h3><BlurText kind="status" text={chart.title} /></h3><div className="plot-unit">{chart.yLabel || '数值（原文件未提供单位）'}</div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${chart.title}。${chart.yLabel}，${totalPoints} 个原始测量点`}>
    {Array.from({ length: 5 }, (_, i) => geometry.min + (geometry.max - geometry.min) * i / 4).map((tick, i) => <g key={i}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="#e6ebf1" /><text x={left - 10} y={y(tick) + 4} textAnchor="end">{format(tick)}</text></g>)}
    <line x1={left} x2={width - right} y1={zero} y2={zero} stroke="#acb9ca" />
    {chart.series.map((series, si) => <g key={series.name + si}>{chart.kind === 'line' && <polyline fill="none" stroke={colors[si % colors.length]} strokeWidth="2" points={series.points.map(p => `${x(p.x)},${y(p.y)}`).join(' ')} />}{series.points.map((point, i) => {
      const handle = () => setHover({ name: series.name, x: point.x, y: point.y });
      const label = `${series.name} · ${point.x}: ${point.y} ${chart.yLabel}`;
      return chart.kind === 'bar' ? <rect key={i} x={x(point.x) + (si - (chart.series.length - 1) / 2) * barWidth - barWidth / 2} y={Math.min(zero, y(point.y))} width={Math.max(1, barWidth - 2)} height={Math.max(point.y === 0 ? 0 : 1, Math.abs(y(point.y) - zero))} fill={colors[si % colors.length]} rx="2" onMouseEnter={handle} onFocus={handle} tabIndex={totalPoints < 60 ? 0 : -1} aria-label={label}><title>{label}</title></rect> : <circle key={i} cx={x(point.x)} cy={y(point.y)} r={series.points.length < 50 ? 3 : 1.6} fill={colors[si % colors.length]} onMouseEnter={handle} onFocus={handle} tabIndex={totalPoints < 60 ? 0 : -1} aria-label={label}><title>{label}</title></circle>;
    })}</g>)}
    {geometry.categories.filter((_, i) => i % Math.max(1, Math.ceil(geometry.categories.length / 7)) === 0).map(category => <text x={x(category)} y={height - 27} textAnchor="middle" key={category}>{category.length > 11 ? category.slice(0, 10) + '…' : category}</text>)}<text x={width / 2} y={height - 5} textAnchor="middle">{chart.xLabel}</text>
  </svg><div className="plot-legend">{chart.series.map((series, i) => <span key={series.name + i}><i style={{ background: colors[i % colors.length] }} />{series.name}</span>)}</div><div className="plot-observation">{hover ? `${hover.name} · ${hover.x} = ${hover.y} ${chart.yLabel}` : '悬浮或用键盘聚焦测量点，查看原始数值'}</div>{chart.note && <p className="desk-caption">{chart.note}</p>}<details className="chart-raw"><summary>查看图表原始数据</summary><div className="data-table-wrap"><table><thead><tr><th>序列</th><th>{chart.xLabel || 'x'}</th><th>{chart.yLabel || 'y'}</th></tr></thead><tbody>{chart.series.flatMap(series => series.points.map((point, index) => <tr key={series.name + index}><td>{series.name}</td><td>{point.x}</td><td>{point.y}</td></tr>))}</tbody></table></div></details></div>;
}
