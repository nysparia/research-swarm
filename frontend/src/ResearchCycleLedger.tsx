import { Alert, Button, Empty, Tag } from 'antd';
import { BlurText } from './BlurReveal';
import type { ResearchCycleState } from './researchCycleTypes';

export const researchStepLabels: Record<string, string> = {
  background: '扩充背景', literature: '研究文献现状', topic: '凝练课题', hypothesis_generation: '提出猜想',
  hypothesis: '猜想重新论证', data_request: '明确数据需求', data_source: '查找数据来源',
  experiment_design: '设计 / 修订实验', experiment_execution: '执行实验', synthesis: '汇总与收敛判断',
};
const statusLabels: Record<string, string> = { running: '研究中', converged: '范围内已收敛 · 待审阅',
  inconclusive: '证据仍不足', budget_exhausted: '本轮资源用完 · 未收敛', awaiting_evidence: '等待数据',
  supported: '数据支持 · 有局限', refuted: '数据证伪', satisfied: '数据已返回', pending: '待执行',
  needs_experiment: '需要实验', blocked: '存在未解决问题', completed: '数据待设计节点复核', problem: '实验问题已上报' };
const reviewLabels: Record<string, string> = { accepted: '设计节点已验收', rejected: '复核未通过', superseded: '方案已被修订替代' };

export function ResearchCycleLedger({ cycle, mode = 'hypotheses', onNode }: { cycle: ResearchCycleState;
  mode?: 'hypotheses' | 'data' | 'experiments'; onNode: (id: string) => void }) {
  return <div className="cycle-ledger"><div className="cycle-ledger-header"><Tag color={cycle.status === 'converged' ? 'green' : cycle.status === 'running' ? 'processing' : 'orange'}><BlurText kind="status" text={statusLabels[cycle.status]} /></Tag><span><BlurText kind="status" text={`${researchStepLabels[cycle.stage] || cycle.stage} · 第 ${cycle.iteration} 次论证`} /></span></div>
    {mode === 'hypotheses' && <>{cycle.topic && <p className="cycle-topic">{cycle.topic.title}</p>}{cycle.hypotheses.map(hypothesis => <article key={hypothesis.id}><div><strong><BlurText text={hypothesis.statement} /></strong><Tag color={hypothesis.status === 'supported' ? 'blue' : hypothesis.status === 'refuted' ? 'purple' : 'default'}><BlurText kind="status" text={statusLabels[hypothesis.status] || hypothesis.status} /></Tag></div><p><b>可证伪条件：</b><BlurText text={hypothesis.falsification} /></p><p><BlurText text={hypothesis.verdict?.reason || hypothesis.reason} /></p>{hypothesis.verdict?.limitations && <p className="quiet-text">局限：{hypothesis.verdict.limitations}</p>}<Button type="link" size="small" onClick={() => onNode(hypothesis.nodeId)}>查看猜想、数据需求与论证来源</Button></article>)}{!cycle.hypotheses.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="背景和文献研究完成后，将围绕具体课题提出猜想。" />}</>}
    {mode === 'data' && <>{cycle.dataRequests.map(demand => <article key={demand.id}><div><strong>{demand.metric}</strong><Tag color={demand.status === 'satisfied' ? 'blue' : demand.status === 'blocked' ? 'orange' : 'default'}><BlurText kind="status" text={statusLabels[demand.status]} /></Tag></div><p><BlurText text={demand.definition} /></p><p><b>检验什么：</b><BlurText text={demand.purpose} /></p><p><b>验收：</b><BlurText text={demand.acceptance} /></p><p className="quiet-text">{demand.evidenceIds.length} 条回传证据 · {demand.source === 'experiment' ? '来自实验' : demand.source === 'existing' ? '来自现有资料' : '尚待取得'}</p>{demand.nodeId && <Button type="link" size="small" onClick={() => onNode(demand.nodeId!)}>查看逐级下派与回传</Button>}</article>)}{!cycle.dataRequests.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="每个猜想会明确它需要什么数据来检验。" />}</>}
    {mode === 'experiments' && <>{cycle.experiments.map(experiment => <article key={experiment.nodeId}><div><strong>实验方案 {experiment.attempt}</strong><Tag color={experiment.status === 'problem' ? 'orange' : 'default'}><BlurText kind="status" text={experiment.reviewStatus ? reviewLabels[experiment.reviewStatus] || experiment.reviewStatus : statusLabels[experiment.status]} /></Tag></div><p className="quiet-text">协议：{experiment.protocolId}</p>{experiment.reviewReason && <p><BlurText text={experiment.reviewReason} /></p>}{experiment.problem && <Alert type="warning" title={experiment.problem.kind} description={experiment.problem.message} />}<Button type="link" size="small" onClick={() => onNode(experiment.nodeId)}>执行记录</Button><Button type="link" size="small" onClick={() => onNode(experiment.designNodeId)}>设计节点与修订依据</Button></article>)}{!cycle.experiments.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="现有证据不足时，再进入实验支撑流程。" />}</>}
    {cycle.unresolved.length > 0 && <Alert type="warning" title="尚未解决" description={cycle.unresolved.join('；')} />}
  </div>;
}
