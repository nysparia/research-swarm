import { GlassSymbol } from './GlassChrome';
import { useEffect, useReducer, useRef, useState } from 'react';
import { Drawer, Modal } from 'antd';
import { api, messageOf, taskPath } from '../taskApi';
import { Markdown } from '../Markdown';
import { CanonicalClaimDetail, TaskEvidence } from '../TaskOverlays';
import { ProviderSettingsDrawer } from '../ProviderSettingsDrawer';
import { hasSavedDeepSeekKey } from '../providerState';
import { deepseekSelection, emptyDeepSeekSelection, type DeepSeekModel } from '../deepseekState';
import type { TaskDetail } from '../taskTypes';
import type { Settings } from '../types';
import type { Artifact, ExperimentJob, ProposalReview } from './types';
import { plain, safeDownload } from './state';
import { Badge, Button, ErrorNote, Icon } from './ui';
import { Discussion } from './Discussion';
import { ExperimentDetail } from './Experiments';

export function ArtifactInspector({ selected, detail, onClose, onPaper, onProposal, onDiscuss, refresh }: { selected: Artifact | null; detail: TaskDetail; onClose: () => void; onPaper: (id: string) => void; onProposal: (review: ProposalReview) => void; onDiscuss?: (artifact: Artifact) => void; refresh: () => Promise<void> }) {
  const [tab, setTab] = useState('content'); const [versions, setVersions] = useState<Artifact[]>([]); const [historic, setHistoric] = useState<Artifact | null>(null); const [error, setError] = useState('');
  const latest = detail.workbench?.artifacts.find(a => a.id === selected?.id) || selected;
  const artifact = historic || latest;
  useEffect(() => { setTab('content'); setHistoric(null); setVersions([]); setError(''); }, [selected?.id]);
  useEffect(() => { if (!selected || tab !== 'versions') return; let live = true; void api<Artifact[]>(taskPath(detail.task.id, `/artifacts/${encodeURIComponent(selected.id)}/versions`)).then(result => { if (live) setVersions(result); }).catch(e => { if (live) setError(messageOf(e)); }); return () => { live = false; }; }, [selected?.id, latest?.revision, tab, detail.task.id]);
  const claim = detail.state?.claimGraph?.claims.find(c => artifact?.claimRefs.some(r => r.claimId === c.id && r.version === c.version));
  const evidence = detail.state?.evidence.filter(e => artifact?.evidenceIds.includes(e.id)) || [];
  return <Drawer open={!!selected} title="产物、证据与来源" width={700} onClose={onClose} rootClassName="sw-drawer">
    {artifact && <><div className="sw-inspector-heading"><Badge status={artifact.status} /><span>版本 {artifact.revision}</span><h2>{artifact.title}</h2>{artifact.staleReason && <p className="sw-notice">{artifact.staleReason}</p>}{historic && <div className="sw-notice">正在查看历史版本，不影响当前研究。<Button onClick={() => setHistoric(null)}>回到当前版本</Button></div>}</div>
      <div className="sw-segments sw-inspector-tabs">{[['content', '内容'], ['sources', '证据与溯源'], ['versions', '版本记录']].map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {tab === 'content' && (artifact.kind === 'experiment_job' ? <ExperimentDetail key={artifact.id} taskId={detail.task.id} job={artifact.content as unknown as ExperimentJob} refresh={refresh} historical={!!historic || artifact.status === 'stale'} /> : artifact.kind === 'claim' && claim && detail.state && !historic ? <CanonicalClaimDetail claim={claim} state={detail.state} onPaper={onPaper} /> : <><Markdown text={plain(artifact.content.markdown) || plain(artifact.content.summary) || plain(artifact.content.statement)} /><details className="sw-details"><summary>结构化原始内容</summary><pre>{JSON.stringify(artifact.content, null, 2)}</pre></details></>)}
      {tab === 'sources' && <>{detail.state ? <TaskEvidence evidence={evidence} state={detail.state} onPaper={onPaper} /> : <p className="sw-muted">尚无证据记录。</p>}<h3>登记的来源位置</h3>{artifact.sourceRefs.length ? artifact.sourceRefs.map((ref, i) => <div key={i} className="sw-source"><strong>{plain(ref.type) || plain(ref.kind) || '来源记录'}</strong>{plain(ref.locator) && <p>{plain(ref.locator)}</p>}{plain(ref.path) && <code>{plain(ref.path)}</code>}{safeDownload(ref.url) && <a href={safeDownload(ref.url)} target="_blank" rel="noreferrer">打开来源 <Icon name="arrow" /></a>}{plain(ref.sha256) && <small>SHA256 {plain(ref.sha256)}</small>}</div>) : <p className="sw-no-evidence">无证据位置，不能据此推断主张成立。</p>}<details className="sw-details"><summary>关联节点与依赖</summary><pre>{JSON.stringify({ nodeIds: artifact.nodeIds, claimRefs: artifact.claimRefs, dependencies: artifact.dependencies, sourceRevision: artifact.sourceRevision }, null, 2)}</pre></details></>}
      {tab === 'versions' && (versions.length ? versions.map(version => <button key={version.revision} className="sw-version-row" onClick={() => { setHistoric(version); setTab('content'); }}><span>v{version.revision} · {version.title}</span><Badge status={version.status} /></button>) : <p className="sw-muted">暂无可读取的版本记录。</p>)}
      {!historic && <div className="sw-modal-footer">{onDiscuss ? <Button icon="chat" onClick={() => onDiscuss(artifact)}>围绕这里继续讨论</Button> : <Discussion artifact={artifact} detail={detail} onProposal={onProposal} label="围绕这份产物继续讨论" />}</div>}
    </>}
  </Drawer>;
}
export function ModelSettings({ open, settings, onClose, onSaved }: { open: boolean; settings: Settings | null; onClose: () => void; onSaved: (settings: Settings) => void }) {
  const [selection, dispatch] = useReducer(deepseekSelection, emptyDeepSeekSelection);
  const requestSequence = useRef(0);
  const [busy, setBusy] = useState(''); const [error, setError] = useState(''); const [saved, setSaved] = useState(''); const [advanced, setAdvanced] = useState(false);
  const savedOfficialKey = hasSavedDeepSeekKey(settings);
  const canFetch = !!selection.key.trim() || savedOfficialKey;
  const canSave = canFetch && !selection.loading && selection.models.some(item => item.id === selection.model);
  useEffect(() => {
    dispatch({ type: 'reset', requestId: ++requestSequence.current }); setError(''); setSaved('');
    return () => { requestSequence.current += 1; };
  }, [open, advanced]);
  const loadModels = async () => {
    const requestId = ++requestSequence.current;
    dispatch({ type: 'loading', requestId }); setError(''); setSaved('');
    try {
      const result = await api<{ models: DeepSeekModel[] }>('/provider/models/deepseek', { ...(selection.key.trim() ? { apiKey: selection.key.trim() } : {}) });
      if (requestSequence.current === requestId) dispatch({ type: 'loaded', models: result.models, requestId });
    } catch (e) {
      if (requestSequence.current === requestId) dispatch({ type: 'failed', error: messageOf(e), requestId });
    }
  };
  const save = async (test: boolean) => { setBusy(test ? 'test' : 'save'); setError(''); setSaved(''); try {
    const result = await api<Settings>(test ? '/setup' : '/settings/deepseek', { model: selection.model, ...(selection.key.trim() ? { apiKey: selection.key.trim() } : {}) }, test ? 120000 : 30000);
    onSaved(result); dispatch({ type: 'reset', requestId: ++requestSequence.current });
    setSaved(test ? 'DeepSeek 官方连接测试成功。' : 'DeepSeek 官方配置已保存，尚未测试连通性。');
  } catch (e) { setError(messageOf(e)); } finally { setBusy(''); } };
  return <><Modal open={open && !advanced} title="模型设置" width={520} onCancel={() => { if (!busy) onClose(); }} footer={null} className="sw-modal sw-model-settings">
    <div className="sw-model-brand"><GlassSymbol name="settings" /><div><strong>DeepSeek 官方</strong><span>api.deepseek.com</span></div><Badge status={savedOfficialKey ? 'completed' : 'pending'} text={savedOfficialKey ? '已配置密钥' : '待配置'} /></div>
    <div className="sw-settings-group">
      <label className="sw-field sw-settings-row" htmlFor="deepseek-key"><span>API 密钥</span><input id="deepseek-key" type="password" autoComplete="new-password" value={selection.key} disabled={!!busy} onChange={e => { dispatch({ type: 'key', key: e.target.value, requestId: ++requestSequence.current }); setError(''); setSaved(''); }} placeholder={savedOfficialKey ? '留空使用已保存的密钥' : '粘贴官方 API 密钥'} /></label>
      <div className="sw-settings-row"><span>可用模型</span><Button variant="outline" busy={selection.loading} disabled={!!busy || !canFetch} onClick={() => { void loadModels(); }}>获取模型</Button></div>
      <label className="sw-field sw-settings-row" htmlFor="deepseek-model"><span>模型</span><select id="deepseek-model" value={selection.model} disabled={!!busy || selection.loading || !selection.models.length} onChange={e => dispatch({ type: 'select', model: e.target.value })}><option value="">{selection.loading ? '正在获取模型…' : '请选择模型'}</option>{selection.models.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>
    </div>
    <p className="sw-muted sw-settings-help">任务与记录保存在本机，推理请求发送至模型服务。已有裁判和红队配置保留，未单独配置的角色共享主模型。</p>
    {selection.fetched && !selection.models.length && <p className="sw-muted" role="status">当前密钥没有可用模型。</p>}
    {(error || selection.error) && <ErrorNote onRetry={selection.error && canFetch ? () => { void loadModels(); } : undefined}>{error || selection.error}</ErrorNote>}
    {saved && <p className="sw-success" role="status">{saved}</p>}
    <div className="sw-modal-footer"><Button disabled={!!busy} onClick={() => setAdvanced(true)}>高级设置</Button><Button variant="outline" busy={busy === 'save'} disabled={!!busy || !canSave} onClick={() => { void save(false); }}>保存</Button><Button variant="primary" busy={busy === 'test'} disabled={!!busy || !canSave} onClick={() => { void save(true); }}>保存并测试</Button></div>
  </Modal><ProviderSettingsDrawer open={open && advanced} settings={settings} onClose={() => { setAdvanced(false); onClose(); }} onSaved={onSaved} /></>;
}
