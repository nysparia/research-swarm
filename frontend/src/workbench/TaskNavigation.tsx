import type { TaskSummary } from '../taskTypes';
import { Button, Icon } from './ui';
import { SwarmMark } from './WorkspaceChrome';

export function TaskNavigation({ open, busy, searching, query, tasks, selectedId, loading, hasTask, modelReady, onClose, onSearch, onQuery, onChoose, onNew, onLibrary, onExperiments, onSettings }: {
  open: boolean; busy: boolean; searching: boolean; query: string; tasks: TaskSummary[];
  selectedId: string | null; loading: boolean; hasTask: boolean; modelReady: boolean;
  onClose: () => void; onSearch: () => void; onQuery: (value: string) => void;
  onChoose: (id: string) => void; onNew: () => void; onLibrary: () => void;
  onExperiments: () => void; onSettings: () => void;
}) {
  return <aside className="sw-sidebar sw-navigation" aria-hidden={!open} {...(!open ? { inert: '' } : {})}>
    <header className="sw-sidebar-top"><div className="sw-sidebar-brand"><SwarmMark /><strong>科研蜂群</strong></div><Button icon="panel" aria-label="收起侧边栏" title="收起侧边栏" onClick={onClose} /></header>
    <div className="sw-navigation-primary"><Button className="sw-navigation-row sw-new-task" icon="edit" disabled={busy} onClick={onNew}>新建研究</Button><Button className="sw-navigation-row sw-side-action" icon="search" aria-expanded={searching} onClick={onSearch}>搜索任务</Button></div>
    {searching && <label className="sw-search"><Icon name="search" /><input autoFocus aria-label="搜索科研任务" placeholder="搜索任务" value={query} onChange={event => onQuery(event.target.value)} /></label>}
    <div className="sw-side-tools"><Button className="sw-navigation-row" icon="book" disabled={!hasTask} onClick={onLibrary}>资料库</Button><Button className="sw-navigation-row" icon="lab" disabled={!hasTask} onClick={onExperiments}>实验环境</Button></div>
    <div className="sw-sidebar-section">最近研究</div>
    <nav aria-label="科研任务列表">{loading ? <p className="sw-loading"><span className="sw-spinner" /></p> : tasks.map(task => <button type="button" key={task.id} className={`sw-task ${task.id === selectedId ? 'active' : ''}`} aria-current={task.id === selectedId ? 'page' : undefined} disabled={busy} title={task.title} onClick={() => onChoose(task.id)}><span>{task.title || '新的科研任务'}</span>{['researching', 'retrieving'].includes(task.phase) && <i className="sw-task-live" title="研究中" />}</button>)}{!loading && !tasks.length && <p className="sw-sidebar-empty">{searching ? '没有匹配的任务' : '你的研究会保存在这里。'}</p>}</nav>
    <footer><Button className="sw-navigation-row sw-model-link" icon="settings" onClick={onSettings}><span>模型与设置</span><span className={`sw-connection-dot ${modelReady ? 'is-ready' : ''}`} title={modelReady ? '研究助手已配置' : '尚未配置模型'} /><Icon name="chevron" /></Button></footer>
  </aside>;
}
