import { useEffect, useState } from 'react';
import { api, messageOf, taskPath } from './taskApi';
import { conceptBlockers, conceptProgress, conceptSourceLabel, hasPendingConceptFacts, needsConceptClarification } from './conceptState';
import type { TaskDetail } from './taskTypes';

interface ConceptRun {
  id: string; status: string; stale?: boolean; requestCount: number; startedAt: string;
  draftError?: string; error?: string;
  lookups: { term: string; query: string; status: string; error?: string; cacheHit: boolean; reason: string;
    sources: { id: string; title: string; url: string; content: string; sourceKind?: 'official' | 'third_party' }[] }[];
}
export function ConceptUnderstandingPanel({ detail }: { detail: TaskDetail }) {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState<ConceptRun[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const understanding = detail.document.conceptUnderstanding;
  useEffect(() => { setOpen(false); setRuns([]); setError(''); }, [detail.task.id]);
  useEffect(() => {
    if (!open || !understanding?.runId) return;
    let active = true; setLoading(true); setError('');
    void api<{ runs: ConceptRun[] }>(taskPath(detail.task.id, '/concept-search')).then(data => { if (active) setRuns(data.runs); }).catch(e => { if (active) setError(messageOf(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, detail.task.id, understanding?.runId, understanding?.status, detail.document.revision]);
  if (!understanding) return null;
  const run = runs.find(item => item.id === understanding.runId);
  return <aside className="sw-concept-understanding" aria-label="概念理解参考">
    {!detail.document.polishing && ['failed', 'interrupted'].includes(understanding.status) && <div className="sw-notice" role="alert"><strong>{conceptProgress(detail.document)}</strong><p>草稿已保留，请在对话中重试。</p></div>}
    {understanding.status === 'needs_clarification' && needsConceptClarification(detail.document) && <div className="sw-notice" role="alert"><strong>先明确核心研究对象</strong>{conceptBlockers(detail.document).map(item => <p key={item.term}>{item.question}</p>)}<p>请在对话中补充定义、全称或确认所指对象，草稿已保留。</p></div>}
    {understanding.status === 'ready' && hasPendingConceptFacts(detail.document) && <p className="sw-muted">官方资料待核实，已保留为研究问题。</p>}
    {understanding.runId && <details open={open} onToggle={event => setOpen(event.currentTarget.open)}><summary>概念理解参考 · {conceptProgress(detail.document)}</summary>
      <p className="sw-muted">这些来源仅帮助理解问题，不是论文检索结果或科研证据。</p>
      {loading && <p role="status">正在读取参考记录…</p>}{error && <p role="alert">{error}</p>}
      {run && <><p>实际请求 {run.requestCount} 次{run.stale ? ' · 历史记录，未应用于当前需求' : ''}</p>
        {run.lookups.map((lookup, index) => <section key={index}><strong>{lookup.term}</strong><p>{lookup.reason}</p><p className="sw-muted">查询：{lookup.query}{lookup.cacheHit ? ' · 使用本任务缓存' : ''}</p>
          {lookup.error && <p>{lookup.error}</p>}{lookup.status === 'empty' && <p>未找到可用的概念说明。</p>}
          {lookup.sources.map(source => <details key={source.id}><summary>{conceptSourceLabel(source.sourceKind)} · {source.title || source.url}</summary><a href={source.url} target="_blank" rel="noopener noreferrer">查看来源</a><p>{source.content}</p></details>)}
        </section>)}{(run.error || run.draftError) && <p role="alert">{run.error || run.draftError}</p>}
      </>}
    </details>}
  </aside>;
}
