import type { TaskDetail } from '../taskTypes';
import type { Artifact, DraftBlock, ExperimentJob, Interaction, InteractionKind, InteractionRequest, Proposal, Workbench } from './types';

export const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const plain = (value: unknown): string => typeof value === 'string' ? value : '';
export function metricRows(value: unknown, prefix = ''): { name: string; value: number }[] {
  if (Array.isArray(value)) return value.flatMap((row, i) => {
    const data = record(row); const label = plain(data.method) || plain(data.name) || plain(data.label) || String(i + 1);
    return metricRows(Object.fromEntries(Object.entries(data).filter(([key]) => !['method', 'name', 'label'].includes(key))), label + ' · ');
  }).slice(0, 60);
  return Object.entries(record(value)).flatMap(([key, item]) => typeof item === 'number' && Number.isFinite(item)
    ? [{ name: prefix + key, value: item }]
    : item && typeof item === 'object' && !Array.isArray(item) ? metricRows(item, prefix + key + '.') : []).slice(0, 60);
}
export function moduleTabs(workbench?: Workbench, state?: TaskDetail['state']) {
  const caps = workbench?.plan?.capabilities || [];
  const artifacts = workbench?.artifacts || [];
  return [
    { id: 'board', label: '研究看板' },
    ...(state?.claimGraph?.claims.length || artifacts.some(a => a.kind === 'claim') ? [{ id: 'claims', label: '主张与证据' }] : []),
    ...(caps.includes('experimentation') || caps.includes('reproduction') || artifacts.some(a => a.kind === 'experiment_job') ? [{ id: 'experiments', label: '实验室' }] : []),
    ...(state?.papers.length || caps.includes('review') ? [{ id: 'papers', label: '文献库' }] : []),
    { id: 'report', label: '研究报告' },
    ...(caps.includes('paper_preparation') || artifacts.some(a => a.kind === 'expression' && a.content.kind === 'paper') ? [{ id: 'paper', label: '论文草稿' }] : []),
    { id: 'process', label: '研究过程' },
  ];
}
export function interactionPayload(artifact: Artifact, kind: InteractionKind, text: string, replacement = '', nodeId = '', quote = ''): InteractionRequest {
  if (!text.trim()) throw new Error('请写下你想了解或调整的内容。');
  if (kind !== 'ask' && artifact.status === 'stale') throw new Error('这份产物已失效，请选择当前版本。');
  if (kind !== 'ask' && !nodeId && artifact.nodeIds.length !== 1) throw new Error('请选择具体负责节点，再调整研究。');
  if (nodeId && !artifact.nodeIds.includes(nodeId)) throw new Error('节点不属于这份产物。');
  if (kind === 'revise' && !replacement.trim()) throw new Error('请填写修改后的完整主张。');
  if (quote && !JSON.stringify(artifact.content).includes(JSON.stringify(quote).slice(1, -1))) throw new Error('选中文字已改变，请重新选择。');
  return { kind, text: text.trim(), target: { artifactId: artifact.id, revision: artifact.revision, ...(quote ? { selection: { quote } } : {}) },
    ...(kind === 'revise' ? { replacement: replacement.trim() } : {}), ...(kind !== 'ask' ? { nodeId: nodeId || artifact.nodeIds[0] } : {}) };
}
export function canResumeJob(job: ExperimentJob) {
  return ['failed', 'cancelled', 'timed_out', 'interrupted'].includes(job.status) && Boolean(job.checkpoint?.path && /^[a-f0-9]{64}$/i.test(job.checkpoint.sha256) && job.request?.checkpoint?.resumeCode?.trim());
}
export function proposalIsCurrent(proposal: Proposal, detail: TaskDetail) {
  return proposal.status === 'pending' && proposal.documentRevision === detail.document.revision
    && proposal.revision === (detail.state?.revision ?? detail.document.revision)
    && (!proposal.target || detail.workbench?.artifacts.some(a => a.id === proposal.target?.artifactId && a.revision === proposal.target.revision));
}
export function changedBlocks(blocks: DraftBlock[], edits: Record<string, string>) {
  if (Object.keys(edits).some(id => !blocks.some(b => b.id === id))) throw new Error('笔记结构已改变，未保存文字已保留，请对照新版本处理。');
  return blocks.filter(b => edits[b.id] !== undefined && edits[b.id] !== b.content).map(b => ({ op: 'update', id: b.id, changes: { content: edits[b.id] } }));
}
export function interactionForArtifact(interaction: Interaction | null, artifact: Artifact) {
  return interaction?.target.artifactId === artifact.id && interaction.target.revision === artifact.revision ? interaction : null;
}
export function rebaseNotebook(base: DraftBlock[], latest: DraftBlock[], edits: Record<string, string>, removed: string[], makeId: (id: string) => string) {
  const ids = new Set(latest.map(block => block.id));
  const recovered = base.filter(block => !ids.has(block.id) && edits[block.id] !== undefined && !removed.includes(block.id)).map(block => ({ ...block, id: makeId(block.id), title: '已恢复 · ' + (block.title || '研究补充'), content: edits[block.id], source: 'user', locked: true }));
  return { recovered, edits: Object.fromEntries(Object.entries(edits).filter(([id]) => ids.has(id))), removed: removed.filter(id => ids.has(id)) };
}
export function notebookSubmissionUnchanged(submitted: { edits: Record<string, string>; added: DraftBlock[]; removed: string[] }, current: typeof submitted) {
  return JSON.stringify(submitted) === JSON.stringify(current);
}
export const statusLabels: Record<string, string> = {
  empty: '新任务', requirements: '准备研究', retrieving: '检索资料', researching: '研究中',
  pending: '待执行', queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', waiting_user: '等待你确认',
  cancelled: '已取消', timed_out: '超时', interrupted: '已中断', stale: '已失效',
  supported: '证据支持', refuted: '存在反证', mixed: '证据混合', inconclusive: '尚无定论', unassessed: '待验证', draft: '草稿', confirmed: '用户已确认',
};
export const shortTime = (at?: string) => at && Number.isFinite(Date.parse(at)) ? new Date(at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
export const duration = (ms?: number | null) => typeof ms === 'number' ? ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} 秒` : `${Math.floor(ms / 60000)} 分 ${Math.floor(ms / 1000) % 60} 秒` : '—';
export const safeDownload = (url?: unknown) => typeof url === 'string' && (/^\/api\//.test(url) || /^https?:\/\//i.test(url)) ? url : undefined;
