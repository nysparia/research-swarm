import { Suspense, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Checkbox, Drawer, Empty, Input, Modal, Progress, Segmented, Select, Space, Spin, Tabs, Tag, Tooltip } from 'antd';
import { ApartmentOutlined, BulbOutlined, CheckCircleOutlined, CodeOutlined, DownloadOutlined, EditOutlined, ExperimentOutlined, FileTextOutlined, LinkOutlined, ExpandOutlined } from '@ant-design/icons';
import { Markdown } from './Markdown';
import { BlurChange, BlurText } from './BlurReveal';
import { ResearchGraph, timeLabel } from './ConversationParts';
import { TaskEvidence } from './TaskOverlays';
import { api, messageOf, taskPath } from './taskApi';
import { executionEntries, protocolLabels, protocolValue, researchCounts } from './paperState';
import type { TaskDetail } from './taskTypes';
import type { PaperSection, TopicCandidate } from './paperTypes';
import type { VisualNode } from './graphData';
import type { Impact } from './types';
import './paper.css';
import { ResearchDesk } from './ResearchDesk';

type Props = { detail: TaskDetail; accept: (detail: TaskDetail) => void; onNode: (node: VisualNode) => void; onPaper: (id: string) => void };

export function ResearchBudget({ detail, accept }: Pick<Props, 'detail' | 'accept'>) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const tier = detail.paper?.budgetTier || 'swarm';
  const change = async (tier: string) => {
    setBusy(true); setError('');
    try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/paper/budget'), { tier })); }
    catch (error) { setError(messageOf(error)); } finally { setBusy(false); }
  };
  return <><div className="budget-control"><div><strong>科研蜂群规模</strong><span>{tier === 'swarm' ? '最多 8 路并发 · 240 个任务 · 8 次研究迭代' : tier === 'team' ? '最多 6 路并发 · 160 个任务 · 6 次研究迭代' : '最多 3 路并发 · 48 个任务 · 3 次研究迭代'}<br />按研究需要建立专业节点，API 用量随实际任务数增加。</span></div><Select aria-label="选择科研蜂群规模" value={tier} loading={busy} disabled={busy} onChange={value => { void change(value); }} options={[{ value: 'swarm', label: '大型蜂群 · 多组协作' }, { value: 'team', label: '研究团队 · 中等规模' }, { value: 'compact', label: '小规模 · 快速验证' }]} /></div>{error && <Alert type="error" title={error} />}</>;
}

export function TopicSelection({ detail, accept }: Pick<Props, 'detail' | 'accept'>) {
  const [choice, setChoice] = useState<TopicCandidate | null>(null);
  const [note, setNote] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const paper = detail.paper;
  if (!paper?.topics.length) return null;
  const select = async () => {
    if (!choice) return; setBusy(true); setError('');
    try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/paper/topic'), { topicId: choice.id, expectedRevision: paper.revision, note })); setChoice(null); setNote(''); }
    catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  };
  return <section className="topic-selection"><div className="section-heading"><div><span className="eyebrow">一起确定值得研究的问题</span><h2>从你的想法，走向一个论文课题</h2></div>{paper.selectedTopicId && <Tag color="blue">你已选定方向</Tag>}</div><p className="muted">这些是待检验的选题。你来决定最关心哪个问题，AI 负责查证、实验和写作。</p><div className="topic-grid">{paper.topics.map((topic, index) => <article className={`topic-card ${paper.selectedTopicId === topic.id ? 'chosen' : ''}`} key={topic.id}><div className="topic-index">方向 {String(index + 1).padStart(2, '0')}{paper.selectedTopicId === topic.id && <CheckCircleOutlined />}</div><h3><BlurText text={topic.title} /></h3><p>{topic.question}</p><dl><dt>可能的贡献</dt><dd>{topic.contribution}</dd><dt>先做什么验证</dt><dd>{topic.firstExperiment}</dd></dl><Button block type={paper.selectedTopicId === topic.id ? 'primary' : 'default'} disabled={detail.document.polishing} onClick={() => { setChoice(topic); setError(''); }}>{paper.selectedTopicId === topic.id ? '查看我的选择' : '探讨这个方向'}</Button></article>)}</div><Modal title={choice?.title} open={Boolean(choice)} onCancel={() => setChoice(null)} onOk={() => { void select(); }} confirmLoading={busy} okText="以这个选题完善需求" cancelText="再想想"><p>{choice?.question}</p><dl className="protocol-list"><dt>待验证假设</dt><dd>{choice?.hypothesis}</dd><dt>可行性</dt><dd>{choice?.feasibility}</dd></dl><Input.TextArea aria-label="我对选题的补充" value={note} onChange={e => setNote(e.target.value)} placeholder="你更在意什么？例如：优先低延迟，实验只用本机 CPU。" autoSize={{ minRows: 3, maxRows: 6 }} />{error && <Alert type="error" title={error} />}</Modal></section>;
}

