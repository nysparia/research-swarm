import { Alert } from 'antd';
import type { Snapshot } from './types';

const names: Record<string, string> = { openalex: 'OpenAlex', arxiv: 'arXiv', semantic_scholar: 'Semantic Scholar', crossref: 'Crossref' };
const reasons: Record<string, string> = { http_429: '接口限流', http_401: '鉴权失败', http_403: '访问被拒绝', rate_limited: '接口要求延后重试', network_unavailable: '网络不可用', invalid_response: '接口返回格式异常', time_budget: '时间预算已用完', request_or_time_budget: '请求次数或时间预算已用完' };

export function SearchHistory({ operations }: { operations: Snapshot['operations'] }) {
  const runs = operations?.filter(operation => operation.retrieval?.version === 'multi-source/v1') || [];
  if (!runs.length) return null;
  return <section className="process-log" aria-label="论文检索记录"><h2>论文检索记录</h2>{runs.map(run => {
    const result = run.retrieval!;
    return <details key={run.id}><summary>{run.message}</summary>
      <p>API 请求 {result.requestCount} 次 · 缓存命中 {result.cacheHits} 次 · 合并重复记录 {result.duplicateCount} 条 · 保留引用关系 {result.retainedCitationEdges} 条</p>
      <p>{Object.entries(result.sourceCounts || {}).map(([source, count]) => `${names[source] || source}：${count} 篇`).join('；')}。同一论文可能来自多个源，各源数量不可直接相加。</p>
      {result.stopReason && <Alert type="warning" showIcon description={reasons[result.stopReason] || result.stopReason} />}
      {!!result.errors?.length && <Alert type="warning" showIcon description={[...new Set(result.errors.map(error => `${names[error.source] || error.source}：${reasons[error.reason] || error.reason}`))].join('；')} />}
      <ul>{result.queries?.map((query, index) => <li key={index}>{query.query} · {query.yearFrom ? `${query.yearFrom} 年起` : '不限起始年份'}</li>)}</ul>
    </details>;
  })}</section>;
}
