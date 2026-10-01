import { useEffect, useRef, useState } from 'react';
import { Modal, Popover } from 'antd';
import { api, messageOf, taskPath } from '../taskApi';
import { Markdown } from '../Markdown';
import type { TaskDetail } from '../taskTypes';
import type { Artifact, Interaction, InteractionKind, ProposalReview } from './types';
import { interactionPayload, proposalIsCurrent, interactionForArtifact } from './state';
import { Avatar, Button, ErrorNote, Icon } from './ui';

export function Discussion({ artifact, detail, onProposal, label = '询问 / 深入' }: { artifact: Artifact; detail: TaskDetail; onProposal: (review: ProposalReview) => void; label?: string }) {
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(artifact);
  const [kind, setKind] = useState<InteractionKind>('ask');
  const [text, setText] = useState('');
  const [replacement, setReplacement] = useState('');
  const [nodeId, setNodeId] = useState('');
  const [result, setResult] = useState<Interaction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true); const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    const handle = (event: KeyboardEvent) => { if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('keydown', handle); return () => document.removeEventListener('keydown', handle);
  }, [open]);
  useEffect(() => {
    if (!result || !['running', 'queued'].includes(result.status)) return;
    let live = true; let timer: number;
    const poll = async () => { try { const next = await api<Interaction>(taskPath(detail.task.id, `/interactions/${encodeURIComponent(result.id)}`)); if (live) { setResult(next); setError(''); } } catch (e) { if (live) setError(messageOf(e)); } finally { if (live) timer = window.setTimeout(poll, 1800); } };
    timer = window.setTimeout(poll, 1000); return () => { live = false; window.clearTimeout(timer); };
  }, [result?.id, result?.status, detail.task.id]);
  const send = async (requestedKind: InteractionKind = kind) => {
    setError(''); setBusy(true);
    try {
      const request = interactionPayload(shown, requestedKind, text, replacement, nodeId);
      const value = await api<Interaction>(taskPath(detail.task.id, '/interactions'), request);
      if (!mounted.current) return;
      setResult(value);
      if (value.proposal) { onProposal({ proposal: value.proposal, request }); setOpen(false); }
    } catch (e) { if (mounted.current) setError(messageOf(e)); }
    finally { if (mounted.current) setBusy(false); }
  };
  return <Popover open={open} trigger="click" placement="bottom" autoAdjustOverflow onOpenChange={next => { if (next && !open) { setShown(artifact); setResult(previous => interactionForArtifact(previous, artifact)); } setOpen(next); }} styles={{ container: { padding: 0, width: 'min(390px, calc(100vw - 32px))' } }} content={<section className="sw-discussion" role="dialog" aria-label="围绕当前内容讨论">
    <header><strong className="sw-discussion-pill">{shown.kind === 'claim' ? '围绕这条主张' : label === '关于这组对照' ? label : '关于这份产物'}</strong><Button icon="close" aria-label="关闭局部讨论" onClick={() => { setOpen(false); trigger.current?.focus(); }} /></header>
    <div className="sw-discussion-target"><Icon name="link" /><span>{shown.title}</span><small>v{shown.revision}</small></div>
    {(artifact.id !== shown.id || artifact.revision !== shown.revision) && <p className="sw-notice">内容已更新。此次讨论仍引用你打开时的版本。<button onClick={() => { setShown(artifact); setResult(previous => interactionForArtifact(previous, artifact)); }}>使用最新版本</button></p>}
    <details className="sw-discussion-advanced"><summary>更多研究操作</summary><div className="sw-segments" aria-label="讨论方式">{([['ask', '问一问'], ['deepen', '深入研究'], ['challenge', '提出质疑'], ['revise', '修改主张']] as const).map(([value, title]) => <button key={value} className={kind === value ? 'active' : ''} onClick={() => { setKind(value); setError(''); }} disabled={busy || !!result && ['running', 'queued'].includes(result.status) || (value === 'revise' && shown.kind !== 'claim')}>{title}</button>)}</div></details>
    {kind !== 'ask' && shown.nodeIds.length !== 1 && <label className="sw-field">由哪个节点继续研究<select value={nodeId} onChange={event => setNodeId(event.target.value)}><option value="">选择负责节点</option>{shown.nodeIds.map(id => <option key={id} value={id}>{detail.state?.nodes.find(n => n.id === id)?.title || id}</option>)}</select></label>}
    {kind === 'revise' && <label className="sw-field">修改后的完整主张<textarea value={replacement} onChange={event => setReplacement(event.target.value)} rows={3} /></label>}
    {result?.text && <div className="sw-local-user">{result.text}</div>}
    {result?.reply ? <div className="sw-local-assistant"><Avatar small /><div className="sw-discussion-reply"><span className="sw-eyebrow">{result.source === 'model' ? '研究助手' : '依据已有记录'} · v{result.target.revision}{result.stale || result.target.revision !== artifact.revision ? ' · 历史版本' : ''}</span><Markdown text={result.reply} /></div></div> : !result && <div className="sw-local-assistant"><Avatar small /><p>可以围绕这里的数据、依据或研究方向继续讨论。</p></div>}

    {result && ['running', 'queued'].includes(result.status) && <p className="sw-loading" role="status"><span className="sw-spinner" />{result.status === 'queued' ? '等待上一条回复…' : '正在读取这份内容及其依据…'}</p>}
    {(error || result?.error) && <ErrorNote>{error || result?.error}</ErrorNote>}
    <textarea className="sw-local-input" aria-label="局部讨论内容" placeholder={kind === 'ask' ? '这里的数据说明了什么？' : kind === 'deepen' ? '这个方向还需要补充什么证据或实验？' : '写下修改原因或需要核查的问题…'} value={text} onChange={event => setText(event.target.value)} rows={2} />
    <footer>{kind === 'ask' ? <><Button variant="outline" busy={busy || !!result && ['running', 'queued'].includes(result.status)} disabled={!text.trim()} onClick={() => {void send('ask');}}>追问</Button><Button variant="primary" busy={busy || !!result && ['running', 'queued'].includes(result.status)} disabled={!text.trim() || shown.status === 'stale' || !shown.nodeIds.length} title={!shown.nodeIds.length ? '该产物尚未关联研究节点，可先追问记录或在具体主张旁深入。' : undefined} onClick={() => {if (shown.nodeIds.length === 1) void send('deepen'); else setKind('deepen');}}>沿此深入</Button></> : <><Button onClick={() => setKind('ask')}>返回追问</Button><Button variant="primary" busy={busy || !!result && ['running', 'queued'].includes(result.status)} disabled={!text.trim()} onClick={() => {void send();}}>查看影响</Button></>}</footer>

  </section>}><button ref={trigger} className="sw-button sw-button-quiet sw-discuss-trigger"><Icon name="chat" />{label}</button></Popover>;
}

