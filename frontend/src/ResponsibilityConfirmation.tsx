import { useEffect, useState } from 'react';
import { Alert, Checkbox, Input, Modal } from 'antd';
import { responsibilityPayload, responsibilityStatement } from './reviewState';

export function ResponsibilityConfirmation({ open, title, busy, onCancel, onConfirm }: { open: boolean; title: string; busy: boolean; onCancel: () => void; onConfirm: (signature: ReturnType<typeof responsibilityPayload>) => void }) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [name, setName] = useState('');
  useEffect(() => { if (open) { setAcknowledged(false); setName(''); } }, [open]);
  return <Modal open={open} title={title} onCancel={onCancel} closable={!busy} mask={{ closable: !busy }} confirmLoading={busy} okText="签名并确认" cancelText="取消" cancelButtonProps={{ disabled: busy }} okButtonProps={{ disabled: !acknowledged || !name.trim() }} onOk={() => onConfirm(responsibilityPayload(acknowledged, name))}>
    <Alert type="info" showIcon description="这是责任签名，不是科学校验。请结合证据位置、适用范围和未决问题自行判断；系统不会把点击、展开或签名记录成阅读证明。" />
    <p><Checkbox checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)}>{responsibilityStatement}</Checkbox></p>
    <label className="simple-label" htmlFor="responsibility-name">责任签名（姓名或自选标识）</label>
    <Input id="responsibility-name" value={name} onChange={event => setName(event.target.value)} maxLength={120} disabled={busy} autoComplete="off" />
  </Modal>;
}
