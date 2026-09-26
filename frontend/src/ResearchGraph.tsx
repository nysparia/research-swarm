import { useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Button, Checkbox, Drawer, Input, Popover, Progress, Spin, Tag, Tooltip } from 'antd';
import { AimOutlined, ExpandOutlined, MinusOutlined, PlusOutlined, SearchOutlined, SettingOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { buildGraphData, type VisualLink, type VisualNode } from './graphData';
import { applyManualNodePositions, layoutResearchGraph, type PositionedNode } from './graph2dLayout';
import { fitGraphView, panGraphView, zoomGraphView, type GraphView } from './graph2dViewport';
import { BlurText } from './BlurReveal';
import type { Snapshot } from './types';
import './graph2d.css';

const states = { pending: '待运行', running: '运行中', completed: '已完成', failed: '失败', waiting_user: '等待用户' };
const roles: Record<string, string> = { background: '背景扩充', literature: '文献研究', topic: '课题凝练', hypothesis_generation: '提出猜想', hypothesis: '论证猜想', data_request: '明确数据需求', data_source: '查找数据来源', experiment_design: '设计与复核实验', experiment_execution: '执行实验', synthesis: '综合论证' };
const relationNames: Record<string, string> = { decompose: '需求下派', return: '结果回传', compare: '并列 / 对比', facet: '资料层级' };
const endpoint = (value: string | VisualNode) => typeof value === 'string' ? value : value.id;

function linkPath(link: VisualLink, source: PositionedNode, target: PositionedNode) {
  if (link.type === 'compare') {
    const leftToRight = source.x <= target.x;
    const sx = source.x + (leftToRight ? source.width : 0), sy = source.y + source.height / 2;
    const tx = target.x + (leftToRight ? 0 : target.width), ty = target.y + target.height / 2;
    const bend = Math.max(32, Math.abs(tx - sx) / 2);
    return `M ${sx} ${sy} C ${sx + (leftToRight ? bend : -bend)} ${sy}, ${tx - (leftToRight ? bend : -bend)} ${ty}, ${tx} ${ty}`;
  }
  const downward = source.y <= target.y;
  const offset = link.type === 'return' ? 12 : 0;
  const sx = source.x + source.width / 2 + offset, sy = source.y + (downward ? source.height : 0);
  const tx = target.x + target.width / 2 + offset, ty = target.y + (downward ? 0 : target.height);
  const middle = (sy + ty) / 2;
  return `M ${sx} ${sy} C ${sx} ${middle}, ${tx} ${middle}, ${tx} ${ty}`;
}

interface Gesture { pointerId: number; kind: 'pan' | 'node'; x: number; y: number; view: GraphView; nodeId?: string; position?: { x: number; y: number }; moved: boolean }

export default function ResearchGraph({ state, onSelect, retrieving = false }: { state: Snapshot | null; onSelect: (node: VisualNode) => void; retrieving?: boolean }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [view, setView] = useState<GraphView>({ x: 0, y: 0, scale: 1 });
  const viewRef = useRef(view); viewRef.current = view;
  const [manualPositions, setManualPositions] = useState<Record<string, { x: number; y: number }>>({});
  const gesture = useRef<Gesture | null>(null);
  const suppressClick = useRef<string | null>(null);
  const userMoved = useRef(false);
  const [listOpen, setListOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [showMaterials, setShowMaterials] = useState(false);
  const [showRelations, setShowRelations] = useState(false);
  const markerId = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const data = useMemo(() => buildGraphData(state), [state]);
  const researchNodes = useMemo(() => new Map(state?.nodes.map(node => [node.id, node]) || []), [state]);
  const decision = (state?.project as (Snapshot['project'] & { researchDecision?: { question: string } | null }) | undefined)?.researchDecision;
  const nodes = useMemo(() => data.nodes.filter(node => showMaterials || node.active || node.id === 'central').map(node => node.id === 'central' && decision ? { ...node, status: 'waiting_user' as const, action: '请在下方对话中确认研究方向。' } : node), [data, showMaterials, decision]);
  const nodeIds = new Set(nodes.map(node => node.id));
  const links = data.links.filter(link => nodeIds.has(endpoint(link.source)) && nodeIds.has(endpoint(link.target)));
  const layout = useMemo(() => layoutResearchGraph(nodes, links), [nodes, data, showMaterials]);
  const positioned = applyManualNodePositions(layout.nodes, manualPositions);
  const positions = new Map(positioned.map(node => [node.id, node]));
  const topology = layout.nodes.map(node => `${node.id}:${node.x}:${node.y}`).join('|');
  const visibleLinks = links.filter(link => showRelations || link.type === 'decompose' || link.type === 'facet');

  const fit = () => { userMoved.current = true; setManualPositions({}); setView(fitGraphView(layout, size)); };
  const focusRoot = () => { const root = positions.get('central') || positioned[0]; if (!root) return; userMoved.current = true; setView({ scale: 1, x: size.width / 2 - root.x - root.width / 2, y: 20 - root.y }); };
  const zoom = (factor: number) => { userMoved.current = true; setView(current => zoomGraphView(current, factor, { x: size.width / 2, y: size.height / 2 })); };

  useEffect(() => {
    const host = viewport.current;
    if (!host) return;
    const resize = () => setSize({ width: host.clientWidth, height: host.clientHeight });
    const observer = new ResizeObserver(resize); observer.observe(host); resize();
    const wheel = (event: WheelEvent) => {
      event.preventDefault(); userMoved.current = true;
      const box = host.getBoundingClientRect();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? host.clientHeight : 1);
      setView(current => zoomGraphView(current, Math.exp(-Math.max(-240, Math.min(240, delta)) * 0.002), { x: event.clientX - box.left, y: event.clientY - box.top }));
    };
    host.addEventListener('wheel', wheel, { passive: false });
    return () => { observer.disconnect(); host.removeEventListener('wheel', wheel); };
  }, []);

  useEffect(() => {
    if (!size.width || !size.height || !layout.nodes.length || userMoved.current) return;
    const fitted = fitGraphView(layout, size);
    if (fitted.scale >= 0.85) setView(fitted);
    else { const root = layout.nodes.find(node => node.id === 'central') || layout.nodes[0]; setView({ scale: 0.9, x: size.width / 2 - (root.x + root.width / 2) * 0.9, y: 20 - root.y * 0.9 }); }
  }, [topology, size.width, size.height]);

  const startGesture = (event: ReactPointerEvent<HTMLElement>, nodeId?: string) => {
    if (event.button !== 0 || !event.isPrimary) return;
    if (nodeId) event.stopPropagation(); else event.preventDefault();
    suppressClick.current = null;
    gesture.current = { pointerId: event.pointerId, kind: nodeId ? 'node' : 'pan', nodeId, x: event.clientX, y: event.clientY, view: viewRef.current, position: nodeId ? positions.get(nodeId) : undefined, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveGesture = (event: ReactPointerEvent<HTMLElement>) => {
    const current = gesture.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const dx = event.clientX - current.x, dy = event.clientY - current.y;
    if (!current.moved && Math.hypot(dx, dy) < 4) return;
    current.moved = true; userMoved.current = true;
    if (current.kind === 'pan') setView(panGraphView(current.view, { x: dx, y: dy }));
    else if (current.nodeId && current.position) { const id = current.nodeId, position = current.position; setManualPositions(previous => ({ ...previous, [id]: { x: position.x + dx / current.view.scale, y: position.y + dy / current.view.scale } })); }
  };
  const endGesture = (event: ReactPointerEvent<HTMLElement>) => {
    if (gesture.current?.pointerId !== event.pointerId) return;
    suppressClick.current = event.type !== 'pointercancel' && gesture.current.kind === 'node' && gesture.current.moved ? gesture.current.nodeId || null : null;
    gesture.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const matches = data.nodes.filter(node => `${node.title} ${node.action}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <section className="research-graph graph-2d" aria-label="二维科研结构">
    <div className="graph-toolbar"><div><strong>研究结构</strong><span><BlurText kind="status" text={`${nodes.length} 个节点 · ${state?.nodes.filter(node => node.active && node.status === 'running').length || 0} 个运行中`} /></span></div><div className="graph-actions">
      <Tooltip title="缩小"><Button type="text" icon={<MinusOutlined />} aria-label="缩小结构图" onClick={() => zoom(0.8)} /></Tooltip><span className="graph-zoom-value">{Math.round(view.scale * 100)}%</span><Tooltip title="放大"><Button type="text" icon={<PlusOutlined />} aria-label="放大结构图" onClick={() => zoom(1.25)} /></Tooltip>
      <Tooltip title="适应画布并恢复自动排列"><Button type="text" icon={<ExpandOutlined />} aria-label="适应二维结构图" onClick={fit} /></Tooltip><Tooltip title="定位总 agent"><Button type="text" icon={<AimOutlined />} aria-label="定位总 agent" onClick={focusRoot} /></Tooltip>
      <Popover trigger="click" placement="bottomRight" content={<div className="graph-display-options"><Checkbox checked={showMaterials} onChange={event => { userMoved.current = false; setShowMaterials(event.target.checked); }}>显示资料切面和历史节点</Checkbox><Checkbox checked={showRelations} onChange={event => setShowRelations(event.target.checked)}>显示结果回传与对比关系</Checkbox></div>}><Button type="text" icon={<SettingOutlined />} aria-label="结构图显示选项" /></Popover>
      <Button type="text" icon={<UnorderedListOutlined />} onClick={() => setListOpen(true)}>节点列表</Button>
    </div></div>
    <div ref={viewport} className="graph-2d-viewport" role="region" aria-label="二维层级图，拖动平移，滚轮缩放，点击节点查看详情" tabIndex={0} onPointerDown={event => startGesture(event)} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} onKeyDown={event => {
      if (event.target !== event.currentTarget) return;
      const deltas: Record<string, { x: number; y: number }> = { ArrowLeft: { x: 80, y: 0 }, ArrowRight: { x: -80, y: 0 }, ArrowUp: { x: 0, y: 80 }, ArrowDown: { x: 0, y: -80 } };
      if (deltas[event.key]) { event.preventDefault(); userMoved.current = true; setView(current => panGraphView(current, deltas[event.key])); }
      if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(1.25); }
      if (event.key === '-') { event.preventDefault(); zoom(0.8); }
      if (event.key === '0') { event.preventDefault(); fit(); }
    }}>
      <div className="graph-2d-world" style={{ width: layout.width, height: layout.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
        <svg className="graph-2d-links" width={layout.width} height={layout.height} aria-hidden="true">
          <defs>{['decompose', 'return', 'compare', 'facet'].map(type => <marker key={type} id={`${markerId}-${type}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" className={`graph-arrow graph-arrow-${type}`} /></marker>)}</defs>
          {visibleLinks.map(link => { const source = positions.get(endpoint(link.source)), target = positions.get(endpoint(link.target)); return source && target ? <path key={`${source.id}|${target.id}|${link.type}`} d={linkPath(link, source, target)} className={`graph-2d-link graph-2d-link-${link.type}`} markerEnd={`url(#${markerId}-${link.type})`}><title>{relationNames[link.type] || '研究关联'}：{link.reason}</title></path> : null; })}
        </svg>
        {positioned.map(node => {
          const record = researchNodes.get(node.id), label = node.id === 'central' ? '研究协调' : node.sourceKind === 'facet' ? '资料切面' : !node.active ? '历史任务' : roles[String(record?.input.researchStep || '')] || `第 ${node.depth} 层任务`;
          const title = node.id === 'central' ? '总 agent' : node.title;
          return <Tooltip key={node.id} mouseEnterDelay={0.5} title={<div className="graph-2d-tooltip"><strong>{title}</strong><p>{node.action}</p></div>}>
            <button type="button" className={`graph-node-card graph-node-${node.status}${node.sourceKind === 'facet' || !node.active ? ' graph-node-material' : ''}`} style={{ left: node.x, top: node.y, width: node.width, height: node.height }} data-node-id={node.id} aria-label={`查看节点：${title}，${states[node.status]}`} onPointerDown={event => startGesture(event, node.id)} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} onClick={event => { const suppress = event.detail > 0 && suppressClick.current === node.id; suppressClick.current = null; if (suppress) { event.preventDefault(); return; } onSelect(node); }} onFocus={event => { if (!event.currentTarget.matches(':focus-visible')) return; const box = event.currentTarget.getBoundingClientRect(), canvas = viewport.current?.getBoundingClientRect(); if (canvas && (box.left < canvas.left || box.right > canvas.right || box.top < canvas.top || box.bottom > canvas.bottom)) { userMoved.current = true; setView(current => ({ ...current, x: size.width / 2 - (node.x + node.width / 2) * current.scale, y: size.height / 2 - (node.y + node.height / 2) * current.scale })); } }} onDragStart={event => event.preventDefault()}>
              <span className="graph-node-inner"><span className="graph-node-meta"><span>{label}</span><Tag color={node.status === 'running' ? 'blue' : node.status === 'completed' ? 'green' : node.status === 'failed' ? 'red' : node.status === 'waiting_user' ? 'gold' : 'default'}>{node.status === 'running' && <Spin size="small" />}<BlurText kind="status" text={states[node.status]} /></Tag></span><span className="graph-node-title"><BlurText text={title} /></span><span className="graph-node-action"><BlurText text={node.action.slice(0, 160)} /></span></span>
              {node.status === 'running' && <Progress className="graph-node-progress" percent={Math.max(0, Math.min(100, record?.progress || 0))} showInfo={false} size="small" />}
            </button>
          </Tooltip>;
        })}
      </div>
      {!nodes.length && <div className="graph-empty"><span><BlurText text={retrieving ? '正在检索并形成研究结构' : '等待研究节点'} /></span><p>论文和任务就绪后显示节点关系。</p></div>}
    </div>
    <div className="graph-footer"><div className="graph-legend"><span><i className="legend-decompose" />需求下派</span>{showRelations && <><span><i className="legend-return" />结果回传</span><span><i className="legend-compare" />并列 / 对比</span></>}</div><span>拖动平移 · 滚轮缩放 · 点击节点查看</span></div>
    <Drawer open={listOpen} title="研究节点" size={470} onClose={() => setListOpen(false)} rootClassName="task-drawer"><Input prefix={<SearchOutlined />} value={search} onChange={event => setSearch(event.target.value)} allowClear placeholder="搜索节点或当前动作" aria-label="搜索研究节点" /><div className="accessible-node-list">{matches.map(node => <button key={node.id} onClick={() => { onSelect(node); setListOpen(false); }}><div><strong>{node.title}</strong><Tag color={node.status === 'running' ? 'blue' : node.status === 'failed' ? 'red' : 'default'}><BlurText kind="status" text={node.sourceKind === 'facet' ? '资料切面' : states[node.status]} /></Tag></div><p><BlurText text={node.action} /></p></button>)}{!matches.length && <p className="quiet-text">没有符合条件的节点。</p>}</div></Drawer>
  </section>;
}
