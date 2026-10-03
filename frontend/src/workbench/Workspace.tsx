import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { App as AntdApp, Drawer, Dropdown, Modal } from 'antd';
import { api, messageOf, taskPath, useTaskWorkspace } from '../taskApi';
import { useDocumentDraft } from '../useDocumentDraft';
import { modelEnabled } from '../providerState';
import { TaskNodeDrawer, TaskPaperDrawer } from '../TaskOverlays';
import { HistoricalReport } from '../ConversationParts';
import type { ResearchNode, Settings } from '../types';
import type { TaskDetail } from '../taskTypes';
import type { VisualNode } from '../graphData';
import type { Artifact, Interaction, InteractionRequest, ProposalReview } from './types';
import { shortTime, canControlResearch, interactionPayload } from './state';
import { Avatar, Badge, Button, Composer, Empty, ErrorNote, Icon } from './ui';
import { ClaimsView, PapersView, ProcessView } from './Board';
import { Notebook } from './Notebook';
import { Preparation } from './Preparation';
import { ImpactReview } from './Discussion';
import { ExperimentsView, MaterialsDrawer } from './Experiments';
import { ArtifactInspector, ModelSettings } from './Inspectors';
import { Checkpoint } from './Checkpoint';
import { AgentStructure } from './AgentStructure';
import { ConversationPane } from './ConversationPane';
import type { TopicSelectionRequest } from './topicSelectionState';
import { executionLabel, retryNodeRequest } from './executionState';
import { ResearchOutputs } from './ResearchOutputs';
import { PaneDivider } from './PaneDivider';
import { DEFAULT_PANE_PERCENT } from './splitPaneState';
import { TaskNavigation } from './TaskNavigation';
import { GlassButton, GlassGroup, WindowBackdrop } from './GlassChrome';
import { AppearanceControl } from './AppearanceControl';
import { EntrySurface, WorkspaceTabs } from './WorkspaceChrome';
import { contextForNode, contextIsCurrent, overviewArtifact, type ResearchContext } from './conversationState';

interface ConversationDraft { text: string; context: ResearchContext | null; intent: 'ask' | 'deepen' | 'next_round' }

