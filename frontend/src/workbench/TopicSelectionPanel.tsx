import { useEffect, useRef, useState } from 'react';
import type { ResearchCycle, TopicCandidate } from '../types';
import { Button } from './ui';
import { pendingTopic, topicSelectionRequest, type TopicSelectionRequest } from './topicSelectionState';
import './topic-selection.css';

export function TopicSelectionPanel({ cycle, revision, busy, onSelect }: { cycle: ResearchCycle; revision: number; busy: boolean; onSelect: (request: TopicSelectionRequest) => void }) {
  const [customText, setCustomText] = useState('');
  const [editing, setEditing] = useState<TopicCandidate | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (editing) input.current?.focus(); }, [editing]);
  if (!pendingTopic(cycle)) return null;
  return <section className="sw-topic-selection" aria-label="候选课题选择">
    <strong>选择一个课题继续研究</strong>
    <p>以下方向来自当前文献与证据缺口。你可以选择、编辑，或提出自己的课题。</p>
    {(cycle.topicCandidates || []).map((candidate, index) => <article key={candidate.id} className="sw-topic-card">
      <h3>{index + 1}. {candidate.title}</h3>
      <p>{candidate.question}</p>
      <dl><dt>研究缺口</dt><dd>{candidate.researchGap}</dd><dt>最小研究方案</dt><dd>{candidate.minimalStudy}</dd><dt>可行性</dt><dd>{candidate.feasibility}</dd></dl>
      <details><summary>{new Set(candidate.evidenceIds).size} 条证据 · 依据与局限</summary><p>{candidate.rationale}</p><p>{candidate.limitations}</p><p>证据：{candidate.evidenceIds.join('、')}</p></details>
      <div className="sw-topic-actions"><Button variant="primary" disabled={busy} onClick={() => onSelect(topicSelectionRequest(revision, 'candidate', candidate.id))}>选择此课题</Button>
        <Button variant="outline" disabled={busy} onClick={() => { setEditing(candidate); setCustomText(candidate.question); }}>编辑候选</Button></div>
    </article>)}
    <label className="sw-topic-custom">{editing ? `编辑：${editing.title}` : '自定义研究课题'}
      <textarea ref={input} aria-label="自定义研究课题" placeholder="输入你想研究的具体问题，或结合上述方向重新表述" maxLength={8000} value={customText} disabled={busy} onChange={event => setCustomText(event.target.value)} />
    </label>
    <div className="sw-topic-actions"><Button variant="primary" disabled={busy || !customText.trim()} onClick={() => onSelect(topicSelectionRequest(revision, editing ? 'edited' : 'custom', customText, editing?.id))}>{editing ? '确定编辑后的课题' : '使用自定义课题'}</Button>
      {editing && <Button disabled={busy} onClick={() => { setEditing(null); setCustomText(''); }}>取消编辑</Button>}</div>
  </section>;
}
