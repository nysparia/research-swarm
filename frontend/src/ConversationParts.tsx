import { lazy, Suspense } from 'react';
import { Alert, Button, Input, Spin, Tabs, type TabsProps } from 'antd';
import { ArrowUpOutlined, DownloadOutlined, FileTextOutlined } from '@ant-design/icons';
import { Markdown } from './Markdown';
import { TaskEvidence } from './TaskOverlays';
import { BlurText } from './BlurReveal';
import { failureReason, researchAssessment } from './researchStatus';
import { claimReviewLabel } from './reviewState';
import { SearchHistory } from './SearchHistory';
import type { Message, TaskDetail } from './taskTypes';
import type { VisualNode } from './graphData';
import type { Snapshot } from './types';

export const ResearchGraph = lazy(() => import('./ResearchGraph'));
export const timeLabel = (at: string) => new Date(at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

export function Composer({ value, onChange, onSend, busy, centered, phase }: { value: string; onChange: (value: string) => void; onSend: () => void; busy: boolean; centered?: boolean; phase?: string }) {
  return <div className={`composer ${centered ? 'composer-centered' : ''}`}>
    <Input.TextArea
      value={value}
      onChange={event => onChange(event.target.value)}
      autoSize={{ minRows: centered ? 3 : 1, maxRows: 6 }}
      variant="borderless"
      placeholder={centered ? '描述你想研究的问题…' : phase === 'completed' ? '继续追问，准备下一轮研究…' : phase === 'researching' || phase === 'retrieving' ? '补充问题或调整研究方向…' : '补充研究需求，或直接编辑上方文档…'}
      aria-label="科研对话输入"
      onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
          event.preventDefault();
          if (value.trim() && !busy) onSend();
        }
      }}
    />
    <div className="composer-footer">
      <span>{phase === 'researching' || phase === 'retrieving' ? '发送新问题会暂停当前研究，回到需求准备' : 'Enter 发送 · Shift + Enter 换行'}</span>
      <Button type="primary" icon={<ArrowUpOutlined />} aria-label="发送科研问题" loading={busy} disabled={!value.trim() || busy} onClick={onSend} />
    </div>
  </div>;
}

export function AgentActivity({ detail }: { detail: TaskDetail }) {
  const state = detail.state;
  const central = state?.nodes.find(node => node.id === 'central') || state?.nodes.find(node => !node.parentId);
  const operation = state?.operations?.find(item => item.status === 'running');
  const latest = state?.activities.at(-1);
  const message = operation?.message || central?.logs.at(-1)?.message || latest?.message || (detail.phase === 'retrieving' ? '正在检索研究资料' : '等待研究调度');
  const output = central?.output?.summary;
  const failed = state?.nodes.filter(node => node.active && node.status === 'failed') || [];
  const basis = typeof central?.input.description === 'string' ? central.input.description : detail.document.markdown.split('\n').find(line => line.trim() && !line.startsWith('#'));
  return <div className="agent-activity"><div className="agent-identity"><span className={`agent-dot ${detail.phase === 'failed' ? 'failed' : ''}`} /><strong>总 agent</strong><span><BlurText kind="status" text={failed.length ? '有节点执行失败' : state?.paused ? '已暂停' : detail.phase === 'retrieving' ? '检索资料' : detail.phase === 'completed' ? '本轮已结束' : '正在工作'} /></span></div>{failed.map(node => <Alert key={node.id} type="error" showIcon title={node.title} description={<BlurText text={failureReason(node)} />} />)}<p className="agent-action"><BlurText text={message} /></p>{(basis || output) && <details className="agent-context"><summary>查看行动依据与当前输出</summary>{basis && <div><span>任务依据</span><p>{basis}</p></div>}{output && <div><span>当前输出</span><Markdown text={output} /></div>}</details>}</div>;
}

export function ConversationLog({ messages }: { messages: Message[] }) {
  return <div className="conversation-log">{messages.map(message => <article key={message.id} className={`conversation-entry ${message.role}`}><div><strong>{message.role === 'user' ? '你' : message.role === 'assistant' ? '研究助手' : '系统'}</strong><time>{timeLabel(message.at)}</time></div><Markdown text={message.content} /></article>)}</div>;
}

