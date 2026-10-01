import { useEffect, useRef, useState } from 'react';
import { Drawer, Modal } from 'antd';
import { api, messageOf, taskPath } from '../taskApi';
import type { TaskDetail } from '../taskTypes';
import type { Artifact, ExperimentJob, Material, Workbench } from './types';
import { canResumeJob, duration, plain, shortTime } from './state';
import { Badge, Button, Empty, ErrorNote, Icon } from './ui';
import { MetricDisplay } from './Board';

export function JobLogs({ taskId, job }: { taskId: string; job: ExperimentJob }) {
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout'); const [text, setText] = useState(''); const [error, setError] = useState('');
  useEffect(() => {
    let live = true; let timer: number; setText(''); setError('');
    const poll = async () => { try {
      const path = taskPath(taskId, `/jobs/${encodeURIComponent(job.id)}/logs?stream=${stream}`);
      let result = await api<{ text: string; bytes: number }>(path);
      if (result.bytes > 65536) result = await api(path + `&offset=${result.bytes - 65536}`);
      if (live) { setText(result.text); setError(''); }
    } catch (e) { if (live) setError(messageOf(e)); } finally { if (live && ['running', 'queued', 'cancelling'].includes(job.status)) timer = window.setTimeout(poll, 1800); } };
    void poll(); return () => { live = false; window.clearTimeout(timer); };
  }, [taskId, job.id, job.status, job.attempts.length, stream]);
  return <div className="sw-job-logs"><header><div className="sw-segments"><button className={stream === 'stdout' ? 'active' : ''} onClick={() => setStream('stdout')}>标准输出</button><button className={stream === 'stderr' ? 'active' : ''} onClick={() => setStream('stderr')}>错误输出</button></div><span>最近 64 KB · 原始执行日志</span></header>{error && <ErrorNote>{error}</ErrorNote>}<pre tabIndex={0} aria-label="实验原始日志">{text || '该输出流目前没有内容。'}</pre></div>;
}
export function ExperimentDetail({ job, taskId, refresh, historical = false }: { job: ExperimentJob; taskId: string; refresh: () => Promise<void>; historical?: boolean }) {
  const [busy, setBusy] = useState(''); const [error, setError] = useState('');
  const action = async (action: string) => { setBusy(action); setError(''); try { await api(taskPath(taskId, `/jobs/${encodeURIComponent(job.id)}/actions`), { action, expectedRevision: job.revision }); await refresh(); } catch (e) { setError(messageOf(e)); } finally { setBusy(''); } };
  const running = ['running', 'queued', 'cancelling'].includes(job.status);
  const files = [...(job.result?.artifacts || []), ...job.attempts.flatMap(a => a.receipt?.path ? [{ ...a.receipt, name: '执行回执' }] : [])];
  return <div className="sw-experiment-detail"><div className="sw-flex-between"><Badge status={job.status} /><span className="sw-muted">{duration(job.result?.elapsedMs)} · {job.attempts.length} 次尝试</span></div>
    <p className="sw-notice">进程完成代表代码已运行。数据是否支持主张，需要结合实验设计与证据判断。</p>
    {!historical && <div className="sw-flex-wrap">{running ? <Button variant="outline" busy={busy === 'cancel'} disabled={!!busy || job.status === 'cancelling'} onClick={() => { void action('cancel'); }}>停止实验</Button> : <><Button variant="outline" icon="retry" busy={busy === 'retry'} disabled={!!busy} onClick={() => { void action('retry'); }}>重新执行</Button><Button variant="outline" busy={busy === 'resume'} disabled={!!busy || !canResumeJob(job)} title={!canResumeJob(job) ? '需要已停止的实验、真实检查点及续跑代码' : '从已保存的检查点继续'} onClick={() => { void action('resume'); }}>从检查点续跑</Button></>}</div>}
    {error && <ErrorNote>{error}</ErrorNote>}{historical ? <section className="sw-job-logs"><header>历史快照输出 · 不读取后续尝试</header><pre>{job.result?.stdout || '此版本未保存标准输出。'}</pre><header>历史错误输出</header><pre>{job.result?.stderr || '此版本未保存错误输出。'}</pre></section> : <JobLogs taskId={taskId} job={job} />}{job.result?.metrics !== undefined && <><h3>实测指标</h3><MetricDisplay metrics={job.result.metrics} /></>}
    <details className="sw-details"><summary>实验代码、输入与资源</summary><pre>{JSON.stringify(job.request, null, 2)}</pre></details><details className="sw-details"><summary>环境与执行历史</summary><pre>{JSON.stringify({ environment: job.result?.environment, attempts: job.attempts, checkpoint: job.checkpoint, resourceAvailability: job.resourceAvailability }, null, 2)}</pre></details>
    {!!files.length && <section><h3>已登记的产物与回执</h3>{files.map((file, i) => plain(file.path) ? <a className="sw-file-link" key={`${file.path}-${i}`} href={`/api${taskPath(taskId, `/jobs/${encodeURIComponent(job.id)}/files?path=${encodeURIComponent(plain(file.path))}`)}`} download><Icon name="download" /><span>{plain(file.name) || plain(file.path).split(/[\\/]/).at(-1)}</span><small>{plain(file.sha256).slice(0, 12)}</small></a> : null)}</section>}
  </div>;
}
export function ExperimentsView({ detail, onInspect, refresh, onMaterials }: { detail: TaskDetail; onInspect: (artifact: Artifact) => void; refresh: () => Promise<void>; onMaterials: () => void }) {
  const [create, setCreate] = useState(false); const [code, setCode] = useState(''); const [timeout, setTimeoutValue] = useState(90); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [materialIds, setMaterialIds] = useState<string[]>([]);
  const artifacts = detail.workbench?.artifacts.filter(a => a.kind === 'experiment_job') || [];
  const materials = detail.workbench?.artifacts.filter(a => a.kind === 'material') || [];
  const submit = async () => { setBusy(true); setError(''); try { await api(taskPath(detail.task.id, '/jobs'), { code, timeoutSeconds: timeout, materialIds, resources: detail.workbench?.executionSettings.resources || { cpuCores: 1, gpuCount: 0 } }); await refresh(); setCreate(false); setCode(''); } catch (e) { setError(messageOf(e)); } finally { setBusy(false); } };
  return <div className="sw-module"><div className="sw-module-heading"><div><span className="sw-eyebrow">EXPERIMENT LAB</span><h1>从设计，到真实的数据。</h1></div><div className="sw-flex-wrap"><Button variant="outline" icon="folder" onClick={onMaterials}>材料与资源</Button><Button variant="primary" icon="plus" onClick={() => setCreate(true)}>提交本机实验</Button></div></div>
    {artifacts.length ? <div className="sw-experiment-grid">{[...artifacts].reverse().map(a => { const job = a.content as unknown as ExperimentJob; return <article className="sw-card" key={a.id}><header><div><span className="sw-eyebrow">{shortTime(job.createdAt)} · 尝试 {job.attempts?.length || 0} 次</span><h2 title={a.title}>{a.title === `实验 ${job.id}` ? `实验 · ${job.id.slice(-8)}` : a.title}</h2></div><Badge status={a.status} /></header>{a.status === 'stale' && <p className="sw-notice">历史实验：所属研究已变化，不作为当前主张的证据。</p>}<div className="sw-experiment-facts"><span>耗时 {duration(job.result?.elapsedMs)}</span><span>产物 {job.result?.artifacts?.length || 0} 份</span><span>{job.request.resources?.cpuCores ?? '—'} CPU · {job.request.resources?.gpuCount ?? '—'} GPU</span></div><pre className="sw-experiment-preview">{job.result?.stderr || job.result?.stdout || '打开查看实时输出与代码。'}</pre><Button icon="arrow" onClick={() => onInspect(a)}>打开实验记录</Button></article>; })}</div> : <Empty icon="lab" title="等待第一项实验">实验设计下派执行后，会显示真实日志、数据和文件。也可以提交自己的 Python 实验。</Empty>}
    <Modal open={create} title="提交一个本机 Python 实验" width={760} footer={null} onCancel={() => { if (!busy) setCreate(false); }} className="sw-modal"><p>运行你确认的代码，保存标准输出、环境记录和实际生成的文件。</p><label className="sw-field">Python 代码<textarea className="sw-code-input" value={code} onChange={e => setCode(e.target.value)} rows={14} placeholder="输入准备执行的实验代码…" spellCheck={false} /></label><p className="sw-muted">将指标写入工作目录的 metrics.json，可在看板显示原始数值。</p><label className="sw-field">最长运行时间（秒）<input type="number" min={1} max={86400} value={timeout} onChange={e => setTimeoutValue(Number(e.target.value))} /></label>
      {!!materials.length && <fieldset className="sw-material-selection"><legend>使用的材料</legend>{materials.map(a => <label key={a.id}><input type="checkbox" checked={materialIds.includes(plain(a.content.id))} onChange={e => setMaterialIds(previous => e.target.checked ? [...previous, plain(a.content.id)] : previous.filter(id => id !== a.content.id))} />{a.title}</label>)}</fieldset>}{error && <ErrorNote>{error}</ErrorNote>}<div className="sw-modal-footer"><Button disabled={busy} onClick={() => setCreate(false)}>返回</Button><Button variant="primary" busy={busy} disabled={!code.trim() || !Number.isInteger(timeout) || timeout < 1 || timeout > 86400} onClick={() => { void submit(); }}>确认代码并执行</Button></div>
    </Modal>
  </div>;
}

