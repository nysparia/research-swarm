import { useEffect, useMemo, useState } from 'react';
import { BlurText } from '../BlurReveal';
import type { BoardProps } from './Board';
import type { Artifact, ExperimentJob } from './types';
import { Badge, Button, Icon } from './ui';
import { Discussion } from './Discussion';
import { JobLogs } from './JobLogs';
import { plain, record, shortTime } from './state';
import { boardExperiments, chartSeries, protocolStrings, reconcileBoardFeed, relatedClaim } from './boardState';

const emptyArtifacts: Artifact[] = [];

export function BoardChart({ metrics }: { metrics: unknown }) {
  const series = useMemo(() => chartSeries(metrics), [metrics]);
  const [selected, setSelected] = useState('');
  const current = series.find(s => s.key === selected) || series[0];
  if (!current) return <p className="sw-muted">尚无可绘制的实测数值。</p>;
  const lo = Math.min(0, ...current.points.map(p => p.value));
  const hi = Math.max(0, ...current.points.map(p => p.value));
  const range = hi - lo || 1;
  return <div className="sw-metrics sw-board-chart">
    <label className="sw-metric-toolbar">实测指标<select aria-label="选择主图指标" value={current.key} onChange={e => setSelected(e.target.value)}>{series.map(s => <option key={s.key} value={s.key}>{s.key}</option>)}</select></label>
    <div className="sw-bar-chart" aria-label={`${current.key} 实测图表`}>{current.points.map((point, i) => <div className="sw-bar-row" key={`${i}:${point.label}`}><span title={point.label}>{point.label}</span><div className="sw-bar-track"><i style={{ left: `${(Math.min(0, point.value) - lo) / range * 100}%`, width: `${Math.abs(point.value) / range * 100}%` }} /></div><strong><BlurText kind="status" text={point.value.toLocaleString('zh-CN', { maximumSignificantDigits: 6 })} /></strong></div>)}</div>
    <p className="sw-muted">{current.points.length === 1 ? '当前指标只有一条实测记录，尚不能据此比较方案。' : '同一指标的原始记录；效果判断需结合实验条件和证据。'}</p>
  </div>;
}

function ExperimentPlan({ protocol }: { protocol: Record<string, unknown> }) {
  const baselines = protocolStrings(protocol.baselines);
  const metrics = protocolStrings(protocol.metrics);
  return <div className="sw-plan-diagram">
    <div className="sw-plan-flow">
      <div className="sw-plan-methods"><small>对照基线</small>{baselines.length ? baselines.map((baseline, i) => <div className="sw-plan-method" key={i} title={baseline}><BlurText text={baseline} /></div>) : <div className="sw-plan-method">基线待设计</div>}</div>
      <div className="sw-plan-dataset"><Icon name="layers" /><strong>数据与条件</strong><p title={plain(protocol.dataset)}>{plain(protocol.dataset) || '数据来源与划分待明确'}</p></div>
      <div className="sw-plan-metrics"><small>评估指标 · 待测</small>{metrics.length ? metrics.map((metric, i) => <div key={i}><span title={metric}>{metric}</span><strong aria-label="尚无实测值">—</strong></div>) : <div><span>待明确指标</span><strong>—</strong></div>}</div>
    </div>
    {(plain(protocol.method) || plain(protocol.acceptance)) && <details className="sw-plan-facts"><summary>方法与验收条件{typeof protocol.replicates === 'number' ? ` · ${protocol.replicates} 次重复` : ''}</summary>{plain(protocol.method) && <p><strong>方法：</strong>{plain(protocol.method)}</p>}{plain(protocol.acceptance) && <p><strong>验收：</strong>{plain(protocol.acceptance)}</p>}</details>}
  </div>;
}