export function ResultView({ detail, tab, onTab, onPaper, onNode, onExport }: { detail: TaskDetail; tab: string; onTab: (tab: string) => void; onPaper: (id: string) => void; onNode: (node: VisualNode) => void; onExport: () => void }) {
  const state = detail.state;
  const assessment = researchAssessment(state);
  const proposals: unknown[] = Array.isArray(state?.report.structured?.proposals) ? state.report.structured.proposals : [];
  const expression = state?.claimGraph?.expressions.find(item => item.id === state.report.expressionId);
  const items: TabsProps['items'] = [
    {
      key: 'report',
      label: '研究结果',
      children: <article className="research-report">
        {assessment.incomplete && <Alert type="warning" showIcon title="本轮已结束，研究尚未完成验收" description={<BlurText text={`${assessment.description}。下方保留已取得的结果与具体缺口。`} />} />}
        <div className="result-summary"><Markdown text={state?.report.summary || '本轮已结束，尚无报告摘要。'} /></div>
        {expression && <p className="result-provenance">{expression.kind === 'reproduction_report' ? '复现预检与小实验报告' : '研究表达'} · {expression.status === 'confirmed' ? '已确认' : '草稿'} · 引用 {expression.claimRefs.length} 个主张版本</p>}
        {proposals.length > 0 && <section className="report-open-questions"><h2>待验证的新方案</h2>{proposals.map((proposal, index) => <div key={index}><Markdown text={typeof proposal === 'string' ? proposal : Object.entries(proposal as Record<string, unknown>).map(([key, value]) => `**${key}**：${typeof value === 'string' ? value : JSON.stringify(value)}`).join('\n\n')} /></div>)}</section>}
        {state?.report.claims.map((claim, index) => {
          const evidence = state.evidence.filter(item => claim.evidenceIds.includes(item.id));
          const canonical = state.claimGraph?.claims.find(item => item.id === claim.claimId);
          const stale = Boolean(canonical && claim.claimVersion !== canonical.version);
          return <section className="report-claim" key={claim.id}>
            <div className="claim-number">{String(index + 1).padStart(2, '0')}</div>
            <div><p>{claim.text}</p><span className="report-assessment">{claimReviewLabel(stale ? undefined : canonical)}</span>{canonical && <button className="inline-link report-claim-reference" onClick={() => onNode({ id: `claim:${canonical.id}`, title: canonical.statement, status: 'completed', action: canonical.assessment.reason || '', sourceKind: 'claim', claimId: canonical.id, nodeId: canonical.ownerNodeId || undefined, active: !canonical.archived })}>查看主张与证据关系（当前 v{canonical.version}）</button>}{stale && <span className="unsupported-note">此引用属于 v{claim.claimVersion}，不证明当前主张。</span>}{!canonical && claim.claimId && <span className="unsupported-note">主张引用 {claim.claimId} 暂不可读取。</span>}{claim.assessmentStatus && <span className="report-assessment">{({ supported: '有证据支持', refuted: '受到反证', mixed: '证据混合', inconclusive: '尚无定论', unassessed: '尚未评估' } as Record<string, string>)[claim.assessmentStatus]}</span>}{claim.limitations && <p className="quiet-text">局限：{claim.limitations}</p>}{evidence.length ? <details className="claim-evidence"><summary>{evidence.length} 条可追溯证据</summary><TaskEvidence evidence={evidence} state={state} onPaper={onPaper} /></details> : <span className="unsupported-note">无证据，仍需验证</span>}</div>
          </section>;
        })}
        {Boolean(state?.report.unresolved.length) && <section className="report-open-questions"><h2>未解决的问题</h2><ul>{state!.report.unresolved.map((question, index) => <li key={index}>{question}</li>)}</ul></section>}
        <p className="result-provenance">{state?.project.mode === 'llm' ? '模型生成的研究结果（推理位置取决于所配端点）' : '本地资料核验结果'} · 结论需结合证据与局限判断</p>
      </article>,
    },
    {
      key: 'artifacts',
      label: `产物${detail.artifacts.length ? ` ${detail.artifacts.length}` : ''}`,
      children: <div className="artifact-list">{detail.artifacts.length ? detail.artifacts.map((artifact, index) => {
        const href = /^\/api\/|^https?:\/\//.test(artifact.url) ? artifact.url : undefined;
        return <a className="artifact-item" key={`${artifact.name}-${index}`} href={href} target="_blank" rel="noreferrer"><FileTextOutlined /><div><strong>{artifact.name}</strong><span>{artifact.kind}</span></div><DownloadOutlined /></a>;
      }) : <div className="quiet-empty"><p>当前没有单独的产物文件。</p><Button onClick={onExport}>导出本轮研究档案</Button></div>}</div>,
    },
    {
      key: 'process',
      label: '研究过程',
      children: <>
        <SearchHistory operations={state?.operations} />
        <Suspense fallback={<div className="graph-loading" role="status"><Spin /><span>加载二维结构</span></div>}><ResearchGraph key={detail.task.id} state={state} onSelect={onNode} /></Suspense>
        {state && <div className="process-log"><h2>执行记录</h2>{state.activities.map(activity => <div className="process-line" key={activity.id}><time>{timeLabel(activity.at)}</time><span>{activity.actor === 'user' ? '用户' : activity.actor === 'AI' ? 'agent' : '系统'}</span><p>{activity.message}</p></div>)}</div>}
        <details className="complete-conversation"><summary>查看完整对话</summary><ConversationLog messages={detail.messages} /></details>
      </>,
    },
  ];
  return <div className="result-view">
    <div className="result-title"><div><span className="overline">第 {detail.task.round} 轮研究</span><h1>{detail.task.title}</h1></div><Button icon={<DownloadOutlined />} onClick={onExport}>导出</Button></div>
    <Tabs activeKey={tab} onChange={onTab} items={items} />
  </div>;
}

export function HistoricalReport({ data }: { data: Record<string, unknown> }) {
  const state = (data.state || data.snapshot || data) as Partial<Snapshot>;
  return <div className="historical-report"><Markdown text={state.report?.summary || String(data.summary || '历史记录未提供报告摘要。')} />{state.report?.claims?.map(claim => <div className="node-claim-v2" key={claim.id}><p>{claim.text}</p><span className="report-assessment">{claimReviewLabel(state.claimGraph?.claims.find(item => item.id === claim.claimId && item.version === claim.claimVersion))}</span><span className="quiet-text">{claim.evidenceIds.length ? `${claim.evidenceIds.length} 条证据引用` : '无证据'}</span></div>)}<details className="raw-details"><summary>查看此轮保存的完整记录</summary><pre>{JSON.stringify(data, null, 2)}</pre></details></div>;
}
