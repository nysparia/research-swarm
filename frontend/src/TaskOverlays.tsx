import { useEffect, useState } from 'react';
import { Alert, App as AntdApp, Button, Drawer, Dropdown, Spin, Tabs, Tag, type TabsProps } from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import { api, messageOf, taskPath } from './taskApi';
import type { TaskDetail } from './taskTypes';
import type { ActionKind, CanonicalClaim, Evidence, Paper, Snapshot } from './types';
import type { VisualNode } from './graphData';
import { Markdown } from './Markdown';
import { BlurText } from './BlurReveal';
import { failureReason } from './researchStatus';
import { TaskNodeActionModal } from './TaskNodeActionModal';
import { ResponsibilityConfirmation } from './ResponsibilityConfirmation';
import { claimConfirmationLabel, claimReviewLabel, type responsibilityPayload } from './reviewState';

const formatTime = (at?: string | null) => at ? new Date(at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
const statusText = { pending: '待执行', running: '运行中', completed: '已完成', failed: '失败', waiting_user: '待处理' };
const phaseText: Record<string, string> = { plan: '规划', execute: '执行', aggregate: '汇总' };
const safeUrl = (url: string) => /^https?:\/\//i.test(url) || /^\/api\//.test(url) ? url : undefined;

export function TaskEvidence({ evidence, state, onPaper }: { evidence: Evidence[]; state: Snapshot; onPaper: (id: string) => void }) {
  if (!evidence.length) return <p className="unsupported-note">无可定位证据。此项仍是待验证判断。</p>;
  return <div className="task-evidence-list">{evidence.map(item => {
    const paper = state.papers.find(paper => paper.id === item.paperId);
    const type = item.type === 'experiment' ? '实验产物' : /abstract|summary/i.test(item.type) ? '摘要证据' : /full/i.test(item.type) ? '全文证据' : item.type || '资料证据';
    return <article key={item.id}><div><Tag>{type}</Tag><span>证据 #{item.id}</span></div><blockquote>{item.quote || '未提供原文摘录'}</blockquote>{paper && <button className="inline-link" onClick={() => onPaper(paper.id)}>{paper.title}</button>}<p className="evidence-locator">{item.locator || '未定位'}{item.extractor ? ` · ${item.extractor}` : ''}</p></article>;
  })}</div>;
}

const polarityLabel: Record<string, string> = { for: '支持', against: '反对', mixed: '混合', unresolved: '方向未定' };
const assessmentLabel: Record<string, string> = { unassessed: '尚未评估', supported: '有证据支持', refuted: '受到反证', mixed: '证据混合', inconclusive: '尚无定论' };

export function CanonicalClaimDetail({ claim, state, onPaper }: { claim: CanonicalClaim; state: Snapshot; onPaper: (id: string) => void }) {
  const current = state.claimGraph?.relations.filter(relation => relation.claimId === claim.id && relation.claimVersion === claim.version) || [];
  const old = state.claimGraph?.relations.filter(relation => relation.claimId === claim.id && relation.claimVersion !== claim.version) || [];
  return <div className="drawer-section claim-detail"><h3>主张 v{claim.version}</h3><p className="claim-statement">{claim.statement}</p><div className="claim-fields"><div><strong>适用范围</strong><p>{claim.scope || '尚未限定'}</p></div><div><strong>可证伪条件</strong><p>{claim.falsification || '尚未说明'}</p></div></div><p><Tag color={claim.assessment?.confirmedByUser ? 'green' : 'gold'}>{claim.assessment?.confirmedByUser ? claimConfirmationLabel(claim) : claimReviewLabel(claim)}</Tag>{assessmentLabel[claim.assessment?.status] || '尚未评估'}</p>{claim.assessment?.confirmedByUser && <p className="quiet-text">{claimReviewLabel(claim)} · 用户确认不改变独立复核状态。</p>}{claim.assessment?.reason && <p>{claim.assessment.reason}</p>}{claim.assessment?.limitations && <p className="quiet-text">局限：{claim.assessment.limitations}</p>}
    <h3>当前版本的证据关系</h3>{current.length ? current.map(relation => { const evidence = state.evidence.find(item => item.id === relation.evidenceId); return <article className="claim-relation" key={relation.id}><Tag color={relation.polarity === 'for' ? 'green' : relation.polarity === 'against' ? 'red' : 'default'}>{relation.type === 'qualify' ? '限定适用' : polarityLabel[relation.polarity] || '方向未定'}</Tag><span>{relation.quality === 'unusable' ? '不可用' : relation.quality === 'limited' ? '有限证据' : '可用证据'}</span>{relation.reason && <p>{relation.reason}</p>}{relation.applicability && <p>适用性：{relation.applicability}</p>}{relation.rule && <p>命中量规：{relation.rule}</p>}{relation.quote && <blockquote>{relation.quote}</blockquote>}{relation.locator && <p className="evidence-locator">判定定位：{relation.locator}</p>}{relation.semanticGate && !relation.semanticGate.passed && <p className="unsupported-note">语义门未通过：{relation.semanticGate.issues?.join('；') || '仍需复核'}</p>}{evidence ? <TaskEvidence evidence={[evidence]} state={state} onPaper={onPaper} /> : <p className="unsupported-note">证据 #{relation.evidenceId} 暂不可读取</p>}{relation.sourceGroup && <small>来源组：{relation.sourceGroup}</small>}</article>; }) : <p className="unsupported-note">当前版本尚无关联证据。</p>}
    {!!claim.versions?.length && <details className="raw-details"><summary>查看 {claim.versions.length} 个版本与修改原因</summary>{claim.versions.map(version => <div className="claim-version" key={version.version}><strong>v{version.version} · {version.actor} · {formatTime(version.at || version.createdAt)}</strong><p>{version.statement}</p>{version.reason && <p>修改原因：{version.reason}</p>}</div>)}</details>}{!!old.length && <details className="raw-details"><summary>旧版本证据关系 {old.length} 条（不证明当前版本）</summary>{old.map(relation => <p key={relation.id}>v{relation.claimVersion} · {polarityLabel[relation.polarity] || '方向未定'} · 证据 #{relation.evidenceId} · {relation.reason || '未说明'}</p>)}</details>}{!!claim.decisions?.length && <details className="raw-details"><summary>用户判断记录 {claim.decisions.length} 条</summary>{claim.decisions.map((decision, index) => <p key={`${decision.version}-${index}`}>v{decision.version} · {decision.decision === 'confirm' ? '确认' : decision.decision} · {formatTime(decision.at)}{decision.reason ? ` · ${decision.reason}` : ''}{decision.responsibilityName ? ` · 责任签名：${decision.responsibilityName}` : ''}</p>)}</details>}
  </div>;
}

export function TaskNodeDrawer({ target, detail, onClose, onPaper, accept }: { target: VisualNode | null; detail: TaskDetail; onClose: () => void; onPaper: (id: string) => void; accept: (detail: TaskDetail) => void }) {
  const { message } = AntdApp.useApp();
  const state = detail.state;
  const node = state?.nodes.find(node => node.id === target?.nodeId);
  const claim = state?.claimGraph?.claims.find(claim => claim.id === target?.claimId);
  const selectedEvidence = state?.evidence.find(item => item.id === target?.evidenceId);
  const facet = state?.facetNodes.find(facet => facet.id === target?.facetId);
  const [tab, setTab] = useState('output');
  const [runs, setRuns] = useState<Record<string, unknown>[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionKind, setActionKind] = useState<ActionKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [claimConfirmation, setClaimConfirmation] = useState<{ claimId: string; version: number; revision: number } | null>(null);
  useEffect(() => { setTab('output'); setRuns([]); setHistoryError(''); setActionKind(null); setClaimConfirmation(null); }, [target?.id, detail.task.id]);
  useEffect(() => {
    if (!node || tab !== 'history') return;
    let active = true; setHistoryLoading(true);
    api<{ runs?: Record<string, unknown>[]; executions?: Record<string, unknown>[] }>(taskPath(detail.task.id, `/nodes/${encodeURIComponent(node.id)}/history`)).then(value => { if (active) setRuns(value.runs || value.executions || []); }).catch(error => { if (active) setHistoryError(messageOf(error)); }).finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [node?.id, node?.version, node?.finishedAt, tab, detail.task.id]);
  const retry = async () => { if (!node) return; setBusy(true); try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/retry'), { nodeId: node.id })); } catch (error) { void message.error(messageOf(error)); } finally { setBusy(false); } };
  const confirmClaim = async (signature: ReturnType<typeof responsibilityPayload>) => { if (!claimConfirmation) return; setBusy(true); try { accept(await api<TaskDetail>(taskPath(detail.task.id, '/actions/claim-decision'), { claimId: claimConfirmation.claimId, decision: 'confirm', expectedRevision: claimConfirmation.revision, note: '', ...signature })); setClaimConfirmation(null); void message.success('已记录对当前主张版本的责任签名'); } catch (error) { void message.error(messageOf(error)); } finally { setBusy(false); } };
  const ids = new Set([...(node?.evidenceIds || []), ...(node?.output?.evidenceIds || []), ...(node?.output?.claims?.flatMap(claim => claim.evidenceIds) || [])]);
  const tabs: TabsProps['items'] = node && state ? [
    {
      key: 'output',
      label: '输出与依据',
      children: <div className="drawer-section">{claim && <CanonicalClaimDetail claim={claim} state={state} onPaper={onPaper} />}{node.output ? <>
        <Markdown text={node.output.summary || '暂无摘要'} />
        {node.output.claims?.map(claim => <div className="node-claim-v2" key={claim.id}><p>{claim.text}</p><span className="report-assessment">{claimReviewLabel(state.claimGraph?.claims.find(item => item.id === claim.claimId && item.version === claim.claimVersion))}</span>{!claim.evidenceIds.length && <span className="unsupported-note">无证据</span>}{claim.limitations && <p className="quiet-text">{claim.limitations}</p>}</div>)}
        <h3>引用证据</h3><TaskEvidence evidence={state.evidence.filter(item => ids.has(item.id))} state={state} onPaper={onPaper} />
        {!!node.output.unresolved?.length && <><h3>尚未解决</h3><ul>{node.output.unresolved.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
        {node.output.structured && <details className="raw-details"><summary>结构化产物</summary><pre>{JSON.stringify(node.output.structured, null, 2)}</pre></details>}
      </> : <p className="quiet-text">节点尚未产生有效输出。可在动作记录查看执行情况。</p>}</div>,
    },
    {
      key: 'input',
      label: '任务输入',
      children: <div className="drawer-section">{Object.entries(node.input).map(([key, value]) => <div className="task-input" key={key}><span>{({ description: '任务描述', acceptance: '完成标准', constraints: '约束', experiment: '实验输入', paperIds: '关联论文' } as Record<string, string>)[key] || key}</span><pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre></div>)}</div>,
    },
    {
      key: 'logs',
      label: '动作记录',
      children: <><ol className="node-log-list">{node.logs.map(log => <li key={log.id}><time>{formatTime(log.at)}</time><p>{log.message}</p></li>)}</ol>{!node.logs.length && <p className="quiet-text">暂无动作记录。</p>}</>,
    },
    {
      key: 'history',
      label: '历史执行',
      children: <div className="drawer-section">{historyLoading ? <div className="loading-status" role="status"><Spin /><span>读取历史执行</span></div> : historyError ? <Alert type="error" description={historyError} showIcon /> : runs.length ? [...runs].reverse().map((run, index) => <details className="raw-details" key={String(run.id || index)}><summary>第 {String(run.round || '—')} 轮 · v{String(run.version || '—')} · {run.error ? '执行失败' : run.valid === false ? '历史失效执行' : run.type === 'tool-executed' ? '本机工具执行' : '执行记录'} · {formatTime(String(run.at || ''))}</summary>{Boolean(run.error) && <Alert type="error" description={String(run.error)} showIcon />}<pre>{JSON.stringify({ input: run.input, output: run.output, execution: run.execution, executions: run.executions, error: run.error, trace: run.trace }, null, 2)}</pre></details>) : <p className="quiet-text">暂无历史执行。</p>}</div>,
    },
  ] : [];

  return <>
    <Drawer open={Boolean(target)} title="研究节点" size={640} onClose={onClose} rootClassName="task-drawer sw-drawer">
      {target && state && <>
        {node?.status === 'failed' && <Alert type="error" showIcon title="节点执行失败" description={<BlurText text={failureReason(node)} />} />}
        {node?.kind === 'experiment' && ['missing_input', 'needs_execution'].includes(String(node.output?.structured?.status)) && <Alert type="warning" showIcon title="实验尚未执行" description="当前只有设计或缺失输入说明，没有本机实测结果。" />}
        <div className="node-detail-heading"><span><BlurText kind="status" text={claim ? `主张 v${claim.version} · ${node ? statusText[node.status] : '待执行'}` : node ? `${statusText[node.status]} · ${phaseText[node.phase] || node.phase} · v${node.version}` : facet?.facetName || (selectedEvidence ? '证据' : '研究切面')} /></span><h2><BlurText text={claim?.statement || node?.title || facet?.title || target.title} /></h2><p><BlurText text={node?.role || target.action} /></p></div>
        {node && <div className="node-detail-actions">
          <Button icon={<SearchOutlined />} type="primary" onClick={() => setActionKind('deepen')}>从这里深入研究</Button>
          {claim && !claim.archived && !claim.assessment?.confirmedByUser && claim.assessment?.status && claim.assessment.status !== 'unassessed' && <Button onClick={() => setClaimConfirmation({ claimId: claim.id, version: claim.version, revision: state.revision })}>签名确认当前判断</Button>}
          <Dropdown trigger={['click']} placement="bottomLeft" menu={{
            items: [
              { key: 'modify', label: '修改节点需求' },
              { key: 'insert', label: '插入子任务' },
              { key: 'reject', label: '否决节点结论', danger: true },
            ],
            onClick: ({ key }) => setActionKind(key as ActionKind),
          }}><Button type="text" aria-label="调整研究节点">调整节点</Button></Dropdown>
          {node.status === 'failed' && <Button loading={busy} onClick={() => { void retry(); }}>重试节点</Button>}
          <span>{node.elapsedMs > 1000 ? `${(node.elapsedMs / 1000).toFixed(1)} 秒` : `${node.elapsedMs || 0} 毫秒`}</span>
        </div>}
        {node ? <Tabs activeKey={tab} onChange={setTab} items={tabs} /> : claim ? <CanonicalClaimDetail claim={claim} state={state} onPaper={onPaper} /> : selectedEvidence ? <div className="drawer-section"><TaskEvidence evidence={[selectedEvidence]} state={state} onPaper={onPaper} /></div> : <div className="drawer-section"><h3>关联论文</h3>{state.papers.filter(paper => facet?.paperIds.includes(paper.id)).map(paper => <button className="paper-list-link" key={paper.id} onClick={() => onPaper(paper.id)}>{paper.title}<span>{paper.year || '年份未标注'}</span></button>)}</div>}
      </>}
    </Drawer>
    <TaskNodeActionModal initialKind={actionKind} node={node} claimId={claim?.id} detail={detail} onClose={() => setActionKind(null)} accept={accept} />
    <ResponsibilityConfirmation open={Boolean(claimConfirmation && claim)} title={`确认主张 v${claimConfirmation?.version || ''}`} busy={busy} onCancel={() => setClaimConfirmation(null)} onConfirm={signature => { void confirmClaim(signature); }} />
  </>;
}

export function TaskPaperDrawer({ paper, taskId, state, onClose, onPaper }: { paper?: Paper; taskId: string; state: Snapshot | null; onClose: () => void; onPaper: (id: string) => void }) {
  const tabs: TabsProps['items'] = paper && state ? [
    {
      key: 'evidence',
      label: '证据',
      children: <div className="drawer-section"><TaskEvidence evidence={state.evidence.filter(item => item.paperId === paper.id || paper.evidenceIds.includes(item.id))} state={state} onPaper={onPaper} /></div>,
    },
    {
      key: 'abstract',
      label: '摘要与材料',
      children: <div className="drawer-section"><Markdown text={paper.abstract || '来源未提供摘要。'} /><h3>复现材料</h3><p>{paper.reproducibility || '材料待核对；不代表已经完成复现实验。'}</p></div>,
    },
  ] : [];
  return <Drawer open={Boolean(paper)} title="论文与证据" size={650} onClose={onClose} rootClassName="task-drawer sw-drawer">{paper && state && <>
    <div className="node-detail-heading"><span>{paper.year || '年份未标注'} · {paper.venue || '来源未标注'} · 论文 #{paper.id}</span><h2>{paper.title}</h2><p>{Array.isArray(paper.authors) ? paper.authors.join('，') : paper.authors}</p></div>
    <div className="paper-source-actions">{paper.pdfAvailable && <Button icon={<DownloadOutlined />} onClick={() => window.open(`/api${taskPath(taskId, `/papers/${encodeURIComponent(paper.id)}/pdf`)}`, '_blank', 'noopener,noreferrer')}>查看全文</Button>}{paper.codeUrl && safeUrl(paper.codeUrl) && <a href={safeUrl(paper.codeUrl)} target="_blank" rel="noreferrer">代码线索 ↗</a>}{paper.doi && <a href={safeUrl(paper.doi) || `https://doi.org/${encodeURIComponent(paper.doi)}`} target="_blank" rel="noreferrer">来源 ↗</a>}</div>
    <Tabs items={tabs} />
  </>}</Drawer>;
}