export function Workspace() {
  const workspace = useTaskWorkspace(); const { detail } = workspace; const editor = useDocumentDraft(detail, workspace.accept);
  const { modal } = AntdApp.useApp();
  const [panePercent, setPanePercent] = useState(DEFAULT_PANE_PERCENT);
  const [glassTint, setGlassTint] = useState(35);
  const [reduceTransparency, setReduceTransparency] = useState(() => window.matchMedia('(prefers-reduced-transparency: reduce)').matches);
  const [settings, setSettings] = useState<Settings | null>(null); const [settingsOpen, setSettingsOpen] = useState(false);
  const [composer, setComposer] = useState(''); const drafts = useRef(new Map<string, ConversationDraft>()); const composerTask = useRef(workspace.selectedId || 'new'); const composerRef = useRef(composer); composerRef.current = composer;
  const [busy, setBusy] = useState(''); const [error, setError] = useState('');
  const [sidebar, setSidebar] = useState(() => window.innerWidth >= 1180); const [workOpen, setWorkOpen] = useState(true); const [smallWork, setSmallWork] = useState(false);
  const [query, setQuery] = useState(''); const [searching, setSearching] = useState(false);
  const [context, setContext] = useState<ResearchContext | null>(null); const [intent, setIntent] = useState<'ask' | 'deepen' | 'next_round'>('ask');
  const currentDraft = useRef<ConversationDraft>({ text: composer, context, intent }); currentDraft.current = { text: composer, context, intent };
  const attachAfterCreate = useRef<string | null>(null);
  const [workspaceView, setWorkspaceView] = useState<'structure' | 'outputs'>('structure');
  const [tool, setTool] = useState(''); const [selectedNode, setSelectedNode] = useState<VisualNode | null>(null); const [selectedPaper, setSelectedPaper] = useState<string | null>(null); const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [review, setReview] = useState<ProposalReview | null>(null); const [materialsOpen, setMaterialsOpen] = useState(false); const [historyOpen, setHistoryOpen] = useState(false);
  const [notebookDirty, setNotebookDirty] = useState(false); const [notebookEditorOpen, setNotebookEditorOpen] = useState(false);
  const [historic, setHistoric] = useState<{ round: number; data?: Record<string, unknown>; error?: string } | null>(null);
  const selectedId = useRef(workspace.selectedId); selectedId.current = workspace.selectedId;
  const actionLock = useRef(false); const mounted = useRef(true);
  const modelReady = modelEnabled(settings, detail?.modelReady);
  const preparing = !detail || ['empty', 'requirements'].includes(detail.phase);
  const empty = preparing && !detail?.messages.length && !detail?.document.markdown && !detail?.document.polishing;
  const contextCurrent = !context || !detail || contextIsCurrent(context, detail);
  useEffect(() => { mounted.current = true; let live = true; const refresh = () => { void api<Settings>('/settings').then(s => { if (live) setSettings(s); }).catch(() => {}); }; refresh(); window.addEventListener('focus', refresh); return () => { live = false; mounted.current = false; window.removeEventListener('focus', refresh); }; }, []);
  useEffect(() => {
    const id = workspace.selectedId || 'new'; if (composerTask.current !== id) { drafts.current.set(composerTask.current, currentDraft.current); const saved = drafts.current.get(id); setComposer(saved?.text || ''); setContext(saved?.context || null); setIntent(saved?.intent || 'ask'); composerTask.current = id; }
    setSelectedNode(null); setSelectedPaper(null); setSelectedArtifact(null); setReview(null); setMaterialsOpen(attachAfterCreate.current === id); attachAfterCreate.current = null; setHistoryOpen(false); setHistoric(null); setError(''); setNotebookEditorOpen(false); setTool(''); setSmallWork(false);
    if (window.innerWidth < 1180) setSidebar(false);
  }, [workspace.selectedId]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const overlay = Array.from(document.querySelectorAll<HTMLElement>('.ant-modal-wrap, .ant-drawer-open, .ant-popover')).some(el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden');
      if (overlay) return;
      if ((event.metaKey || event.ctrlKey) && !event.altKey && !(event.target as Element).closest('.cm-editor')) {
        if (event.key.toLowerCase() === 'k') { event.preventDefault(); setSmallWork(false); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[aria-label="与研究助手对话"]')?.focus()); }
        else if (event.key === ',') { event.preventDefault(); setSettingsOpen(true); }
        else if (event.key === '\\') { event.preventDefault(); setSidebar(current => !current); }
      }
      if (sidebar && window.innerWidth < 1180) {
        if (event.key === 'Escape') { setSidebar(false); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('[aria-label="展开侧边栏"]')?.focus()); }
        if (event.key === 'Tab') {
          const controls = Array.from(document.querySelectorAll<HTMLElement>('.sw-sidebar button:not(:disabled), .sw-sidebar input')).filter(el => el.getClientRects().length > 0);
          const first = controls[0], last = controls.at(-1), active = document.activeElement;
          if (first && last && (event.shiftKey ? active === first || !controls.includes(active as HTMLElement) : active === last || !controls.includes(active as HTMLElement))) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
        }
      }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, [sidebar]);
  useEffect(() => {
    const media = window.matchMedia('(min-width: 1180px)');
    const adapt = (event: MediaQueryListEvent) => setSidebar(event.matches);
    media.addEventListener('change', adapt); return () => media.removeEventListener('change', adapt);
  }, []);
  useEffect(() => { document.title = detail?.task.title ? `${detail.task.title} · 科研蜂群` : '科研蜂群'; }, [detail?.task.title]);
  useEffect(() => { setWorkspaceView(detail?.phase === 'completed' ? 'outputs' : 'structure'); }, [detail?.task.id, detail?.phase]);
  const guarded = async (name: string, work: () => Promise<void>) => { if (actionLock.current) return; actionLock.current = true; setBusy(name); setError(''); const id = workspace.selectedId; try { await work(); } catch (e) { if (selectedId.current === id || name === 'message' || name === 'create') setError(messageOf(e)); } finally { actionLock.current = false; if (mounted.current) setBusy(''); } };
  const switchTask = async (id: string | null) => {
    if (busy || id === workspace.selectedId && id !== null) return;
    await guarded('switch', async () => {
      if (notebookDirty) { setNotebookEditorOpen(true); throw new Error('研究草稿还有未确认的编辑，请先提交修改或撤销编辑。'); }
      if (!await editor.save()) throw new Error('需求文档还有未保存的文字，请保存后再切换。');
      workspace.choose(id);
    });
  };
  const createFromEntry = async (attach = false) => {
    const task = await api<TaskDetail>('/tasks', {});
    // Migrate the live input before selecting the new task. A failed first request keeps it here.
    if (selectedId.current === null) {
      drafts.current.set(task.task.id, currentDraft.current); drafts.current.delete('new');
      composerTask.current = task.task.id; selectedId.current = task.task.id;
      if (attach) attachAfterCreate.current = task.task.id;
      workspace.choose(task.task.id); workspace.accept(task);
    }
    return task;
  };
  const focusComposer = () => { setSmallWork(false); window.requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[aria-label="与研究助手对话"]')?.focus()); };
  const discussNode = (node: ResearchNode, artifact?: Artifact) => {
    if (!detail) return; const next = contextForNode(detail, node.id) || (artifact ? { nodeId: node.id, artifact } : null);
    if (!next) { setError('这个节点尚未生成可引用的记录，请稍后刷新。'); return; }
    setContext(next); setIntent('ask'); focusComposer();
  };
  const discussArtifact = (artifact: Artifact) => { setContext({ artifact, ...(artifact.nodeIds.length === 1 ? { nodeId: artifact.nodeIds[0] } : {}) }); setIntent('ask'); setSelectedArtifact(null); focusComposer(); };
  const submitInteraction = async (task: TaskDetail, request: InteractionRequest) => {
    const result = await api<Interaction>(taskPath(task.task.id, '/interactions'), request, 60000);
    if (selectedId.current !== task.task.id) return;
    if (result.proposal) setReview({ proposal: result.proposal, request });
    try { workspace.accept(await api<TaskDetail>(taskPath(task.task.id))); }
    catch { setError('消息已提交，暂时无法读取新回复。请刷新状态，无需重新发送。'); }
  };
  const send = () => guarded('message', async () => {
    const text = composerRef.current.trim(); if (!text) return;
    if (notebookDirty) { setNotebookEditorOpen(true); throw new Error('请先提交或撤销研究草稿的修改，再继续对话。'); }
    if (!await editor.save()) throw new Error('请先处理需求文档版本差异，再继续对话。');
    const task = detail || await createFromEntry(); const id = task.task.id;
    if (['empty', 'requirements'].includes(task.phase) || intent === 'next_round') {
      if (intent === 'next_round' && task.phase !== 'completed') throw new Error('研究状态已变化，请查看当前任务后再开始下一轮。');
      workspace.accept(await api<TaskDetail>(taskPath(id, '/messages'), { text, ...(intent === 'next_round' ? { startNewRound: true, expectedRevision: task.document.revision } : {}) }, 60000)); setIntent('ask'); setContext(null);
    } else if (task.state?.project.researchCycle?.topicSelection?.status === 'pending') {
      workspace.accept(await api<TaskDetail>(taskPath(id, '/topic-discussion'), { text, expectedRevision: task.state.revision }, 60000));
    } else {
      const artifact = context?.artifact || overviewArtifact(task);
      if (!artifact) throw new Error('当前研究记录还在建立，请稍后再发。');
      if (intent === 'deepen' && (!context || !contextCurrent)) throw new Error('研究内容已有更新，请先引用最新版本再查看调整影响。');
      const request = interactionPayload(artifact, intent === 'deepen' ? 'deepen' : 'ask', text, '', context?.nodeId || '');
      await submitInteraction(task, { ...request, showInConversation: true, scope: context?.nodeId ? 'node' : 'overview', ...(context?.nodeId ? { nodeId: context.nodeId } : {}) });
    }
    const saved = drafts.current.get(id); if (saved?.text.trim() === text) drafts.current.set(id, { ...saved, text: '' });
    if (selectedId.current === id) setComposer(current => current.trim() === text ? '' : current);
  });
  const retryInteraction = (item: Interaction) => guarded('message', async () => {
    if (!detail) return;
    if (detail.state?.project.researchCycle?.topicSelection?.status === 'pending') {
      workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/topic-discussion'), { text: item.text, expectedRevision: detail.state.revision }, 60000));
    } else {
      await submitInteraction(detail, { kind: 'ask', text: item.text, target: item.target, showInConversation: true, scope: item.context?.scope, nodeId: item.context?.nodeId });
    }
  });
  const decide = (choice: { decisionId: string; expectedRevision: number; optionIndex: number }) => guarded('decision', async () => { if (detail) workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/research-choice'), choice, 60000)); });
  const selectTopic = (choice: TopicSelectionRequest) => guarded('topic', async () => { if (detail?.state) workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/topic-selection'), choice, 60000)); });
  const start = () => guarded('start', async () => { if (!detail || !await editor.save()) return; const latest = await api<TaskDetail>(taskPath(detail.task.id)); workspace.accept(latest); if (latest.document.polishing) throw new Error('需求仍在整理，完成后即可开始。'); if (!editor.isCurrentSaved(detail.task.id)) throw new Error('需求文档有新的编辑，请保存后再开始研究。'); workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/start'), { expectedRevision: latest.document.revision }, 60000)); });
  const mode = (taskMode: 'research' | 'reproduction') => guarded('mode', async () => { if (!detail || !await editor.save()) return; workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/task-mode'), { taskMode })); });
  const action = (name: 'pause' | 'resume') => guarded(name, async () => { if (detail && canControlResearch(detail)) workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, `/actions/${name}`), {})); });
  const retryNode = (node: ResearchNode) => guarded('node-retry', async () => { if (detail) workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/retry'), retryNodeRequest(node), 60000)); });
  const inspectNode = (node: ResearchNode) => setSelectedNode({ id: node.id, nodeId: node.id, sourceKind: 'agent', active: node.active, title: node.title, status: node.status, action: node.role });
  const exportTask = () => guarded('export', async () => { if (!detail) return; const response = await fetch(`/api${taskPath(detail.task.id, '/export')}`); if (!response.ok) { const result = await response.json(); throw new Error(result.error || '导出失败'); } const url = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href = url; a.download = `科研任务-第${detail.task.round}轮.zip`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 60000); });
  const openRun = async (round: number) => { if (!detail) return; const id = detail.task.id; setHistoric({ round }); try { const data = await api<Record<string, unknown>>(taskPath(id, `/runs/${round}`)); if (selectedId.current === id) setHistoric({ round, data }); } catch (e) { if (selectedId.current === id) setHistoric({ round, error: messageOf(e) }); } };
  const rollback = (checkpointId: string) => { if (!detail) return; modal.confirm({ title: '回到这个历史检查点？', content: '当前检查点之后的分支将回滚。执行记录和历史产物仍保留。', okText: '确认回滚', cancelText: '取消', onOk: () => guarded('rollback', async () => { workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/rollback'), { checkpointId })); setHistoryOpen(false); }) }); };
  const onNotebookDirty = useCallback((dirty: boolean) => setNotebookDirty(dirty), []);
  const tasks = workspace.tasks.filter(t => t.title.toLowerCase().includes(query.toLowerCase()));
  const boardProps = detail ? { detail, onProposal: setReview, onInspect: setSelectedArtifact, onNode: setSelectedNode, onPaper: setSelectedPaper, onTab: setTool, onExport: () => { void exportTask(); }, onContinue: focusComposer } : null;
  const currentError = error || workspace.error;
  const liveAgents = detail?.state?.nodes.filter(n => n.active && !n.input.superseded && n.status === 'running' && !n.modelWait) || [];
  const composerElement = <Composer value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message'} onAttach={() => setMaterialsOpen(true)} placeholder={intent === 'next_round' ? '下一轮，你想继续研究什么？' : context ? (context.nodeId ? '继续讨论这个节点…' : '继续讨论这份内容…') : '继续讨论…'} context={context ? <><button className="sw-context-target" title="查看引用内容" onClick={() => setSelectedArtifact(context.artifact)} type="button"><Icon name="link" /><span>{context.nodeId ? detail?.state?.nodes.find(n => n.id === context.nodeId)?.title || context.artifact.title : context.artifact.title}</span><span>v{context.artifact.revision}</span></button><Button type="button" icon="close" aria-label="取消节点引用" onClick={() => { setContext(null); setIntent('ask'); }} />{!contextCurrent && <div className="sw-context-update">内容已更新 <button type="button" onClick={() => { const artifact = detail?.workbench?.artifacts.find(a => a.id === context.artifact.id); if (artifact) setContext({ ...context, artifact }); }}>使用最新版本</button></div>}</> : intent === 'next_round' ? <><span>下一轮研究</span><Button type="button" icon="close" aria-label="取消下一轮研究" onClick={() => setIntent('ask')} /></> : undefined} controls={context?.nodeId && <select aria-label="对话操作" value={intent} onChange={e => setIntent(e.target.value as 'ask' | 'deepen')}><option value="ask">讨论</option><option value="deepen">深入研究</option></select>} />;
  const conversation = detail && <ConversationPane detail={detail} composer={composerElement} sending={!!busy && ['message', 'decision', 'topic', 'node-retry'].includes(busy)} onProposal={setReview} onRetry={item => { void retryInteraction(item); }} onDecision={choice => { void decide(choice); }} onTopicSelection={choice => { void selectTopic(choice); }} onRetryNode={node => { void retryNode(node); }} onInspectNode={inspectNode} />;


  return <div className={`sw-workspace sw-production sw-macos ${sidebar ? 'has-sidebar' : ''} ${workOpen ? 'has-workspace' : ''} ${smallWork ? 'mobile-workspace' : ''} ${preparing ? 'is-preparing' : ''}`} data-reduce-transparency={reduceTransparency} style={{ '--sw-conversation-width': `${panePercent}%`, '--sw-glass-alpha': .42 + glassTint * .0048, '--sw-glass-blur': `${18 + glassTint * .12}px` } as CSSProperties}>
    <WindowBackdrop />
    {sidebar && <button className="sw-nav-scrim" aria-label="关闭任务导航" onClick={() => setSidebar(false)} />}
    <TaskNavigation open={sidebar} busy={!!busy} searching={searching} query={query} tasks={tasks} selectedId={workspace.selectedId} loading={workspace.listLoading} hasTask={!!detail} modelReady={modelReady}
      onClose={() => { setSidebar(false); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('[aria-label="展开侧边栏"]')?.focus()); }}
      onSearch={() => setSearching(!searching)} onQuery={setQuery} onChoose={id => { void switchTask(id); }} onNew={() => { void switchTask(null); }}
      onLibrary={() => setTool('papers')} onExperiments={() => setTool('experiments')} onSettings={() => setSettingsOpen(true)} />
    <main className="sw-main">
      <header className="sw-shell-header"><div className="sw-shell-title">{!sidebar && <Button icon="panel" aria-label="展开侧边栏" onClick={() => setSidebar(true)} />}<div className="sw-title-stack"><h2 title={detail?.task.title}>{detail?.task.title || '新的研究'}</h2><span>{detail ? `第 ${detail.task.round} 轮研究` : '你的个人科研工作区'}</span></div></div>
        <div className="sw-shell-actions">
          {!preparing && detail && <><div className="sw-live-agents">{liveAgents.slice(0, 3).map((node, index) => <button key={node.id} title={node.title + '：' + (node.logs.at(-1)?.message || node.role)} aria-label={'查看节点：' + node.title} onClick={() => setSelectedNode({ id: node.id, nodeId: node.id, sourceKind: 'agent', active: node.active, title: node.title, status: node.status, action: node.role })}><Avatar small index={index} /></button>)}{executionLabel(detail.state) && <span>{executionLabel(detail.state)}</span>}</div>{canControlResearch(detail) ? <Button aria-label={detail.state?.paused ? '继续' : '暂停'} className="sw-research-control-button" icon={detail.state?.paused ? 'play' : 'pause'} busy={busy === 'pause' || busy === 'resume'} disabled={!!busy} onClick={() => { void action(detail.state?.paused ? 'resume' : 'pause'); }}><span className="sw-control-label">{detail.state?.paused ? '继续' : '暂停'}</span></Button> : <Badge status={detail.phase} />}</>}
          <GlassGroup className="sw-toolbar-group" label="视图与工具">
            <AppearanceControl tint={glassTint} onTint={setGlassTint} reduced={reduceTransparency} onReduced={setReduceTransparency} />
            <GlassButton className="sw-toolbar-settings" icon="settings" aria-label="打开模型设置" title="模型与设置（⌘/Ctrl + ,）" onClick={() => setSettingsOpen(true)} />
            {!empty && <><GlassButton className="sw-work-toggle" icon="panel" aria-pressed={workOpen} aria-label={workOpen ? '收起工作区' : '打开工作区'} title={workOpen ? '收起工作区' : '打开工作区'} onClick={() => setWorkOpen(!workOpen)} /><GlassButton className="sw-mobile-work-toggle" aria-pressed={smallWork} icon={smallWork ? 'chat' : 'panel'} onClick={() => setSmallWork(!smallWork)}>{smallWork ? '对话' : '工作区'}</GlassButton><Dropdown trigger={['click']} menu={{ items: [
            ...(!preparing ? [{ key: 'structure', label: '研究结构', onClick: () => { setWorkspaceView('structure'); setWorkOpen(true); setSmallWork(true); } }, { key: 'output', label: '研究产物', onClick: () => { setWorkspaceView('outputs'); setWorkOpen(true); setSmallWork(true); } }, { key: 'claims', label: '主张与证据', onClick: () => setTool('claims') }, { key: 'process', label: '完整运行记录', onClick: () => setTool('process') }, { key: 'draft', label: '修改研究需求', onClick: () => setNotebookEditorOpen(true) }] : []),
            ...(detail?.phase === 'completed' ? [{ key: 'round', label: '开始下一轮研究', onClick: () => { setContext(null); setIntent('next_round'); focusComposer(); } }] : []),
            { key: 'materials', label: '材料与资源', onClick: () => setMaterialsOpen(true) }, { key: 'history', label: '历史与检查点', onClick: () => setHistoryOpen(true) }, { key: 'export', label: '导出研究档案', disabled: preparing || !!busy, onClick: () => { void exportTask(); } },
          ] }}><GlassButton icon="more" aria-label="任务菜单" /></Dropdown></>}
          </GlassGroup>
        </div>
      </header>
      {currentError && <ErrorNote onRetry={() => { setError(''); void workspace.refresh(); }}>{currentError}</ErrorNote>}
      {detail && <Checkpoint key={detail.task.id} detail={detail} accept={workspace.accept} onNotebook={() => { if (preparing) { setWorkOpen(true); setSmallWork(true); } else setNotebookEditorOpen(true); }} />}
      {workspace.loading ? <div className="sw-loading-screen"><span className="sw-spinner" /><span>正在读取</span></div> : empty ? <EntrySurface modelReady={modelReady} onSettings={() => setSettingsOpen(true)} onSuggestion={text => { setComposer(current => current.trim() ? `${current}\n\n${text}` : text); focusComposer(); }}><Composer centered value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message'} placeholder="描述你的问题，或添加一份研究材料…" onAttach={() => { if (detail) setMaterialsOpen(true); else void guarded('create', async () => { await createFromEntry(true); }); }} /></EntrySurface> : preparing && detail ? <Preparation detail={detail} editor={editor} busy={busy} modelReady={modelReady} composer={composerElement} conversation={conversation} separator={<PaneDivider value={panePercent} onChange={setPanePercent} />} onStart={() => { void start(); }} onMode={value => { void mode(value); }} /> : detail && <div className="sw-research-split">{conversation}<PaneDivider value={panePercent} onChange={setPanePercent} /><div className="sw-visual-workspace"><WorkspaceTabs value={workspaceView} onChange={setWorkspaceView} /><div className="sw-workspace-surface" key={workspaceView}>{workspaceView === 'outputs' ? <ResearchOutputs key={detail.task.id} detail={detail} onInspect={setSelectedArtifact} onDiscuss={discussArtifact} onExport={() => { void exportTask(); }} onStructure={() => setWorkspaceView('structure')} /> : <AgentStructure detail={detail} onNode={setSelectedNode} onInspect={setSelectedArtifact} onDiscuss={discussNode} />}</div></div></div>}
    </main>
    <ModelSettings open={settingsOpen} settings={settings} onClose={() => setSettingsOpen(false)} onSaved={value => { setSettings(value); void workspace.refresh(); }} />
    {detail && <div key={detail.task.id}><ArtifactInspector selected={selectedArtifact} detail={detail} onClose={() => setSelectedArtifact(null)} onPaper={setSelectedPaper} onProposal={setReview} onDiscuss={discussArtifact} refresh={workspace.refresh} /><ImpactReview review={review} detail={detail} onClose={() => setReview(null)} onUpdated={setReview} onApplied={workspace.refresh} /><MaterialsDrawer open={materialsOpen} detail={detail} onClose={() => setMaterialsOpen(false)} refresh={workspace.refresh} /><TaskNodeDrawer target={selectedNode} detail={detail} onClose={() => setSelectedNode(null)} onPaper={setSelectedPaper} accept={workspace.accept} /><TaskPaperDrawer paper={detail.state?.papers.find(p => p.id === selectedPaper)} taskId={detail.task.id} state={detail.state} onClose={() => setSelectedPaper(null)} onPaper={setSelectedPaper} />
      {!preparing && <div className="sw-hidden-notebook"><Notebook detail={detail} collapsed onCollapse={() => setNotebookEditorOpen(true)} onProposal={setReview} onDirty={onNotebookDirty} editorOpen={notebookEditorOpen} onEditorOpen={setNotebookEditorOpen} /></div>}
      <Drawer open={!!tool} title={{ claims: '主张与证据', process: '完整运行记录', papers: '资料库', experiments: '实验环境' }[tool] || '研究资料'} width={1000} onClose={() => setTool('')} rootClassName="sw-drawer sw-tool-drawer">{boardProps && (tool === 'claims' ? <ClaimsView {...boardProps} /> : tool === 'papers' ? <PapersView {...boardProps} /> : tool === 'experiments' ? <ExperimentsView detail={detail} onInspect={setSelectedArtifact} refresh={workspace.refresh} onMaterials={() => setMaterialsOpen(true)} /> : tool === 'process' ? <ProcessView {...boardProps} /> : null)}</Drawer>
      <Drawer open={historyOpen} title="历史与检查点" width={520} onClose={() => setHistoryOpen(false)} rootClassName="sw-drawer"><h3>研究轮次</h3>{detail.runs.length ? [...detail.runs].reverse().map(run => <button key={`${run.round}-${run.at}`} className="sw-history-row" onClick={() => { void openRun(run.round); }}><strong>第 {run.round} 轮 · {shortTime(run.at)}</strong><p>{run.summary || '查看本轮结果'}</p><Icon name="arrow" /></button>) : <p className="sw-muted">本轮尚未归档。</p>}<h3>检查点</h3>{detail.state?.checkpoints.map(checkpoint => <div key={checkpoint.id} className="sw-history-row"><strong>{checkpoint.title}</strong><p>{checkpoint.status === 'confirmed' ? '已确认' : checkpoint.status === 'pending' ? '等待处理' : '已被后续版本替代'}</p><Button disabled={!!busy} onClick={() => rollback(checkpoint.id)}>回到此处</Button></div>)}</Drawer>
      <Modal open={!!historic} title={`第 ${historic?.round || ''} 轮研究结果`} width={850} onCancel={() => setHistoric(null)} footer={null} className="sw-modal">{historic?.error ? <ErrorNote>{historic.error}</ErrorNote> : historic?.data ? <HistoricalReport data={historic.data} /> : <Empty title="正在读取历史结果…" />}</Modal>
    </div>}
  </div>;
}
