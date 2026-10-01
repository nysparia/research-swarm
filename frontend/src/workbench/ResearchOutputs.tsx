import { useState } from 'react';
import { Markdown } from '../Markdown';
import type { TaskDetail } from '../taskTypes';
import type { Artifact } from './types';
import { plain, safeDownload } from './state';
import { Badge, Button, Empty, Icon } from './ui';

export function ResearchOutputs({ detail, onInspect, onDiscuss, onExport, onStructure }: {
  detail: TaskDetail; onInspect: (artifact: Artifact) => void; onDiscuss: (artifact: Artifact) => void; onExport: () => void; onStructure: () => void;
}) {
  const [view, setView] = useState('report');
  const artifacts = (detail.workbench?.artifacts || []).filter(a => !['stale', 'historical'].includes(a.status));
  const paper = artifacts.filter(a => a.kind === 'expression' && a.content.kind === 'paper').at(-1);
  const report = artifacts.find(a => a.id === 'report:live');
  const displayed = view === 'paper' && paper ? paper : report;
  const markdown = view === 'paper' && paper ? plain(paper.content.markdown) : detail.workbench?.report.markdown || detail.state?.report.summary || '';
  const claims = detail.state?.report.claims || [];
  return <section className="sw-outputs" aria-label="研究产物">
    <header className="sw-output-header"><strong>研究产物</strong><div><Button icon="layers" onClick={onStructure} title="查看完整 agent 结构">研究过程</Button><Button icon="download" onClick={onExport}>导出</Button></div></header>
    <nav className="sw-output-files" aria-label="产物文件"><button className={view === 'report' ? 'active' : ''} onClick={() => setView('report')}><Icon name="book" />{detail.workbench?.report.ready ? '研究报告' : '研究记录'}</button>{paper && <button className={view === 'paper' ? 'active' : ''} onClick={() => setView('paper')}><Icon name="book" />论文草稿</button>}<button className={view === 'files' ? 'active' : ''} onClick={() => setView('files')}><Icon name="folder" />实验材料</button><button className={view === 'evidence' ? 'active' : ''} onClick={() => setView('evidence')}><Icon name="link" />证据档案</button></nav>
    <div className="sw-output-scroll">
      {view === 'files' ? <div className="sw-output-file-list">{detail.artifacts.length ? detail.artifacts.map(file => <a key={file.url} href={safeDownload(file.url)} download><Icon name="book" /><span>{file.name}</span><Icon name="download" /></a>) : <Empty title="本轮还没有交付文件" />}</div> : view === 'evidence' ? <div className="sw-output-evidence">{artifacts.filter(a => a.kind === 'claim').map(claim => <button key={claim.id} onClick={() => onInspect(claim)}><strong>{plain(claim.content.statement) || claim.title}</strong><span>{claim.evidenceIds.length ? `${claim.evidenceIds.length} 条证据 · 查看来源` : '无证据'}</span></button>)}{!artifacts.some(a => a.kind === 'claim') && <Empty title="尚无已形成的主张" />}</div> : <article className="sw-output-paper">
        <div className="sw-output-paper-meta"><Badge status={detail.workbench?.report.approved ? 'confirmed' : 'draft'} text={detail.workbench?.report.approved ? '已确认' : detail.workbench?.report.ready ? '待审阅' : '形成中'} />{displayed && <Button icon="chat" onClick={() => onDiscuss(displayed)}>继续讨论</Button>}</div>
        {markdown ? <Markdown text={markdown} /> : <Empty icon="book" title="研究结果尚未形成" />}
        {!!claims.length && <section className="sw-output-sources"><h2>结论依据</h2>{claims.map(claim => { const source = artifacts.find(a => a.kind === 'claim' && a.claimRefs.some(ref => ref.claimId === claim.claimId && ref.version === claim.claimVersion)); return <div key={claim.id}><p>{claim.text}</p>{source ? <Button icon="link" onClick={() => onInspect(source)}>{source.evidenceIds.length ? '查看证据' : '无证据'}</Button> : <span className="sw-missing-evidence">无可定位的证据</span>}</div>; })}</section>}
      </article>}
    </div>
  </section>;
}
