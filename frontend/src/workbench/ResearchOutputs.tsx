import { useState } from 'react';
import { Markdown } from '../Markdown';
import type { TaskDetail } from '../taskTypes';
import type { Artifact } from './types';
import { plain, safeDownload } from './state';
import { Badge, Button, Empty, Icon } from './ui';
import { MacSegmentedControl } from './NativeControls';
import { GlassButton, GlassGroup } from './GlassChrome';

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
    <header className="sw-output-header">
      <nav className="sw-output-files" aria-label="产物文件"><MacSegmentedControl value={view} onChange={setView} material="glass" label="产物类型" options={[{ value: 'report', label: detail.workbench?.report.ready ? '研究报告' : '研究记录', icon: 'book' }, ...(paper ? [{ value: 'paper', label: '论文草稿', icon: 'book' }] : []), { value: 'files', label: '实验材料', icon: 'folder' }, { value: 'evidence', label: '证据档案', icon: 'link' }]} /></nav>
      <GlassGroup className="sw-output-actions" label="成果操作"><GlassButton icon="back" onClick={onStructure} aria-label="回到研究过程" title="回到研究过程" />{displayed && ['report', 'paper'].includes(view) && <GlassButton icon="chat" onClick={() => onDiscuss(displayed)}>继续讨论</GlassButton>}<GlassButton icon="download" onClick={onExport} title="导出研究档案">导出</GlassButton></GlassGroup>
    </header>
    <div className="sw-output-scroll">
      {view === 'files' ? <div className="sw-output-file-list">{detail.artifacts.length ? detail.artifacts.map(file => <a key={file.url} href={safeDownload(file.url)} download><Icon name="book" /><span>{file.name}</span><Icon name="download" /></a>) : <Empty title="本轮还没有交付文件" />}</div> : view === 'evidence' ? <div className="sw-output-evidence">{artifacts.filter(a => a.kind === 'claim').map(claim => <button key={claim.id} onClick={() => onInspect(claim)}><strong>{plain(claim.content.statement) || claim.title}</strong><span>{claim.evidenceIds.length ? `${claim.evidenceIds.length} 条证据 · 查看来源` : '无证据'}</span></button>)}{!artifacts.some(a => a.kind === 'claim') && <Empty title="尚无已形成的主张" />}</div> : <article className="sw-output-paper">
        <div className="sw-output-paper-meta"><Badge status={detail.workbench?.report.approved ? 'confirmed' : 'draft'} text={detail.workbench?.report.approved ? '已确认' : detail.workbench?.report.ready ? '待审阅' : '形成中'} /></div>
        {markdown ? <Markdown text={markdown} /> : <Empty icon="book" title="研究结果尚未形成" />}
        {!!claims.length && <section className="sw-output-sources"><h2>结论依据</h2>{claims.map(claim => { const source = artifacts.find(a => a.kind === 'claim' && a.claimRefs.some(ref => ref.claimId === claim.claimId && ref.version === claim.claimVersion)); return <div key={claim.id}><p>{claim.text}</p>{source ? <Button icon="link" onClick={() => onInspect(source)}>{source.evidenceIds.length ? '查看证据' : '无证据'}</Button> : <span className="sw-missing-evidence">无可定位的证据</span>}</div>; })}</section>}
      </article>}
    </div>
  </section>;
}