const artifactLabel = (artifact: Artifact) => artifact.kind === 'claim' ? '研究主张' : artifact.kind === 'expression' ? '研究稿件' : artifact.kind === 'experiment_job' ? '实验记录' : Object.keys(record(record(artifact.content.structured).experimentProtocol)).length ? '实验方案' : '研究发现';
const artifactExcerpt = (artifact: Artifact) => plain(artifact.content.summary) || plain(artifact.content.statement) || plain(artifact.content.markdown).replace(/^#+\s/gm, '') || (artifact.kind === 'experiment_job' ? '执行状态、原始输出和数据已保存，可打开查看。' : '已保存结构化产物，可查看完整内容与来源。');

export function ResearchBoard(props: BoardProps) {
  const { detail, onInspect, onTab } = props;
  const artifacts = detail.workbench?.artifacts || emptyArtifacts;
  const nodes = detail.state?.nodes || [];
  const sources = boardExperiments(artifacts, nodes);
  const [selectedId, setSelectedId] = useState('');
  const fallback = sources.filter(s => s.measured).at(-1) || sources.at(-1);
  const focus = sources.find(s => s.id === selectedId) || fallback;
  useEffect(() => { if (focus && focus.id !== selectedId) setSelectedId(focus.id); }, [focus?.id, selectedId]);
  const claim = relatedClaim(focus, artifacts, nodes);
  const protocol = focus?.protocol || {};
  const designNode = nodes.find(node => focus?.plan?.nodeIds.includes(node.id));
  const purpose = plain(record(designNode?.input.dataDemand).purpose) || plain(protocol.acceptance);
  const job = focus?.result?.content as unknown as ExperimentJob | undefined;
  const execution = job ? nodes.find(n => focus?.result?.nodeIds.includes(n.id)) : [...nodes].reverse().find(n => n.active && n.input.researchStep === 'experiment_execution' && focus?.plan?.nodeIds.includes(n.parentId || ''));
  const decision = detail.state?.project.researchDecision;
  const [feed, setFeed] = useState(() => reconcileBoardFeed(undefined, detail.task.id, artifacts));
  useEffect(() => { setFeed(previous => reconcileBoardFeed(previous, detail.task.id, artifacts)); }, [artifacts, detail.task.id]);
  const inspect = (artifact: Artifact) => { setFeed(previous => ({ ...previous, freshIds: previous.freshIds.filter(id => id !== artifact.id) })); onInspect(artifact); };
  const measured = focus?.measured;
  const focusTitle = measured ? '实验结果与方法对照' : '实验设计与待测指标';
  const terminalStatus = job?.status || execution?.status || 'pending';
  const openLogs = () => {
    if (focus?.result) onInspect(focus.result);
    else if (execution) props.onNode({ id: execution.id, nodeId: execution.id, sourceKind: 'agent', active: execution.active, title: execution.title, status: execution.status, action: execution.role });
    else onTab('experiments');
  };
  return <div className="sw-board sw-research-board">
    {decision && <section className="sw-decision"><span>这一步，由你决定</span><h2>{decision.question}</h2><ol>{decision.options.map((option, i) => <li key={i}><strong>{i + 1}. {option.label}</strong><p>{option.effect}</p></li>)}</ol><p>在下方回复你的选择，研究会据此继续。</p></section>}
    <div className="sw-board-overview">
      <section className="sw-card sw-board-focus">
        <header><div className="sw-focus-heading"><Icon name="lab" /><h2><BlurText text={focusTitle} /></h2></div><Badge status={measured ? 'completed' : terminalStatus} text={measured ? '实测记录' : undefined} /></header>
        {sources.length > 1 && <select className="sw-board-selector" aria-label="选择看板实验" value={focus?.id || ''} onChange={event => setSelectedId(event.target.value)}>{sources.map(source => <option key={source.id} value={source.id}>{source.plan?.title || source.artifact.title}</option>)}</select>}
        {focus && <p className="sw-focus-caption" title={focus.plan?.title || focus.artifact.title}>{focus.plan?.title || focus.artifact.title}</p>}
        <div className="sw-focus-body"><div className="sw-focus-visual">{measured ? <BoardChart key={focus?.id} metrics={job?.result?.metrics} /> : <ExperimentPlan protocol={protocol} />}</div>
          <aside className="sw-focus-reading"><h3><Icon name="book" />{measured ? '如何解读' : '要验证什么'}</h3><p>{purpose || (claim ? plain(claim.content.statement) || claim.title : '实验方案形成后，会在这里明确比较对象、所需数据和验收条件。')}</p><p className="sw-muted">{measured ? '图表来自当前执行记录。进程完成不代表研究主张成立。' : '指标尚未实测；执行数据返回后，这里转为图表。'}</p>{focus && <Button icon="link" onClick={() => onInspect(focus.artifact)}>{measured ? '查看数据来源' : '查看完整方案'}</Button>}</aside>
        </div>
        <footer className="sw-focus-footer"><span>{measured ? '来源：实验执行记录' : focus ? '来源：已保存的实验方案' : '等待研究节点形成实验方案'}</span>{focus && <Discussion key={focus.id} artifact={focus.artifact} detail={detail} onProposal={props.onProposal} label="讨论这张卡片" />}</footer>
      </section>
      <section className="sw-card sw-board-related"><header><h2><Icon name="spark" />相关主张</h2><Badge status={claim?.status || 'pending'} text={!claim ? '待关联' : undefined} /></header><p className="sw-related-statement">{claim ? <BlurText text={plain(claim.content.statement) || claim.title} /> : '这份实验尚未关联具体主张。'}</p><p className="sw-related-context">{claim ? claim.evidenceIds.length ? `${claim.evidenceIds.length} 条关联证据，需结合适用条件审阅。` : '无证据 · 目前仍是假设。' : '形成可追溯的关联后，再展示对应判断。'}</p><footer>{claim && <Discussion artifact={claim} detail={detail} onProposal={props.onProposal} label="就此追问" />}<Button icon="arrow" onClick={() => claim ? onInspect(claim) : onTab('claims')}>{claim ? '查看证据' : '查看研究主张'}</Button></footer></section>
      <section className="sw-card sw-board-terminal"><header><h2><Icon name="lab" />实验执行</h2><Badge status={terminalStatus} /></header>{job ? <JobLogs taskId={detail.task.id} job={job} compact /> : <pre tabIndex={0} aria-label="当前实验节点日志">{execution?.logs.slice(-5).map(log => `[${shortTime(log.at)}] ${log.message}`).join('\n') || '[等待] 本方案尚无执行日志。'}</pre>}<button className="sw-inline-link" onClick={openLogs}>完整日志 <Icon name="arrow" /></button></section>
    </div>
    <section className="sw-board-results"><header><h2>研究成果</h2><span>{feed.entries.length} 份已展示{feed.freshIds.length ? ` · ${feed.freshIds.length} 份新增` : ''}</span><Button icon="arrow" onClick={() => onTab('process')}>全部记录</Button></header><div className="sw-results-grid">
      {feed.entries.map(artifact => <article className={`sw-card sw-result-card ${feed.freshIds.includes(artifact.id) ? 'is-new' : ''}`} key={artifact.id} data-artifact-id={artifact.id}><header><span>{feed.freshIds.includes(artifact.id) && <b>新增 · </b>}{artifactLabel(artifact)}</span><Badge status={artifact.status} /></header>{artifact.kind !== 'claim' && <h3 title={artifact.title}><BlurText text={artifact.title} /></h3>}<p className="sw-result-excerpt"><BlurText text={artifactExcerpt(artifact)} /></p>{artifact.status === 'stale' && <p className="sw-muted">历史产物，已不用于当前判断。</p>}<footer><Button icon="link" onClick={() => inspect(artifact)}>查看完整内容</Button><Discussion artifact={artifact} detail={detail} onProposal={props.onProposal} label="讨论" /></footer></article>)}
      <div className="sw-result-placeholder"><Icon name="plus" /><strong>新的对比与发现将在这里追加</strong><span>已有卡片保持位置</span></div>
    </div></section>
  </div>;
}