export function PaperWorkbench({ detail, accept, onNode, onPaper }: Props) {
  const { notification } = App.useApp();
  const paper = detail.paper;
  const [view, setView] = useState('desk');
  const [side, setSide] = useState('conversation');
  const [companionOpen, setCompanionOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(true);
  const [editor, setEditor] = useState<{ section: PaperSection; text: string } | null>(null);
  const [editorError, setEditorError] = useState(''); const [busy, setBusy] = useState('');
  const [evidenceSection, setEvidenceSection] = useState<PaperSection | null>(null);
  const [decisionOpen, setDecisionOpen] = useState(false); const [option, setOption] = useState<number | undefined>();
  const [note, setNote] = useState(''); const [impact, setImpact] = useState<Impact | null>(null); const [decisionError, setDecisionError] = useState('');
  const [approval, setApproval] = useState(false); const [acknowledge, setAcknowledge] = useState(false);
  const lastDecision = useRef<string>(); const alive = useRef(true);
  const counts = researchCounts(detail.state);
  const pending = paper?.pendingDecision;
  const showCompanion = view !== 'desk' || companionOpen || Boolean(pending);
  const chosenTopic = paper?.topics.find(topic => topic.id === paper.selectedTopicId);
  const draftKey = (id: string) => `research-paper-draft:${detail.task.id}:${id}`;
  const beginEdit = (section: PaperSection) => {
    let text = section.markdown; let base = section;
    try { const saved = JSON.parse(localStorage.getItem(draftKey(section.id)) || 'null');
      if (saved && typeof saved.text === 'string' && typeof saved.revision === 'number') {
        text = saved.text; base = { ...section, revision: saved.revision };
      }
    } catch { /* Private browsing/storage limits still allow editing in memory. */ }
    setEditor({ section: base, text }); setEditorError('');
  };
  const changeText = (text: string) => {
    setEditor(current => {
      if (!current) return null;
      try { localStorage.setItem(draftKey(current.section.id), JSON.stringify({ text, revision: current.section.revision })); } catch { /* Keep in-memory draft. */ }
      return { ...current, text };
    });
  };
  const openNode = (id: string) => { const node = detail.state?.nodes.find(n => n.id === id); if (node) onNode({ id: `agent:${node.id}`, nodeId: node.id, title: node.title, status: node.status, active: node.active, action: node.logs.at(-1)?.message || node.title, sourceKind: 'agent' }); };
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (pending && pending.id !== lastDecision.current) {
      lastDecision.current = pending.id; setDecisionOpen(true); setOption(undefined); setNote(''); setImpact(null); setDecisionError('');
      notification.info({ title: '研究团队在等你的判断', description: pending.question, duration: 7 });
    }
  }, [pending?.id, notification]);
  const saveSection = async (acceptSuggestion = false) => {
    if (!editor) return; setBusy('section'); setEditorError('');
    try {
      const next = await api<TaskDetail>(taskPath(detail.task.id, `/paper/sections/${editor.section.id}`), { markdown: editor.text, expectedRevision: editor.section.revision,
        ...(acceptSuggestion ? { acceptSuggestion: true, suggestionId: editor.section.suggestion?.id } : {}) });
      accept(next);
      try { localStorage.removeItem(draftKey(editor.section.id)); } catch { /* Storage optional. */ }
      if (alive.current) setEditor(null);
    } catch (e) { setEditorError(messageOf(e)); } finally { setBusy(''); }
  };
  const reviewImpact = async () => {
    if (!pending) return; setBusy('impact'); setDecisionError('');
    try { setImpact(await api<Impact>(taskPath(detail.task.id, '/actions/impact'), { nodeId: pending.nodeId, kind: 'modify' })); }
    catch (e) { setDecisionError(messageOf(e)); } finally { setBusy(''); }
  };
  const choose = async () => {
    if (!pending || !impact) return; setBusy('decision'); setDecisionError('');
    try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/paper/decision'), { decisionId: pending.id, optionIndex: option ?? null, note, expectedRevision: impact.revision })); setDecisionOpen(false); setImpact(null); }
    catch (e) { setDecisionError(messageOf(e)); setImpact(null); } finally { setBusy(''); }
  };
  const approve = async () => {
    if (!paper) return; setBusy('approve'); setEditorError('');
    try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/paper/approve'), { expectedRevision: paper.revision, acknowledgeIssues: acknowledge })); setApproval(false); }
    catch (e) { setEditorError(messageOf(e)); } finally { setBusy(''); }
  };
  if (!paper) return <div className="loading-status"><Spin /><span>载入论文工作区</span></div>;
  return <div className="paper-workbench">
    <div className="research-ribbon"><div><span className="eyebrow">AI FOR COMPUTER SCIENCE</span><span className="ribbon-purpose">与你一起完成一篇有证据的论文</span></div><Space wrap><Tooltip title="本轮已创建并参与研究的真实节点"><Tag>{counts.total} 个研究节点</Tag></Tooltip><Tag color={counts.running ? 'processing' : 'default'}><BlurText kind="status" text={`${counts.running} 运行 · ${counts.completed} 完成`} /></Tag>{counts.failed > 0 && <Tag color="error">{counts.failed} 失败</Tag>}<Tooltip title="全屏科研指挥台"><Button type="text" aria-label="全屏科研指挥台" icon={<ExpandOutlined />} onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void document.documentElement.requestFullscreen().catch(() => {}); }} /></Tooltip><Button className="command-companion-toggle" onClick={() => setCompanionOpen(value => !value)}>{companionOpen ? '收起搭档' : '研究搭档'}</Button><Button icon={<DownloadOutlined />} href={`/api${taskPath(detail.task.id, '/paper/export')}`}>导出论文与材料</Button></Space></div>
    <div className={`paper-workbench-body ${showCompanion ? '' : 'wide-command'}`}><section className="research-main-pane">
      <div className="workbench-tabs"><Segmented aria-label="研究工作区视图" value={view} onChange={value => setView(String(value))} options={[{ label: '指挥台', value: 'desk', icon: <ApartmentOutlined /> }, { label: '蜂群', value: 'graph', icon: <ApartmentOutlined /> }, { label: '论文', value: 'paper', icon: <FileTextOutlined /> }, { label: '实验', value: 'experiments', icon: <ExperimentOutlined /> }, { label: '论点与证据', value: 'claims', icon: <LinkOutlined /> }]} /><Tooltip title={terminalOpen ? '收起实验终端' : '打开实验终端'}><Button aria-label="切换实验终端" type="text" icon={<CodeOutlined />} onClick={() => { if (view === 'desk') document.querySelector('.terminal-wall')?.scrollIntoView({ block: 'start' }); else setTerminalOpen(v => !v); }} /></Tooltip></div>
      <div className={`research-main-view view-${view}`}>
        {view === 'desk' && <ResearchDesk detail={detail} onNode={onNode} onEdit={beginEdit} onView={setView} />}
        {view === 'graph' && <><Suspense fallback={<div className="graph-loading"><Spin /><span>加载真实研究结构</span></div>}><ResearchGraph state={detail.state} retrieving={detail.phase === 'retrieving'} onSelect={onNode} /></Suspense><div className="graph-context"><div><strong>{pending ? '研究方向由你决定' : detail.state?.paused ? '研究已暂停' : '专业节点正在协作'}</strong><p>{pending?.question || '悬浮查看每个节点正在做什么；点击节点可查看依据、干预任务或从这里深入研究。'}</p></div>{pending && <Button type="primary" onClick={() => setDecisionOpen(true)}>作出选择</Button>}</div></>}
        {view === 'paper' && <article className="manuscript"><div className="manuscript-heading"><div><span className="eyebrow">共同撰写 · 第 {detail.task.round} 轮</span><h1>{chosenTopic?.title || detail.task.title}</h1></div><Tag color={paper.approved ? 'green' : 'default'}>{paper.approved ? '此版本已由你确认' : '论文草稿'}</Tag></div><div className="manuscript-progress"><span>已形成 {paper.coverage.written} / {paper.coverage.total} 个章节</span><Progress percent={paper.coverage.written / paper.coverage.total * 100} showInfo={false} size="small" /></div>{paper.coverage.written === 0 && <Alert type="info" showIcon title="论文会随研究逐步形成" description="文献研究、方法与实验节点会将结果写到对应章节。你也可以先写下自己的想法，AI 后续给出建议。" />}<div className="paper-sections">{paper.coverage.written === 0 && detail.state?.report.summary && <details className="legacy-report"><summary>查看已有研究结果</summary><Markdown text={detail.state.report.summary} /></details>}{paper.sections.map(section => <section className="paper-section" key={section.id}><div className="paper-section-header"><h2>{section.title}</h2><Space size={4}>{section.author === 'user' && <Tag color="blue">你的文字</Tag>}{section.suggestion && <Tag color="gold">有 AI 建议</Tag>}{section.stale && <Tag color="orange">来源待重验</Tag>}<Button type="text" size="small" icon={<EditOutlined />} onClick={() => { beginEdit(section); }}>编辑</Button></Space></div><BlurChange value={section.revision}>{section.markdown ? <Markdown text={section.markdown} /> : <p className="section-placeholder">等待对应研究产出，你也可以从这里开始写。</p>}</BlurChange>{Boolean(section.markdown) && <div className="section-provenance"><span>{section.author === 'user' ? '你保存的版本' : 'AI 候选文本'} · v{section.revision} · {section.evidenceIds.length ? `${section.evidenceIds.length} 条证据关联，仍需核验` : '无证据，待验证'}</span><Button type="link" size="small" onClick={() => setEvidenceSection(section)}>查看来源</Button></div>}</section>)}</div><div className="manuscript-review"><p>最终取舍由你决定。草稿的确认不会自动消除证据缺口。</p><Button type="primary" disabled={!paper.coverage.written || Boolean(pending)} onClick={() => { setApproval(true); setAcknowledge(false); setEditorError(''); }}>审阅并确认当前草稿</Button></div></article>}
        {view === 'experiments' && <div className="experiment-workspace"><div className="section-heading"><div><span className="eyebrow">把想法变成可检验的结果</span><h2>实验台</h2></div><Tag>{paper.coverage.executedExperiments} / {paper.coverage.experiments} 已实际执行</Tag></div><p className="muted">协议、过程与结果放在一起。成功执行代码与假设成立分别判断。</p>{paper.experiments.length ? paper.experiments.map(experiment => <article className="experiment-card" key={experiment.nodeId}><div className="paper-section-header"><h3>{experiment.title}</h3><Tag color={experiment.executionStatus === 'executed' ? 'green' : experiment.executionStatus === 'failed' ? 'red' : 'default'}>{experiment.executionStatus === 'executed' ? '有执行凭据' : experiment.executionStatus === 'failed' ? '执行失败' : '尚无执行凭据'}</Tag></div><dl className="protocol-list">{Object.entries(protocolLabels).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{protocolValue(experiment.design[key])}</dd></div>)}</dl>{experiment.summary && <Markdown text={experiment.summary} />}{experiment.unresolved.length > 0 && <Alert type="warning" title="仍需验证" description={experiment.unresolved.join('；')} />}<div className="experiment-actions"><Button onClick={() => openNode(experiment.nodeId)}>查看节点与实验依据</Button><span>{experiment.receipts.length} 次本轮进程记录</span></div></article>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成实验任务；方案形成后，协议与实际执行记录会出现在这里。" />}</div>}
        {view === 'claims' && <div className="claim-workspace"><div className="section-heading"><div><span className="eyebrow">每个主张都有来处</span><h2>论点与证据</h2></div><Tag>{paper.coverage.linkedClaims} / {paper.coverage.claims} 有证据关联</Tag></div><p className="muted">关联证据需要支持具体表述；有引用不代表结论已经成立。</p>{paper.claims.length ? paper.claims.map((claim, index) => <article className="claim-ledger-card" key={claim.id}><div className="claim-index">{String(index + 1).padStart(2, '0')}</div><div><p>{claim.text}</p><Space wrap><Tag color={claim.evidenceIds.length ? 'blue' : 'orange'}>{claim.evidenceIds.length ? `${claim.evidenceIds.length} 条证据` : '无证据'}</Tag>{claim.experimentEvidenceIds.length > 0 && <Tag>含实验凭据</Tag>}<Button type="link" size="small" onClick={() => openNode(claim.nodeId!)}>来源节点 / 质疑这个论点</Button></Space>{claim.limitations && <p className="muted">{claim.limitations}</p>}{claim.evidenceIds.length > 0 && detail.state && <details><summary>展开证据位置</summary><TaskEvidence evidence={detail.state.evidence.filter(e => claim.evidenceIds.includes(e.id))} state={detail.state} onPaper={onPaper} /></details>}</div></article>) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前还没有候选论点。研究节点会在取得材料后提出可追溯的判断。" />}</div>}
      </div>
      {terminalOpen && view !== 'desk' && <ResearchTerminal detail={detail} openNode={openNode} />}
    </section>{showCompanion && <aside className="research-companion"><div className="companion-heading"><BulbOutlined /><strong>你的研究搭档</strong><Tag>第 {detail.task.round} 轮</Tag></div><div className="research-health"><span>论文与实验进展</span><div><strong><BlurText kind="status" text={String(paper.coverage.written)} /></strong><span>章节</span><strong><BlurText kind="status" text={String(paper.coverage.executedExperiments)} /></strong><span>已执行实验</span></div></div>{pending && <div className="decision-callout"><Tag color="gold">等你决定</Tag><h3>{pending.question}</h3><p>{pending.rationale}</p><Button type="primary" block onClick={() => setDecisionOpen(true)}>查看选项与影响</Button></div>}<Tabs activeKey={side} onChange={setSide} items={[{ key: 'conversation', label: '协作动态', children: <div className="companion-feed">{[...detail.messages].slice(-20).reverse().map(entry => <article className={`companion-message ${entry.role}`} key={entry.id}><div><strong>{entry.role === 'user' ? '你' : entry.role === 'assistant' ? '研究搭档' : '系统'}</strong><time>{timeLabel(entry.at)}</time></div><Markdown text={entry.content.length > 1800 ? entry.content.slice(0, 1800) + '…' : entry.content} /></article>)}</div> }, { key: 'contributions', label: `你的贡献 ${paper.decisions.length}`, children: <div className="contribution-feed">{[...paper.decisions].reverse().map(item => <article key={item.id}><time>{timeLabel(item.at)}</time><h3>{item.title}</h3><p>{item.answer}</p><div className="contribution-effect">{item.effect}</div></article>)}{!paper.decisions.length && <p className="muted">你选择的方向、提出的质疑和写下的文字，会在这里留下影响记录。</p>}</div> }]} /><div className="companion-footer">想改变方向或提出新想法，直接在下方告诉我。</div></aside>}</div>
    <Drawer open={Boolean(editor)} title={editor?.section.title} size={720} onClose={() => setEditor(null)} extra={<Button type="primary" loading={busy === 'section'} onClick={() => { void saveSection(); }}>保存我的文字</Button>} destroyOnHidden><p className="muted">你的编辑会成为正文，AI 后续只提出建议。保存后进入其他节点的研究上下文。</p>{editor && <Input.TextArea aria-label="在线编辑论文章节" value={editor.text} disabled={busy === 'section'} onChange={e => changeText(e.target.value)} autoSize={{ minRows: 16, maxRows: 32 }} className="paper-editor" />}{editorError && <Alert type="error" title={editorError} description="当前输入已保留。请核对服务器的新版本后，再用你的文字保存。" />}{editorError && editor && <Button style={{ marginTop: 12 }} onClick={() => { const latest = paper.sections.find(s => s.id === editor.section.id); if (latest) { setEditor(current => current ? { ...current, section: latest } : null); setEditorError('已采用当前版本号；请先核对最新正文，再保存你的文字。'); } }}>保留文字，与最新版本对照</Button>}{editorError && editor && <details><summary>服务端当前正文</summary><Markdown text={paper.sections.find(s => s.id === editor.section.id)?.markdown || '尚无正文'} /></details>}{editor?.section.suggestion && <div className="paper-suggestion"><h3>AI 提出的修改建议</h3><Markdown text={editor.section.suggestion.markdown} /><Button onClick={() => { void saveSection(true); }} loading={busy === 'section'}>用此建议替换正文</Button></div>}</Drawer>
    <Drawer open={Boolean(evidenceSection)} title={`「${evidenceSection?.title || ''}」的来源`} onClose={() => setEvidenceSection(null)} size={600}><p className="muted">这是章节层面的来源关联，需核对证据是否支持具体表述。</p>{evidenceSection?.sourceNodeIds.map(id => <Button key={id} onClick={() => openNode(id)}>打开来源节点</Button>)}{detail.state && evidenceSection?.evidenceIds.length ? <TaskEvidence evidence={detail.state.evidence.filter(e => evidenceSection.evidenceIds.includes(e.id))} state={detail.state} onPaper={onPaper} /> : <Alert type="warning" title="无证据" description="本段尚无可定位证据。" />}</Drawer>
    <Modal open={decisionOpen && Boolean(pending)} title="这一步，由你决定" onCancel={() => setDecisionOpen(false)} width={660} footer={<Space><Button onClick={() => setDecisionOpen(false)}>稍后决定，保持暂停</Button><Button type="primary" disabled={option === undefined && !note.trim()} loading={busy === 'decision' || busy === 'impact'} onClick={() => { void (impact ? choose() : reviewImpact()); }}>{impact ? '确认影响并继续研究' : '查看选择会影响什么'}</Button></Space>}><h3>{pending?.question}</h3><p className="muted">{pending?.rationale}</p><div className="decision-options">{pending?.options.map((item, index) => <button className={option === index ? 'selected' : ''} key={index} onClick={() => { setOption(index); setImpact(null); }}><strong>{item.label}</strong><span>{item.effect}</span></button>)}</div><Input.TextArea aria-label="我的研究判断" placeholder="也可以给出你的判断，或补充约束…" value={note} onChange={e => { setNote(e.target.value); setImpact(null); }} autoSize={{ minRows: 2, maxRows: 5 }} />{impact && <Alert type="warning" showIcon title={`将重新验证 ${impact.affectedIds.length} 个相关节点（含 ${impact.downstreamCount} 个下游或汇总任务）`} description="已完成结果保留在历史中，来源发生变化的论文章节会标记为待重验。" />}{decisionError && <Alert type="error" title={decisionError} />}</Modal>
    <Modal open={approval} title="审阅当前论文草稿" onCancel={() => setApproval(false)} onOk={() => { void approve(); }} confirmLoading={busy === 'approve'} okText="确认这个版本" cancelText="继续修改" okButtonProps={{ disabled: Boolean(paper.issues.length && !acknowledge) }}><p>确认版本 v{paper.revision}。你仍可继续研究或修改；新内容产生后需要重新确认。</p>{paper.issues.length > 0 && <><Alert type="warning" title={`${paper.issues.length} 项尚待处理`} description={<ul className="approval-issues">{paper.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>} /><Checkbox checked={acknowledge} onChange={e => setAcknowledge(e.target.checked)}>我已看到这些缺口，作为当前草稿保留</Checkbox></>}{editorError && <Alert type="error" title={editorError} />}</Modal>
  </div>;
}

function ResearchTerminal({ detail, openNode }: { detail: TaskDetail; openNode: (id: string) => void }) {
  const [tab, setTab] = useState('logs'); const [selectedRun, setSelectedRun] = useState(''); const [output, setOutput] = useState('');
  const [expanded, setExpanded] = useState(false); const [artifact, setArtifact] = useState<{ name: string; text: string } | null>(null);
  const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  const runs = executionEntries(detail.state);
  const run = runs.find(r => r.id === selectedRun) || runs[0];
  const active = detail.state?.nodes.filter(n => n.active && n.status === 'running') || [];
  useEffect(() => {
    let disposed = false;
    if (!run || tab !== 'output') return;
    setOutput(''); setError(''); setLoading(true);
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const value = await api<{ stdout: string; stderr: string }>(taskPath(detail.task.id, `/paper/terminal?execution=${encodeURIComponent(run.id)}`));
        if (!disposed) { setOutput(`${value.stdout || '（此进程没有标准输出）'}${value.stderr ? '\n\n── stderr ──\n' + value.stderr : ''}`); setError(''); }
      } catch (error) { if (!disposed) setError(messageOf(error)); }
      finally { if (!disposed) { setLoading(false); if (run.execution.status === 'running') timer = setTimeout(read, 2000); } }
    };
    void read();
    return () => { disposed = true; clearTimeout(timer); };
  }, [detail.task.id, run?.id, run?.execution.status, tab]);
  const preview = async (item: TaskDetail['artifacts'][number]) => {
    setLoading(true); setError('');
    try { const response = await fetch(item.url); if (!response.ok) throw new Error('读取产物失败'); const text = await response.text(); setArtifact({ name: item.name, text: text.slice(0, 100000) + (text.length > 100000 ? '\n[预览已截断，请下载完整文件]' : '') }); }
    catch (e) { setError(messageOf(e)); } finally { setLoading(false); }
  };
  const content = <div className="research-terminal"><div className="terminal-header"><div><CodeOutlined /><strong>实验终端</strong><span>{active.length ? `${active.length} 个节点运行中` : '真实执行记录'}</span></div><Button type="text" size="small" aria-label="展开实验终端" icon={<ExpandOutlined />} onClick={() => setExpanded(v => !v)} /></div><Tabs size="small" activeKey={tab} onChange={setTab} items={[{ key: 'logs', label: '任务日志', children: <div className="terminal-logs" role="log" aria-label="实时科研执行日志">{active.map(node => <div className="terminal-active" key={node.id}><Spin size="small" /><button onClick={() => openNode(node.id)}>{node.role} · {node.title}</button><span>{Math.round(node.elapsedMs / 1000)}s</span></div>)}{(detail.state?.activities || []).slice(-70).reverse().map(entry => <div className={`terminal-line ${entry.level === 'error' ? 'error' : ''}`} key={entry.id}><time>{timeLabel(entry.at)}</time><span>{entry.actor}</span><button onClick={() => entry.nodeId && openNode(entry.nodeId)}><BlurText kind="status" text={entry.message} /></button></div>)}{!detail.state?.activities.length && <p>等待检索与研究节点的真实执行记录。</p>}</div> },
      { key: 'output', label: `进程输出 ${runs.length}`, children: <div className="process-output"><Select aria-label="选择真实执行进程" value={run?.id} onChange={setSelectedRun} placeholder="尚无本机进程记录" options={runs.map(r => ({ value: r.id, label: `${r.execution.tool} · ${r.execution.status} · ${timeLabel(r.at)}${r.valid ? '' : ' · 历史失效'}` }))} style={{ width: '100%' }} />{run && <div className="receipt-line">退出码 {run.execution.returnCode ?? '—'} · {((run.execution.elapsedMs || 0) / 1000).toFixed(1)} 秒 · {run.valid ? '记录已保留，科学结果另行核验' : '已失效的历史执行'}</div>}{loading ? <Spin size="small" /> : <pre>{output || '实验执行后可在这里查看 stdout / stderr。'}</pre>}</div> },
      { key: 'artifacts', label: `产物 ${detail.artifacts.length}`, children: <div className="terminal-artifacts">{detail.artifacts.length ? detail.artifacts.map((item, index) => <div key={`${item.url}-${index}`}><FileTextOutlined /><Tooltip title={item.url}><span>{item.name}</span></Tooltip><Space>{/\.(json|csv|txt|py|md|tex|bib)$/i.test(item.name) && <Button type="text" size="small" onClick={() => { void preview(item); }}>查看</Button>}<Button type="link" size="small" href={item.url}>下载</Button></Space></div>) : <p>代码、原始数据、图表和执行凭据会保存在这里。</p>}</div> }]} />{error && <Alert type="error" title={error} />}</div>;
  return <><div className="terminal-dock">{!expanded && content}</div><Drawer open={expanded} title="实验终端与产物" placement="bottom" size="75vh" onClose={() => setExpanded(false)}>{expanded && content}</Drawer><Drawer open={Boolean(artifact)} title={artifact?.name} size={760} onClose={() => setArtifact(null)}><pre className="artifact-preview">{artifact?.text}</pre></Drawer></>;
}