export function ImpactReview({ review, detail, onClose, onUpdated, onApplied }: { review: ProposalReview | null; detail: TaskDetail; onClose: () => void; onUpdated: (review: ProposalReview) => void; onApplied: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => setError(''), [review?.proposal.id]);
  if (!review) return null;
  const { proposal } = review; const current = proposalIsCurrent(proposal, detail);
  const origin = detail.state?.nodes.find(node => node.id === proposal.command?.nodeId);
  const deepening = proposal.command?.kind === 'deepen';
  const apply = async () => { setBusy(true); setError(''); try { await api(taskPath(detail.task.id, `/proposals/${encodeURIComponent(proposal.id)}/apply`), { expectedRevision: proposal.revision, confirmed: true }); await onApplied(); review.onApplied?.(); onClose(); } catch (e) { setError(messageOf(e)); } finally { setBusy(false); } };
  const refresh = async () => {
    if (!review.request) return;
    setBusy(true); setError('');
    try { const latest = await api<TaskDetail>(taskPath(detail.task.id)); const artifact = latest.workbench?.artifacts.find(a => a.id === review.request!.target.artifactId); if (!artifact) throw new Error('当前产物已不存在，请重新选择。');
      const request = { ...interactionPayload(artifact, review.request.kind, review.request.text, review.request.replacement, review.request.nodeId), showInConversation: review.request.showInConversation, scope: review.request.scope };
      const next = await api<Interaction>(taskPath(detail.task.id, '/interactions'), request); if (!next.proposal) throw new Error('尚未生成影响预览。'); onUpdated({ ...review, proposal: next.proposal, request }); await onApplied();
    } catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  };
  return <Modal open title="这次调整，会改变哪些研究？" width={680} onCancel={() => { if (!busy) onClose(); }} footer={null} destroyOnHidden className="sw-modal">
    <p className="sw-impact-lead"><strong>{proposal.affectedNodeIds.length}</strong> 个任务、<strong>{proposal.affectedArtifactIds.length}</strong> 份产物将受影响。</p>
    {deepening ? <p>以「{origin?.title || '当前节点'}」为研究起点，新增研究任务，并更新下列汇总结果。</p> : <p>确认后，受影响的旧结果会被标记，并按你的修改重新推进。</p>}
    {proposal.text && <blockquote className="sw-quote">{proposal.text}</blockquote>}{review.request?.kind === 'revise' && proposal.replacement && <p><strong>新主张：</strong>{proposal.replacement}</p>}
    {proposal.blocks && <details className="sw-details"><summary>查看修改后的笔记</summary><Markdown text={proposal.blocks.map(b => b.content).join('\n\n')} /></details>}
    <div className="sw-impact-list">{proposal.affectedNodeIds.map(id => <div key={id}><Icon name="layers" /><span>{detail.state?.nodes.find(n => n.id === id)?.title || id}</span></div>)}{!proposal.affectedNodeIds.length && <p>当前没有需要重跑的节点。</p>}</div>
    <details className="sw-details"><summary>受影响产物 · {proposal.affectedArtifactIds.length}</summary>{proposal.affectedArtifactIds.map(id => <p key={id}>{detail.workbench?.artifacts.find(a => a.id === id)?.title || id}</p>)}</details>
    {!current && <div className="sw-notice">研究版本已有变化，请重新核对影响范围。{review.request ? <Button icon="retry" busy={busy} onClick={() => { void refresh(); }}>重新计算影响</Button> : <span>关闭后回到笔记，对照最新版本重新提交；你的文字会保留。</span>}</div>}
    {error && <ErrorNote>{error}</ErrorNote>}
    {detail.state?.paused && <p className="sw-muted">当前处于全局暂停；应用修改后，点击顶部“继续”即可推进。</p>}
    <div className="sw-modal-footer"><Button disabled={busy} onClick={onClose}>返回修改</Button><Button variant="primary" busy={busy} disabled={!current} onClick={() => { void apply(); }}>确认应用修改</Button></div>
  </Modal>;
}
