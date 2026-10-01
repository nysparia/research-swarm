import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button } from 'antd';
import { api, messageOf, taskPath } from './taskApi';
import type { TaskDetail } from './taskTypes';
import { Markdown } from './Markdown';
import { ResponsibilityConfirmation } from './ResponsibilityConfirmation';
import type { responsibilityPayload } from './reviewState';

export function CheckpointPanel({ detail, accept }: { detail: TaskDetail; accept: (detail: TaskDetail) => void }) {
  const { message } = AntdApp.useApp();
  const [reviewedRevision, setReviewedRevision] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const checkpoint = detail.state?.checkpoints.find(item => item.id === detail.state?.activeCheckpointId && item.status === 'pending');
  useEffect(() => setReviewedRevision(null), [checkpoint?.id, detail.task.id]);
  if (!checkpoint || !detail.state) return null;
  const confirm = async (signature: ReturnType<typeof responsibilityPayload>) => {
    setBusy(true);
    try {
      accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/checkpoint'), { id: checkpoint.id, decision: 'confirm', expectedRevision: reviewedRevision, ...signature }));
      setReviewedRevision(null);
    } catch (error) { void message.error(messageOf(error)); }
    finally { setBusy(false); }
  };
  return <section className="checkpoint-panel">
    <Alert type="info" showIcon title={checkpoint.title} description={<><Markdown text={checkpoint.summary} /><p>确认会记录当前检查点的责任签名，不证明已阅读全文或结论已获科学验证。</p><Button type="primary" onClick={() => setReviewedRevision(detail.state!.revision)}>查看责任说明并确认</Button></>} />
    <ResponsibilityConfirmation open={reviewedRevision !== null} title={checkpoint.title} busy={busy} onCancel={() => setReviewedRevision(null)} onConfirm={signature => { void confirm(signature); }} />
  </section>;
}
