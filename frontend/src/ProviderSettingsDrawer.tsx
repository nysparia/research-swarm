import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Drawer, Input, Segmented, Select, Space, Tag } from 'antd';
import { api, messageOf } from './taskApi';
import { inferenceLocation, isLoopbackEndpoint, providerFor, providerRoleLabels, providerRoles } from './providerState';
import type { ProviderRole, Settings } from './types';

interface ProviderDraft { type: string; baseUrl: string; model: string; apiKey: string }
const draftsFrom = (settings: Settings | null): Record<ProviderRole, ProviderDraft> => {
  const draft = (role: ProviderRole): ProviderDraft => { const { type, baseUrl, model } = providerFor(settings, role); return { type, baseUrl, model, apiKey: '' }; };
  return { main: draft('main'), judge: draft('judge'), redteam: draft('redteam') };
};

export function ProviderSettingsDrawer({ open, settings, onClose, onSaved }: { open: boolean; settings: Settings | null; onClose: () => void; onSaved: (settings: Settings) => void }) {
  const { message } = AntdApp.useApp();
  const [role, setRole] = useState<ProviderRole>('main');
  const [drafts, setDrafts] = useState(() => draftsFrom(settings));
  const [mode, setMode] = useState<Settings['mode']>('evidence');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState('');
  const [cleared, setCleared] = useState<ProviderRole[]>([]);
  useEffect(() => { if (open) { setDrafts(draftsFrom(settings)); setMode(settings?.mode || 'evidence'); setError(''); setTestResult(''); setCleared([]); } }, [open, Boolean(settings)]);
  const draft = drafts[role];
  const saved = providerFor(settings, role);
  const change = (field: keyof ProviderDraft, value: string) => { setDrafts(previous => ({ ...previous, [role]: { ...previous[role], [field]: value } })); setCleared(previous => previous.filter(item => item !== role)); setTestResult(''); };
  const save = async (testConnection: boolean) => {
    setBusy(testConnection ? 'test' : 'save'); setError(''); setTestResult('');
    try {
      const providers = Object.fromEntries(providerRoles.filter(item => cleared.includes(item) || drafts[item].baseUrl.trim() || drafts[item].model.trim()).map(item => {
        if (cleared.includes(item)) return [item, null];
        const { type, baseUrl, model, apiKey } = drafts[item];
        const endpointChanged = baseUrl.trim().replace(/\/+$/, '') !== providerFor(settings, item).baseUrl.replace(/\/+$/, '');
        return [item, { type, baseUrl: baseUrl.trim(), model: model.trim(), ...(apiKey.trim() ? { apiKey: apiKey.trim(), ...(endpointChanged ? { apiKeyEnv: '' } : {}) } : endpointChanged ? { clearKey: true } : {}) }];
      }));
      const result = await api<Settings>('/settings', { mode, providers });
      onSaved(result); setDrafts(draftsFrom(result)); setCleared([]);
      if (testConnection) {
        const tested = await api<{ ok?: boolean; error?: string; message?: string }>('/provider/test', { role }, 120000);
        if (tested.ok === false) throw new Error(tested.error || tested.message || '连接测试失败');
        setTestResult(`${providerRoleLabels[role]}：${tested.message || '连接测试成功'}`);
      } else { void message.success('运行模式与模型角色已保存'); }
    } catch (error) { setError(messageOf(error)); }
    finally { setBusy(''); }
  };
  return <Drawer open={open} title="运行模式与模型角色" size={520} onClose={onClose} rootClassName="task-drawer" footer={<Space wrap><Button disabled={Boolean(busy)} onClick={onClose}>关闭</Button><Button loading={busy === 'save'} disabled={Boolean(busy)} onClick={() => { void save(false); }}>保存设置</Button><Button type="primary" loading={busy === 'test'} disabled={Boolean(busy) || !draft.baseUrl.trim() || !draft.model.trim()} onClick={() => { void save(true); }}>保存并测试{providerRoleLabels[role]}</Button></Space>}>
    <p className="settings-intro">{inferenceLocation(settings)}。任务编排与档案在本机；推理数据发送到所选模型端点，API Key 会用于该端点的鉴权。</p>
    <label className="simple-label">运行模式</label>
    <Segmented block value={mode} disabled={Boolean(busy)} options={[{ label: '本地资料核验', value: 'evidence' }, { label: '启用模型推理', value: 'llm' }]} onChange={value => setMode(value as Settings['mode'])} />
    <p className="quiet-text">资料核验模式无需密钥或网络：可记录需求、检查已有本地材料与凭据；不进行在线论文检索、模型推理或 AI 润色。切换模式影响后续操作，已开始的任务请先暂停。</p>
    <label className="simple-label">分别配置生产、判定与对抗复核</label>
    <Segmented block value={role} disabled={Boolean(busy)} options={providerRoles.map(value => ({ label: providerRoleLabels[value], value }))} onChange={value => { setRole(value as ProviderRole); setTestResult(''); setError(''); }} />
    <p><Tag color={saved.ready ? 'green' : 'default'}>{saved.ready ? '已存配置齐全（不代表连通）' : '已存配置尚未就绪'}</Tag>{role !== 'main' && <Tag color={saved.independentFromMain ? 'blue' : 'gold'}>{saved.independentFromMain ? '端点或模型标识与主模型不同' : '尚无不同的角色配置'}</Tag>}</p>
    {role !== 'main' && <Button size="small" danger disabled={Boolean(busy)} onClick={() => { setDrafts(previous => ({ ...previous, [role]: { type: 'openai', baseUrl: '', model: '', apiKey: '' } })); setCleared(previous => [...previous.filter(item => item !== role), role]); setTestResult(''); }}>停用{providerRoleLabels[role]}并清除配置（保存后生效）</Button>}
    <label className="simple-label" htmlFor="provider-preset">端点预设</label>
    <Select id="provider-preset" style={{ width: '100%' }} placeholder="选择预设或直接填写下方字段" value={undefined} disabled={Boolean(busy)} options={[{ label: 'DeepSeek · 远程 OpenAI 兼容接口', value: 'deepseek' }, { label: 'Ollama · 本机回环接口', value: 'ollama' }]} onChange={value => { setDrafts(previous => ({ ...previous, [role]: { type: 'openai', baseUrl: value === 'ollama' ? 'http://127.0.0.1:11434/v1' : 'https://api.deepseek.com', model: value === 'ollama' ? '' : 'deepseek-flash', apiKey: '' } })); setCleared(previous => previous.filter(item => item !== role)); setTestResult(''); }} />
    <label className="simple-label" htmlFor="provider-type">接口协议</label>
    <Select id="provider-type" style={{ width: '100%' }} value={draft.type || 'openai'} disabled={Boolean(busy)} options={[{ label: 'OpenAI 兼容', value: 'openai' }, { label: 'Anthropic', value: 'anthropic' }]} onChange={value => change('type', value)} />
    <label className="simple-label" htmlFor="provider-url">服务地址</label>
    <Input id="provider-url" value={draft.baseUrl} onChange={event => change('baseUrl', event.target.value)} disabled={Boolean(busy)} placeholder="https://… 或 http://127.0.0.1:…" />
    <label className="simple-label" htmlFor="provider-model">模型名称</label>
    <Input id="provider-model" value={draft.model} onChange={event => change('model', event.target.value)} disabled={Boolean(busy)} placeholder="填写该端点可用的模型名称" />
    <label className="simple-label" htmlFor="provider-key">API Key</label>
    <Input.Password id="provider-key" value={draft.apiKey} onChange={event => change('apiKey', event.target.value)} autoComplete="new-password" placeholder={saved.hasKey ? '留空保留该角色现有密钥' : '回环端点可不填；远程端点需要密钥'} disabled={Boolean(busy)} />
    <p className="quiet-text">更换服务地址会清除旧密钥，避免将其发送到新端点；如需鉴权，请重新输入。{isLoopbackEndpoint(draft.baseUrl) ? '连接回环地址；端点本身是否转发到云端，需由你核对其部署。' : '将向此远程端点发送任务相关文本和证据片段。'} 模型名称或地址不同只表示配置不同，不证明厂商、模型族或判断在认知上独立。未配置裁判时，主模型结果标为未独立复核；不会静默使用主模型冒充裁判。</p>
    {testResult && <Alert type="success" showIcon description={testResult} />}
    {error && <Alert type="error" showIcon description={error} />}
  </Drawer>;
}