export function MaterialsDrawer({ open, detail, onClose, refresh }: { open: boolean; detail: TaskDetail; onClose: () => void; refresh: () => Promise<void> }) {
  const [materials, setMaterials] = useState<Material[]>([]); const [settings, setSettings] = useState<Workbench['executionSettings']>();
  const [kind, setKind] = useState<'file' | 'text' | 'path' | 'repository'>('file'); const [name, setName] = useState('notes.txt'); const [text, setText] = useState(''); const [path, setPath] = useState(''); const [repo, setRepo] = useState(''); const [commit, setCommit] = useState('');
  const [file, setFile] = useState<File | null>(null); const [busy, setBusy] = useState(''); const [error, setError] = useState(''); const [saved, setSaved] = useState(''); const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (!open) return; let live = true; setError(''); setSaved(''); void Promise.all([api<Material[]>(taskPath(detail.task.id, '/materials')), api<Workbench['executionSettings']>(taskPath(detail.task.id, '/execution-settings'))]).then(([m, s]) => { if (live) { setMaterials(m); setSettings(s); } }).catch(e => { if (live) setError(messageOf(e)); }); return () => { live = false; }; }, [open, detail.task.id]);
  const add = async () => {
    setBusy('add'); setError(''); setSaved('');
    try {
      let payload: Record<string, unknown>;
      if (kind === 'file') { if (!file) throw new Error('先选择一个文件。'); if (file.size > 1400 * 1024) throw new Error('此上传入口支持 1.4 MB 以内的文件，较大文件请使用本机路径导入。'); const buffer = new Uint8Array(await file.arrayBuffer()); let binary = ''; for (let offset = 0; offset < buffer.length; offset += 8192) binary += String.fromCharCode(...buffer.subarray(offset, offset + 8192)); payload = { name: file.name, base64: btoa(binary) }; }
      else if (kind === 'text') payload = { name, text };
      else if (kind === 'path') payload = { name, sourcePath: path };
      else payload = { name: name.endsWith('.tar') ? name : 'repository.tar', repository: { url: repo, commit } };
      const value = await api<Material>(taskPath(detail.task.id, '/materials'), payload, 90000);
      if (mounted.current) { setMaterials(previous => [...previous, value]); setSaved('材料已导入。勾选并保存后会用于后续实验。'); setFile(null); } await refresh();
    } catch (e) { if (mounted.current) setError(messageOf(e)); } finally { if (mounted.current) setBusy(''); }
  };
  const save = async () => { if (!settings) return; setBusy('save'); setError(''); setSaved(''); try { const next = await api<Workbench['executionSettings']>(taskPath(detail.task.id, '/execution-settings'), { expectedRevision: settings.revision, maxTimeoutSeconds: settings.maxTimeoutSeconds, materialIds: settings.materialIds, resources: settings.resources || { cpuCores: 1, gpuCount: 0 } }); if (mounted.current) { setSettings(next); setSaved('执行材料和资源设置已保存。'); } await refresh(); } catch (e) { if (mounted.current) setError(messageOf(e)); } finally { if (mounted.current) setBusy(''); } };
  return <Drawer open={open} title="研究材料与本机资源" width={600} onClose={onClose} rootClassName="sw-drawer"><div className="sw-segments">{(['file', 'text', 'path', 'repository'] as const).map(value => <button className={kind === value ? 'active' : ''} key={value} onClick={() => setKind(value)}>{{ file: '上传文件', text: '文本', path: '本机路径', repository: '代码仓库' }[value]}</button>)}</div>
    {kind === 'file' ? <label className="sw-upload"><Icon name="folder" /><span>{file?.name || '选择研究材料 · 最大 1.4 MB'}</span><input type="file" aria-label="选择研究材料" onChange={e => setFile(e.target.files?.[0] || null)} /></label> : <><label className="sw-field">保存的文件名<input value={name} onChange={e => setName(e.target.value)} /></label>{kind === 'text' ? <label className="sw-field">材料内容<textarea value={text} onChange={e => setText(e.target.value)} rows={6} /></label> : kind === 'path' ? <label className="sw-field">本机文件的完整路径<input value={path} onChange={e => setPath(e.target.value)} placeholder="文件绝对路径，最大 50 MB" /></label> : <><label className="sw-field">HTTPS 仓库地址<input value={repo} onChange={e => setRepo(e.target.value)} placeholder="https://github.com/组织/仓库" /></label><label className="sw-field">固定版本（完整 40 位 commit）<input value={commit} onChange={e => setCommit(e.target.value)} /></label></>}</>}
    <Button variant="outline" busy={busy === 'add'} disabled={!!busy} onClick={() => { void add(); }}>导入材料</Button>
    <h3 className="mt-7">后续实验使用的材料</h3>{materials.length ? materials.map(material => <label className="sw-material-item" key={material.id}><input type="checkbox" checked={settings?.materialIds.includes(material.id) || false} onChange={e => setSettings(previous => previous ? { ...previous, materialIds: e.target.checked ? [...previous.materialIds, material.id] : previous.materialIds.filter(id => id !== material.id) } : previous)} /><div><strong>{material.name}</strong><small>{material.bytes?.toLocaleString()} bytes · SHA256 {material.sha256?.slice(0, 16)}</small></div></label>) : <p className="sw-muted">尚未导入材料。</p>}
    {settings && <><h3 className="mt-7">执行资源</h3><div className="sw-resource-fields"><label className="sw-field">CPU 核心<input type="number" min={1} value={settings.resources?.cpuCores ?? 1} onChange={e => setSettings({ ...settings, resources: { cpuCores: Number(e.target.value), gpuCount: settings.resources?.gpuCount ?? 0 } })} /></label><label className="sw-field">GPU 数量<input type="number" min={0} value={settings.resources?.gpuCount ?? 0} onChange={e => setSettings({ ...settings, resources: { cpuCores: settings.resources?.cpuCores ?? 1, gpuCount: Number(e.target.value) } })} /></label><label className="sw-field">最长运行秒数<input type="number" min={1} max={86400} value={settings.maxTimeoutSeconds} onChange={e => setSettings({ ...settings, maxTimeoutSeconds: Number(e.target.value) })} /></label></div><p className="sw-muted">运行中的研究请先暂停。资源值用于调度与线程提示，不是操作系统硬配额。</p></>}{error && <ErrorNote>{error}</ErrorNote>}{saved && <p className="sw-success" role="status">{saved}</p>}<div className="sw-modal-footer"><Button variant="primary" busy={busy === 'save'} disabled={!!busy || !settings} onClick={() => { void save(); }}>保存执行设置</Button></div>
  </Drawer>;
}
