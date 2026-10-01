import { useState, type ReactNode } from 'react';
import type { TaskDetail } from '../taskTypes';
import type { useDocumentDraft } from '../useDocumentDraft';
import { Markdown } from '../Markdown';
import { BlurText } from '../BlurReveal';
import { Avatar, Button, ErrorNote, Icon } from './ui';

export function Preparation({ detail, editor, busy, modelReady, onStart, onMode, composer }: { detail: TaskDetail; editor: ReturnType<typeof useDocumentDraft>; busy: string; modelReady: boolean; onStart: () => void; onMode: (mode: 'research' | 'reproduction') => void; composer: ReactNode }) {
  const [preview, setPreview] = useState(false);
  return <div className="sw-preparation">
    <section className="sw-prep-conversation">
      <div className="sw-mode-switch" aria-label="研究方式"><button className={detail.taskMode !== 'reproduction' ? 'active' : ''} onClick={() => onMode('research')} disabled={!!busy}><Icon name="spark" /><strong>探索研究</strong><span>从问题出发，形成主张与证据</span></button><button className={detail.taskMode === 'reproduction' ? 'active' : ''} onClick={() => onMode('reproduction')} disabled={!!busy}><Icon name="lab" /><strong>论文复现</strong><span>检查方法，设计并执行实验</span></button></div>
      <div className="sw-prep-messages">{detail.messages.map(item => <article key={item.id} className={`sw-message sw-message-${item.role}`}><div className="sw-message-label">{item.role === 'user' ? <span className="sw-you">你</span> : <Avatar small />}<strong>{item.role === 'user' ? '你的想法' : item.role === 'system' ? '任务记录' : '研究助手'}</strong></div><Markdown text={item.content} /></article>)}</div>
      {!!detail.document.questions.length && <section className="sw-open-questions"><h3>这些问题还能更明确</h3>{detail.document.questions.map((question, i) => <p key={i}><span>{i + 1}</span>{question}</p>)}</section>}
    <div className="sw-prep-composer">{composer}</div></section>
    <section className="sw-prep-document"><header><div><Icon name="book" /><strong>研究草稿</strong><span><BlurText kind="status" text={editor.draft.saving ? '保存中' : editor.draft.dirty ? '未保存' : detail.document.polishing ? '正在整理' : `v${editor.draft.baseRevision}`} /></span></div><div className="sw-segments"><button className={!preview ? 'active' : ''} onClick={() => setPreview(false)}>编辑</button><button className={preview ? 'active' : ''} onClick={() => setPreview(true)}>预览</button></div></header>
      <div className="sw-prep-paper">{!editor.draft.text && detail.document.polishing ? <div className="sw-loading"><span className="sw-spinner" />正在把你的问题整理为研究需求…</div> : preview ? <Markdown text={editor.draft.text} /> : <textarea aria-label="在线编辑研究需求 Markdown" value={editor.draft.text} onChange={event => editor.edit(event.target.value)} onCompositionStart={() => editor.setComposing(true)} onCompositionEnd={() => editor.setComposing(false)} onBlur={() => { void editor.save(); }} placeholder="需求文档会在这里形成。你可以直接编辑。" spellCheck={false} />}</div>
      {editor.draft.conflict && <div className="sw-notice"><p>{editor.draft.error}</p><Button onClick={editor.keepLocal}>保留我的修改</Button><Button onClick={editor.useServer}>采用服务端版本</Button></div>}
      {(editor.draft.error && !editor.draft.conflict || detail.document.error) && <ErrorNote>{editor.draft.error || detail.document.error}</ErrorNote>}
      <footer><p>{detail.document.source === 'model' ? '自动保存；AI 润色会保留你明确写下的边界。' : '当前为本地草稿，可随时编辑与补充。'}</p><div><span>{modelReady ? '确认后开始检索与研究' : '当前为本地资料核验模式'}</span><Button variant="primary" icon="arrow" busy={busy === 'start'} disabled={!editor.draft.text.trim() || editor.draft.saving || editor.draft.conflict || detail.document.polishing || !!busy} onClick={onStart}>确认需求，开始研究</Button></div></footer>
    </section>
  </div>;
}
