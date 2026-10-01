import { useEffect, useRef, useState } from 'react';
import { api, messageOf, taskPath } from '../taskApi';
import { Markdown } from '../Markdown';
import { BlurText } from '../BlurReveal';
import type { TaskDetail } from '../taskTypes';
import type { DraftBlock, Proposal, ProposalReview } from './types';
import { changedBlocks, rebaseNotebook, notebookSubmissionUnchanged } from './state';
import { Button, ErrorNote, Icon } from './ui';

export function Notebook({ detail, collapsed, onCollapse, onProposal, onDirty }: { detail: TaskDetail; collapsed: boolean; onCollapse: () => void; onProposal: (review: ProposalReview) => void; onDirty: (dirty: boolean) => void }) {
  const draft = detail.workbench?.draft;
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [added, setAdded] = useState<DraftBlock[]>([]);
  const [removed, setRemoved] = useState<string[]>([]);
  const [base, setBase] = useState(draft);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const local = useRef({ edits, added, removed }); local.current = { edits, added, removed };
  const dirty = Object.entries(edits).some(([id, content]) => base?.blocks.find(b => b.id === id)?.content !== content) || added.length > 0 || removed.length > 0;
  const conflict = dirty && base?.revision !== draft?.revision;
  useEffect(() => { if (!dirty) setBase(draft); }, [draft?.revision, dirty]);
  useEffect(() => { onDirty(dirty); const warn = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty, onDirty]);
  useEffect(() => { live.current = true; return () => { live.current = false; onDirty(false); }; }, [onDirty]);
  const reset = () => { setEdits({}); setAdded([]); setRemoved([]); setEditing(null); setError(''); };
  const preview = async () => {
    if (!base) return; setBusy(true); setError(''); const submitted = local.current;
    try {
      const operations = [...changedBlocks(base.blocks, edits).filter(op => !removed.includes(op.id)), ...removed.map(id => ({ op: 'remove', id })), ...added.map(block => ({ op: 'add', block }))];
      const result = await api<{ proposal: Proposal }>(taskPath(detail.task.id, '/draft/proposals'), { expectedRevision: base.revision, operations });
      if (live.current) onProposal({ proposal: result.proposal, onApplied: () => { if (notebookSubmissionUnchanged(submitted, local.current)) reset(); else setError('已应用预览中的修改；较新的编辑已保留，请对照新版本继续。'); } });
    } catch (e) { if (live.current) setError(messageOf(e)); }
    finally { if (live.current) setBusy(false); }
  };
  return <aside className={`sw-notebook ${collapsed ? 'is-collapsed' : ''}`} aria-label="研究笔记">
    <header><button className="sw-notebook-heading" onClick={onCollapse} aria-expanded={!collapsed}><Icon name="book" />{!collapsed && <strong>研究笔记</strong>}</button><Button icon={collapsed ? 'back' : 'chevron'} onClick={onCollapse} aria-label={collapsed ? '展开研究笔记' : '折叠研究笔记'} /></header>
    {!collapsed && <><div className="sw-notebook-meta"><span>你的研究边界</span><span><BlurText kind="status" text={dirty ? '有未保存修改' : `已同步 · v${draft?.revision ?? detail.document.revision}`} /></span></div>
      <fieldset disabled={busy} className="sw-notebook-scroll">{!draft && <p className="sw-notice">服务尚未提供新版笔记接口。原需求已保留。</p>}{!(base?.blocks.length || added.length) && <p className="sw-muted">笔记会随着需求整理逐步形成。</p>}
        {[...(base?.blocks || []), ...added].map((block, index) => <section key={block.id} className={`sw-note-block ${removed.includes(block.id) ? 'is-removed' : ''}`}>
          <div className="sw-note-title"><span className="sw-note-index">{String(index + 1).padStart(2, '0')}</span><h3>{block.title || '研究补充'}</h3>{block.locked && <span title="用户维护的内容不会被 AI 自动覆盖" className="sw-user-mark">你</span>}</div>
          {editing === block.id ? <textarea autoFocus aria-label={`编辑笔记：${block.title || '研究补充'}`} value={edits[block.id] ?? block.content} rows={Math.max(4, Math.min(14, (edits[block.id] ?? block.content).split('\n').length + 1))} onChange={event => added.some(b => b.id === block.id) ? setAdded(previous => previous.map(b => b.id === block.id ? { ...b, content: event.target.value } : b)) : setEdits(previous => ({ ...previous, [block.id]: event.target.value }))} /> : <button className="sw-note-content" disabled={removed.includes(block.id)} onClick={() => setEditing(block.id)} aria-label={`编辑笔记：${block.title || '研究补充'}`}><Markdown text={(edits[block.id] ?? block.content).replace(/^#{1,6}\s+[^\n]+\n?/, '') || '点击补充内容…'} /></button>}
          <div className="sw-note-actions">{removed.includes(block.id) ? <button onClick={() => setRemoved(previous => previous.filter(id => id !== block.id))}>撤销移除</button> : <><button onClick={() => setEditing(editing === block.id ? null : block.id)}>{editing === block.id ? '收起编辑' : '编辑'}</button><button onClick={() => added.some(b => b.id === block.id) ? setAdded(previous => previous.filter(b => b.id !== block.id)) : setRemoved(previous => [...previous, block.id])}>移除</button></>}</div>
        </section>)}
        {draft && <Button icon="plus" className="w-full" onClick={() => { const block = { id: `block:${crypto.randomUUID().replace(/-/g, '')}`, title: '研究补充', content: '## 研究补充\n\n', kind: 'section', source: 'user', locked: true }; setAdded(previous => [...previous, block]); setEditing(block.id); }}>补充一条笔记</Button>}
        {conflict && <div className="sw-notice"><strong>笔记已有新版本，编辑内容已保留。</strong><details><summary>对照服务端最新笔记</summary><Markdown text={draft?.blocks.map(b => b.content).join('\n\n') || ''} /></details><Button onClick={() => { const rebased = rebaseNotebook(base?.blocks || [], draft?.blocks || [], edits, removed, () => `block:${crypto.randomUUID().replace(/-/g, '')}`); setEdits(rebased.edits); setAdded(previous => [...previous, ...rebased.recovered]); setRemoved(rebased.removed); setBase(draft); setError(rebased.recovered.length ? '其他地方已删除的条目，已保留为“已恢复”笔记。请检查后提交。' : ''); }}>已对照，保留我的文字重新预览</Button></div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </fieldset>
      <footer>{dirty ? <><Button variant="outline" onClick={reset} disabled={busy}>撤销编辑</Button><Button variant="primary" busy={busy} disabled={conflict} onClick={() => { void preview(); }}>查看修改影响</Button></> : <p><Icon name="link" />修改会先预览影响，再由你确认。</p>}</footer>
    </>}
  </aside>;
}
