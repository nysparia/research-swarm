import { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Drawer, Dropdown, Modal } from 'antd';
import { api, messageOf, taskPath, useTaskWorkspace } from '../taskApi';
import { useDocumentDraft } from '../useDocumentDraft';
import { modelEnabled } from '../providerState';
import { TaskNodeDrawer, TaskPaperDrawer } from '../TaskOverlays';
import { HistoricalReport } from '../ConversationParts';
import { Markdown } from '../Markdown';
import { BlurText } from '../BlurReveal';
import type { Settings } from '../types';
import type { TaskDetail } from '../taskTypes';
import type { VisualNode } from '../graphData';
import type { Artifact, ProposalReview } from './types';
import { moduleTabs, shortTime, canControlResearch } from './state';
import { Badge, Button, Composer, Empty, ErrorNote, Icon } from './ui';
import { AgentStrip, Board, ClaimsView, PapersView, ProcessView, ReportView } from './Board';
import { Notebook } from './Notebook';
import { Preparation } from './Preparation';
import { ImpactReview } from './Discussion';
import { ExperimentsView, MaterialsDrawer } from './Experiments';
import { ArtifactInspector, ModelSettings } from './Inspectors';
import { Checkpoint } from './Checkpoint';

export function Workspace() {
  const workspace = useTaskWorkspace(); const { detail } = workspace; const editor = useDocumentDraft(detail, workspace.accept);
  const { modal } = AntdApp.useApp();
  const [settings, setSettings] = useState<Settings | null>(null); const [settingsOpen, setSettingsOpen] = useState(false);
  const [composer, setComposer] = useState(''); const drafts = useRef(new Map<string, string>()); const composerTask = useRef(workspace.selectedId || 'new'); const composerRef = useRef(composer); composerRef.current = composer;
  const [busy, setBusy] = useState(''); const [error, setError] = useState(''); const [tab, setTab] = useState('board');
  const [sidebar, setSidebar] = useState(false); const [notebookCollapsed, setNotebookCollapsed] = useState(() => window.matchMedia('(max-width:900px)').matches); const [notebookDirty, setNotebookDirty] = useState(false);
  const [notebookEditorOpen, setNotebookEditorOpen] = useState(false);
  const [query, setQuery] = useState(''); const [selectedNode, setSelectedNode] = useState<VisualNode | null>(null); const [selectedPaper, setSelectedPaper] = useState<string | null>(null); const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [review, setReview] = useState<ProposalReview | null>(null); const [materialsOpen, setMaterialsOpen] = useState(false); const [historyOpen, setHistoryOpen] = useState(false); const [chatOpen, setChatOpen] = useState(false);
  const [historic, setHistoric] = useState<{ round: number; data?: Record<string, unknown>; error?: string } | null>(null);
  const selectedId = useRef(workspace.selectedId); selectedId.current = workspace.selectedId;
  const actionLock = useRef(false); const mounted = useRef(true);
  const modelReady = modelEnabled(settings, detail?.modelReady);
  const preparing = !detail || ['empty', 'requirements'].includes(detail.phase);
  const empty = preparing && !detail?.messages.length && !detail?.document.markdown && !detail?.document.polishing;
  const tabs = moduleTabs(detail?.workbench, detail?.state); const activeTab = ['claims','process'].includes(tab) || tabs.some(t => t.id === tab) ? tab : 'board';
  useEffect(() => { mounted.current = true; let live = true; const refresh = () => { void api<Settings>('/settings').then(s => { if (live) setSettings(s); }).catch(() => {}); }; refresh(); window.addEventListener('focus', refresh); return () => { live = false; mounted.current = false; window.removeEventListener('focus', refresh); }; }, []);
  useEffect(() => {
    const id = workspace.selectedId || 'new'; if (composerTask.current !== id) { drafts.current.set(composerTask.current, composerRef.current); setComposer(drafts.current.get(id) || ''); composerTask.current = id; }
    setTab('board'); setSelectedNode(null); setSelectedPaper(null); setSelectedArtifact(null); setReview(null); setMaterialsOpen(false); setHistoryOpen(false); setHistoric(null); setChatOpen(false); setError(''); setSidebar(false); setNotebookEditorOpen(false);
  }, [workspace.selectedId]);
  useEffect(() => { document.title = detail?.task.title ? `${detail.task.title} · 科研蜂群` : '科研蜂群 · 从一个问题开始'; }, [detail?.task.title]);
  useEffect(() => { const media = window.matchMedia('(max-width:900px)'); const resize = () => { if (media.matches) setNotebookCollapsed(true); }; media.addEventListener('change', resize); return () => media.removeEventListener('change', resize); }, []);
  const guarded = async (name: string, work: () => Promise<void>) => { if (actionLock.current) return; actionLock.current = true; setBusy(name); setError(''); const id = workspace.selectedId; try { await work(); } catch (e) { if (selectedId.current === id || name === 'message' || name === 'create') setError(messageOf(e)); } finally { actionLock.current = false; if (mounted.current) setBusy(''); } };
  const switchTask = async (id: string | null) => {
    if (busy || id === workspace.selectedId && id !== null) return;
    if (notebookDirty) { setError('研究草稿还有未确认的编辑，请先提交修改或撤销编辑，再切换任务。'); setNotebookEditorOpen(true); return; }
    if (!await editor.save()) { setError('请先处理需求文档的保存或版本差异。'); return; }
    if (id) workspace.choose(id); else await guarded('create', async () => { await workspace.create(); });
  };
  const send = () => guarded('message', async () => {
    const text = composerRef.current.trim(); if (!text) return;
    if (notebookDirty) { setNotebookEditorOpen(true); throw new Error('研究草稿还有未确认的修改。请先提交或撤销这些编辑，再继续对话，避免进入下一轮时丢失文字。'); }
    if (!await editor.save()) throw new Error('请先处理需求文档版本差异，再继续对话。');
    const task = detail || await workspace.create(); const id = task.task.id;
    const next = await api<TaskDetail>(taskPath(id, '/messages'), { text }, 60000); workspace.accept(next);
    drafts.current.delete(id); drafts.current.delete('new');
    if (selectedId.current === id) { setComposer(current => current.trim() === text ? '' : current); setTab('board'); }
  });
  const start = () => guarded('start', async () => { if (!detail || !await editor.save()) return; const latest = await api<TaskDetail>(taskPath(detail.task.id)); workspace.accept(latest); if (latest.document.polishing) throw new Error('需求仍在整理，完成后即可开始。'); workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/start'), { expectedRevision: latest.document.revision }, 60000)); });
  const mode = (taskMode: 'research' | 'reproduction') => guarded('mode', async () => { if (!detail || !await editor.save()) return; workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/task-mode'), { taskMode })); });
  const action = (name: 'pause' | 'resume') => guarded(name, async () => { if (detail && canControlResearch(detail)) workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, `/actions/${name}`), {})); });
  const exportTask = () => guarded('export', async () => { if (!detail) return; const response = await fetch(`/api${taskPath(detail.task.id, '/export')}`); if (!response.ok) { const result = await response.json(); throw new Error(result.error || '导出失败'); } const url = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href = url; a.download = `科研任务-第${detail.task.round}轮.zip`; a.click(); window.setTimeout(() => URL.revokeObjectURL(url), 60000); });
  const openRun = async (round: number) => { if (!detail) return; const id = detail.task.id; setHistoric({ round }); try { const data = await api<Record<string, unknown>>(taskPath(id, `/runs/${round}`)); if (selectedId.current === id) setHistoric({ round, data }); } catch (e) { if (selectedId.current === id) setHistoric({ round, error: messageOf(e) }); } };
  const rollback = (checkpointId: string) => { if (!detail) return; modal.confirm({ title: '回到这个历史检查点？', content: '当前检查点之后的分支将回滚。执行记录和历史产物仍保留。', okText: '确认回滚', cancelText: '取消', onOk: () => guarded('rollback', async () => { workspace.accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/rollback'), { checkpointId })); setHistoryOpen(false); }) }); };
  const onNotebookDirty = useCallback((dirty: boolean) => setNotebookDirty(dirty), []);
  useEffect(() => { setTab(detail?.phase === 'completed' ? 'report' : 'board'); }, [detail?.task.id, detail?.phase]);
  const tasks = workspace.tasks.filter(t => t.title.toLowerCase().includes(query.toLowerCase()));
  const boardProps = detail ? { detail, onProposal: setReview, onInspect: setSelectedArtifact, onNode: setSelectedNode, onPaper: setSelectedPaper, onTab: setTab, onExport: () => {void exportTask();}, onContinue: () => document.querySelector<HTMLTextAreaElement>('[aria-label="与研究助手对话"]')?.focus() } : null;
  const currentError = error || workspace.error;
  const primaryTabs = ['board','report','papers','experiments','paper'].flatMap(id => tabs.filter(t => t.id === id));
  const tabNames: Record<string,string> = {board:'看板',report:'研究报告',papers:'论文索引',experiments:'实验',paper:'论文草稿'};
  const composerElement = <Composer value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message' || busy === 'create'} onAttach={() => setMaterialsOpen(true)} onHistory={preparing ? undefined : () => setChatOpen(true)} />;

  return <div className={`sw-workspace ${sidebar ? 'nav-open' : ''}`}>
    {sidebar && <button className="sw-nav-scrim" aria-label="关闭任务导航" onClick={() => setSidebar(false)} />}
    <aside className="sw-sidebar"><div className="sw-brand"><span><Icon name="layers" /></span><div><strong>科研蜂群</strong><small>让 研 究 更 高 效</small></div></div><Button className="sw-new-task" variant="primary" icon="plus" busy={busy === 'create'} disabled={!!busy} onClick={() => { void switchTask(null); }}>新建科研任务</Button><div className="sw-sidebar-label"><span>我的任务</span><span>{workspace.tasks.length}</span></div><label className="sw-search"><Icon name="search" /><input aria-label="搜索科研任务" placeholder="搜索任务" value={query} onChange={e => setQuery(e.target.value)} /></label><nav aria-label="科研任务列表">{workspace.listLoading ? <p className="sw-loading"><span className="sw-spinner" />读取任务</p> : tasks.map(task => <button key={task.id} className={`sw-task ${task.id === workspace.selectedId ? 'active' : ''}`} disabled={!!busy} onClick={() => { void switchTask(task.id); }}><Icon name="book" /><div><strong>{task.title || '新的科研任务'}</strong><span><Badge status={task.phase} /></span></div></button>)}{!workspace.listLoading && !tasks.length && <p className="sw-sidebar-empty">{query ? '没有匹配的任务' : '你的研究，从一个问题开始。'}</p>}</nav><footer><Button icon="settings" onClick={() => setSettingsOpen(true)}>系统设置</Button><span><i />本地工作区</span></footer></aside>
    <main className={'sw-main ' + (preparing ? 'is-preparing-view ' : '') + (detail?.phase === 'completed' ? 'is-completed-view' : '')}>
      <div className={'sw-reference-header ' + (preparing ? 'is-quiet' : '')}>
        <header className="sw-topbar"><div className="sw-task-heading"><Button className="sw-mobile-menu" icon="menu" aria-label="打开任务导航" onClick={() => setSidebar(true)} /><h2 title={detail?.task.title}><BlurText kind="status" text={detail?.task.title || '新的科研任务'} /></h2></div>
          {!empty && <Dropdown trigger={['click']} menu={{items:[
            {key:'claims',label:'主张与证据',disabled:preparing,onClick:()=>setTab('claims')},
            {key:'process',label:'研究过程与二维结构',disabled:preparing,onClick:()=>setTab('process')},
            {type:'divider'},
            {key:'draft',label:notebookDirty ? '处理草稿修改' : '编辑研究草稿',disabled:preparing,onClick:()=>setNotebookEditorOpen(true)},
            {key:'materials',label:'研究材料与资源',onClick:()=>setMaterialsOpen(true)},
            {key:'history',label:'历史轮次与检查点',onClick:()=>setHistoryOpen(true)},
            {key:'chat',label:'完整对话记录',onClick:()=>setChatOpen(true)},
            {key:'export',label:'导出研究档案',disabled:preparing || !!busy,onClick:()=>{void exportTask();}},
          ]}}><Button icon="more" aria-label="任务菜单" /></Dropdown>}
          {preparing && !empty && <Badge status="requirements" text="明确研究需求" />}
        </header>
        {!preparing && detail && <AgentStrip detail={detail} onNode={setSelectedNode} />}
        {!preparing && <div className="sw-research-control">{detail && canControlResearch(detail) ? <><Button variant="outline" icon={detail.state?.paused ? 'play' : 'pause'} aria-label={detail.state?.paused ? '继续研究' : '暂停研究'} busy={busy === 'pause' || busy === 'resume'} disabled={!!busy} onClick={() => {void action(detail.state?.paused ? 'resume' : 'pause');}} /><span>{detail.state?.paused ? '研究已暂停' : '暂停研究'}</span></> : <Badge status={detail?.phase || 'empty'} />}{detail?.error && <button className="sw-run-alert" onClick={() => modal.info({title:'当前运行提示',content:detail.error,okText:'知道了'})}>运行提示</button>}</div>}
        {!preparing && <div className="sw-tabbar"><nav aria-label="工作台模块">{primaryTabs.map(t => <button key={t.id} className={activeTab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>{tabNames[t.id]}</button>)}{['claims','process'].includes(activeTab) && <button className="active" onClick={() => setTab(activeTab)}>{activeTab === 'claims' ? '主张与证据' : '研究过程'}</button>}</nav><Button className="sw-mobile-notebook" icon="book" aria-label="展开或折叠研究草稿" onClick={() => setNotebookCollapsed(!notebookCollapsed)} /></div>}
      </div>
      {currentError && <ErrorNote onRetry={() => { setError(''); void workspace.refresh(); }}>{currentError}</ErrorNote>}
      {detail && <Checkpoint key={detail.task.id} detail={detail} accept={workspace.accept} onNotebook={() => { setNotebookCollapsed(false); setTab('board'); }} />}
      {workspace.loading ? <div className="sw-loading-screen"><span className="sw-spinner" /><span>读取研究工作区…</span></div> : empty ? <section className="sw-entry"><div className="sw-entry-mark"><Icon name="layers" /></div><span className="sw-eyebrow">从一个好问题开始</span><h1>下一项研究，<br /><span>我们一起完成。</span></h1><p>带上你的问题，把想法变成有依据的发现。</p><Composer centered value={composer} onChange={setComposer} onSend={() => { void send(); }} busy={busy === 'message' || busy === 'create'} /><div className="sw-entry-hints"><span><Icon name="spark" />探索一个方向</span><span><Icon name="lab" />复现一篇论文</span><span><Icon name="book" />形成研究成果</span></div>{!modelReady && <button className="sw-config-hint" onClick={() => setSettingsOpen(true)}>连接 DeepSeek 开始模型研究 <Icon name="arrow" /></button>}</section> : <div className={`sw-body ${preparing ? 'is-preparing' : ''}`}><div className="sw-center"><div className="sw-content" key={detail?.task.id}>
        {preparing && detail ? <Preparation composer={composerElement} detail={detail} editor={editor} busy={busy} modelReady={modelReady} onStart={() => { void start(); }} onMode={value => { void mode(value); }} /> : boardProps && <>{!detail?.workbench && <p className="sw-notice">本地服务尚未提供新版工作台数据，请重新启动服务以读取产物与笔记。</p>}{activeTab === 'board' ? <Board {...boardProps} /> : activeTab === 'claims' ? <ClaimsView {...boardProps} /> : activeTab === 'papers' ? <PapersView {...boardProps} /> : activeTab === 'report' || activeTab === 'paper' ? <ReportView {...boardProps} paper={activeTab === 'paper'} /> : activeTab === 'experiments' ? <ExperimentsView detail={boardProps.detail} onInspect={setSelectedArtifact} refresh={workspace.refresh} onMaterials={() => setMaterialsOpen(true)} /> : <ProcessView {...boardProps} />}</>}
      </div>{!preparing && <div className="sw-compose-dock">{composerElement}</div>}</div>
        {!preparing && detail && <Notebook key={detail.task.id} detail={detail} collapsed={notebookCollapsed} onCollapse={() => setNotebookCollapsed(!notebookCollapsed)} onProposal={setReview} onDirty={onNotebookDirty} editorOpen={notebookEditorOpen} onEditorOpen={setNotebookEditorOpen} />}
      </div>}
    </main>
    <ModelSettings open={settingsOpen} settings={settings} onClose={() => setSettingsOpen(false)} onSaved={value => { setSettings(value); void workspace.refresh(); }} />
    {detail && <div key={detail.task.id}><ArtifactInspector selected={selectedArtifact} detail={detail} onClose={() => setSelectedArtifact(null)} onPaper={setSelectedPaper} onProposal={setReview} refresh={workspace.refresh} /><ImpactReview review={review} detail={detail} onClose={() => setReview(null)} onUpdated={setReview} onApplied={workspace.refresh} /><MaterialsDrawer open={materialsOpen} detail={detail} onClose={() => setMaterialsOpen(false)} refresh={workspace.refresh} /><TaskNodeDrawer target={selectedNode} detail={detail} onClose={() => setSelectedNode(null)} onPaper={setSelectedPaper} accept={workspace.accept} /><TaskPaperDrawer paper={detail.state?.papers.find(p => p.id === selectedPaper)} taskId={detail.task.id} state={detail.state} onClose={() => setSelectedPaper(null)} onPaper={setSelectedPaper} />
      <Drawer open={historyOpen} title="历史轮次与检查点" width={520} onClose={() => setHistoryOpen(false)} rootClassName="sw-drawer"><h3>研究轮次</h3>{detail.runs.length ? [...detail.runs].reverse().map(run => <button key={`${run.round}-${run.at}`} className="sw-history-row" onClick={() => { void openRun(run.round); }}><strong>第 {run.round} 轮 · {shortTime(run.at)}</strong><p>{run.summary || '查看本轮结果'}</p><Icon name="arrow" /></button>) : <p className="sw-muted">本轮尚未归档。</p>}<h3>检查点</h3>{detail.state?.checkpoints.map(checkpoint => <div key={checkpoint.id} className="sw-history-row"><strong>{checkpoint.title}</strong><p>{checkpoint.status === 'confirmed' ? '已确认' : checkpoint.status === 'pending' ? '等待处理' : '已被后续版本替代'}</p><Button disabled={!!busy} onClick={() => rollback(checkpoint.id)}>回到此处</Button></div>)}</Drawer>
      <Modal open={!!historic} title={`第 ${historic?.round || ''} 轮研究结果`} width={850} onCancel={() => setHistoric(null)} footer={null} className="sw-modal">{historic?.error ? <ErrorNote>{historic.error}</ErrorNote> : historic?.data ? <HistoricalReport data={historic.data} /> : <Empty title="正在读取历史结果…" />}</Modal>
      <Modal open={chatOpen} title="与研究助手的对话" width={780} onCancel={() => setChatOpen(false)} footer={null} className="sw-modal"><div className="sw-conversation-history">{detail.messages.map(m => <article key={m.id}><span className="sw-eyebrow">{m.role === 'user' ? '你' : m.role === 'assistant' ? '研究助手' : '系统'} · {shortTime(m.at)}</span><Markdown text={m.content} /></article>)}</div></Modal>
    </div>}
  </div>;
}
