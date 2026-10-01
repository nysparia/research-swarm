import { useEffect, useRef, useState } from 'react';
import { App, Modal } from 'antd';
import { api, messageOf, taskPath } from '../taskApi';
import { Markdown } from '../Markdown';
import { ResponsibilityConfirmation } from '../ResponsibilityConfirmation';
import type { responsibilityPayload } from '../reviewState';
import type { TaskDetail } from '../taskTypes';
import { Button, ErrorNote, Icon } from './ui';

export function Checkpoint({ detail, accept, onNotebook }: { detail: TaskDetail; accept: (detail: TaskDetail) => void; onNotebook: () => void }) {
  const { notification, modal } = App.useApp();
  const checkpoint = detail.state?.checkpoints.find(c => c.id === detail.state?.activeCheckpointId && c.status === 'pending');
  const [open, setOpen] = useState(false); const [signing, setSigning] = useState<number | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [note, setNote] = useState(''); const seen = useRef(new Set<string>());
  useEffect(() => { if (checkpoint && !seen.current.has(checkpoint.id)) { seen.current.add(checkpoint.id); setOpen(true); notification.info({ key: checkpoint.id, message: '研究需要你的判断', description: checkpoint.title, duration: 6 }); } }, [checkpoint?.id, notification]);
  if (!checkpoint || !detail.state) return null;
  const act = async (decision: 'confirm' | 'modify' | 'rollback', signature?: ReturnType<typeof responsibilityPayload>) => {
    setBusy(true); setError(''); try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/checkpoint'), { id: checkpoint.id, decision, note, ...(signature ? { expectedRevision: signing, ...signature } : {}) })); setSigning(null); setOpen(false); if (decision === 'modify') onNotebook(); } catch (e) { setSigning(null); setError(messageOf(e)); } finally { setBusy(false); }
  };
  return <><div className="sw-checkpoint"><span><Icon name="chat" /><strong>等待你判断</strong> · {checkpoint.title}</span><Button onClick={() => setOpen(true)}>查看并处理 <Icon name="arrow" /></Button></div>
    <Modal open={open} title={checkpoint.title} width={720} footer={null} onCancel={() => { if (!busy) setOpen(false); }} className="sw-modal"><Markdown text={checkpoint.summary} /><p className="sw-muted">AI 提供候选判断与证据；这一步由你决定如何继续。</p><label className="sw-field">你的修改意见（可选）<textarea value={note} onChange={e => setNote(e.target.value)} rows={3} placeholder="保留哪些条件，或希望补充什么？" /></label>{error && <ErrorNote>{error}</ErrorNote>}<div className="sw-modal-footer"><Button disabled={busy} onClick={() => modal.confirm({ title: '回到上一个检查点？', content: '后续分支会按历史状态回滚。现有记录保留在历史中。', okText: '确认回退', cancelText: '保留当前研究', onOk: () => act('rollback') })}>回退</Button><Button variant="outline" disabled={busy} onClick={() => { void act('modify'); }}>修改</Button><Button variant="primary" disabled={busy} onClick={() => setSigning(detail.state!.revision)}>确认</Button></div></Modal>
    <ResponsibilityConfirmation open={signing !== null} title={checkpoint.title} busy={busy} onCancel={() => setSigning(null)} onConfirm={signature => { void act('confirm', signature); }} />
  </>;
}
