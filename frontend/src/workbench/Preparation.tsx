import { useState, type ReactNode } from 'react';
import type { TaskDetail } from '../taskTypes';
import type { useDocumentDraft } from '../useDocumentDraft';
import { Markdown } from '../Markdown';
import { ConceptUnderstandingPanel } from '../ConceptUnderstandingPanel';
import { conceptProgress, needsConceptClarification } from '../conceptState';
import { MarkdownEditor } from './MarkdownEditor';
import { Button, ErrorNote, Icon } from './ui';

export function Preparation({ detail, editor, busy, modelReady, onStart, onMode, composer, conversation, separator }: {
  detail: TaskDetail;
  editor: ReturnType<typeof useDocumentDraft>;
  busy: string;
  modelReady: boolean;
  onStart: () => void;
  onMode: (mode: 'research' | 'reproduction') => void;
  composer: ReactNode;
  conversation?: ReactNode;
  separator?: ReactNode;
}) {
  const [preview, setPreview] = useState(false);
  const saveStatus = editor.draft.conflict ? '版本冲突' : editor.draft.saving ? '保存中' : editor.draft.error ? '保存失败' : editor.draft.dirty ? '待保存' : detail.document.polishing ? 'AI 正在润色' : `已保存 · v${editor.draft.baseRevision}`;
  return <div className="sw-preparation sw-preparation-full">
    <section className="sw-prep-conversation" aria-label="明确研究需求">
      {conversation || <><div className="sw-prep-messages">{detail.messages.map(item => <article key={item.id} className={`sw-prep-message sw-prep-message-${item.role}`}><Markdown text={item.content} /></article>)}</div><div className="sw-prep-composer">{composer}</div></>}
    </section>
    {separator}
    <section className="sw-prep-document" aria-label="完整 Markdown 编辑器">
      <header><div className="sw-prep-document-name"><Icon name="book" /><strong>研究需求</strong><span className="sw-file-extension">.md</span></div><div className="sw-prep-document-actions"><select aria-label="研究方式" value={detail.taskMode === 'reproduction' ? 'reproduction' : 'research'} disabled={!!busy || detail.document.polishing} onChange={event => onMode(event.target.value as 'research' | 'reproduction')}><option value="research">探索研究</option><option value="reproduction">论文复现</option></select><button type="button" className="sw-md-preview-toggle" aria-pressed={preview} onClick={() => setPreview(!preview)}>{preview ? '编辑' : '预览'}</button></div></header>
      <MarkdownEditor documentId={detail.task.id} value={editor.draft.text} preview={preview} readOnly={['start', 'mode', 'message', 'decision', 'switch'].includes(busy)} onChange={editor.edit} onComposingChange={editor.setComposing} onSave={editor.save} />
      <ConceptUnderstandingPanel detail={detail} />
      {editor.draft.conflict && <div className="sw-notice sw-prep-conflict" role="alert"><p>{editor.draft.error || '需求已更新。你的修改仍保留在编辑器中。'}</p><div><Button onClick={editor.keepLocal}>保留我的修改</Button><Button onClick={editor.useServer}>采用最新版本</Button></div></div>}
      {((editor.draft.error && !editor.draft.conflict) || detail.document.error) && <ErrorNote onRetry={editor.draft.dirty && !editor.draft.conflict ? () => { void editor.save(); } : undefined}>{editor.draft.error || detail.document.error}</ErrorNote>}
      <footer><span className="sw-prep-save-status" role="status">{detail.document.polishing && <span className="sw-spinner" />}{detail.document.polishing ? conceptProgress(detail.document) : saveStatus}</span><Button variant="primary" icon="arrow" busy={busy === 'start'} title={needsConceptClarification(detail.document) ? '请先在对话中明确核心研究对象' : modelReady ? '确认需求边界，开始研究' : '确认需求边界，开始本地资料核验'} disabled={needsConceptClarification(detail.document) || !editor.draft.text.trim() || editor.draft.saving || editor.draft.conflict || detail.document.polishing || !!busy} onClick={onStart}>开始研究</Button></footer>
    </section>
  </div>;
}
