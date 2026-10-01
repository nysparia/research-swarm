import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Annotation, Compartment, EditorState, Transaction } from '@codemirror/state';
import { EditorView, drawSelection, dropCursor, keymap, placeholder as editorPlaceholder } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, redo, redoDepth, undo, undoDepth } from '@codemirror/commands';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { openSearchPanel, search, searchKeymap } from '@codemirror/search';
import { tags } from '@lezer/highlight';
import { Markdown } from '../Markdown';
import { documentChange, markdownTransaction, type MarkdownAction } from './markdownCommands';
import './markdown-editor.css';

const externalDocument = Annotation.define<boolean>();
const highlights = HighlightStyle.define([
  { tag: tags.heading, fontWeight: '600', color: '#202124' },
  { tag: tags.strong, fontWeight: '700' },
  { tag: tags.emphasis, fontStyle: 'italic' },
  { tag: tags.link, color: '#5a6480', textDecoration: 'underline', textUnderlineOffset: '3px' },
  { tag: tags.url, color: '#777a80' },
  { tag: tags.monospace, fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace', color: '#555' },
  { tag: tags.meta, color: '#888' },
]);
const editorTheme = EditorView.theme({
  '&': { height: '100%', color: '#292a2d', backgroundColor: '#fff', fontSize: '14px' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { overflow: 'auto', fontFamily: 'inherit', lineHeight: '1.9' },
  '.cm-content': { padding: '24px 24px 72px', minHeight: '100%', caretColor: '#202124' },
  '.cm-line': { padding: '0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#202124' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': { backgroundColor: '#e1e8f3' },
  '.cm-placeholder': { color: '#98999e' },
  '.cm-panels': { backgroundColor: '#fafafa', borderColor: '#ececee' },
  '.cm-search': { padding: '10px 14px', fontFamily: 'inherit', fontSize: '13px' },
  '.cm-textfield': { border: '1px solid #ddd', borderRadius: '5px', padding: '3px 6px', background: '#fff' },
  '.cm-button': { border: '1px solid #dedee0', borderRadius: '5px', background: '#fff', padding: '3px 7px', textTransform: 'none' },
});

const labels: Record<MarkdownAction | 'undo' | 'redo' | 'search', string> = {
  heading: '标题', bold: '加粗 (Ctrl/⌘ B)', italic: '斜体 (Ctrl/⌘ I)', list: '列表', checklist: '任务列表',
  link: '链接 (Ctrl/⌘ K)', code: '代码块', table: '表格', undo: '撤销 (Ctrl/⌘ Z)', redo: '重做', search: '查找与替换 (Ctrl/⌘ F)',
};
function ToolIcon({ name }: { name: keyof typeof labels }) {
  if (name === 'heading') return <span className="sw-md-letter">H</span>;
  if (name === 'bold') return <b className="sw-md-letter">B</b>;
  if (name === 'italic') return <i className="sw-md-letter">I</i>;
  const paths: Record<string, string> = {
    list: 'M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01',
    checklist: 'm3 6 1.5 1.5L7 4M10 6h11M3 12h4v4H3zM10 14h11M10 20h11',
    link: 'm10 13 4-4M8 15l-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 5a3 3 0 0 0 4 0l5-5a3 3 0 0 0-4-4l-2 2',
    code: 'm7 7-5 5 5 5m10-10 5 5-5 5M14 4l-4 16',
    table: 'M3 4h18v16H3zM3 10h18M3 15h18M10 4v16',
    undo: 'm7 4-4 4 4 4M3 8h10a7 7 0 0 1 0 14',
    redo: 'm17 4 4 4-4 4M21 8H11a7 7 0 0 0 0 14',
    search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

interface MarkdownEditorProps {
  documentId: string;
  value: string;
  preview: boolean;
  readOnly?: boolean;
  onChange: (text: string) => void;
  onComposingChange: (composing: boolean) => void;
  onSave: () => Promise<boolean>;
}

export function MarkdownEditor(props: MarkdownEditorProps) {
  const { documentId, value, preview, readOnly = false } = props;
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const editability = useRef(new Compartment());
  const current = useRef(props);
  current.current = props;
  const composing = useRef(false);
  const pendingFrame = useRef<number | null>(null);
  const previousPreview = useRef(preview);
  const [historyState, setHistoryState] = useState({ undo: false, redo: false });

  function syncValue() {
    const view = editor.current;
    if (!view || composing.current || view.composing) return;
    const change = documentChange(view.state.doc.toString(), current.current.value);
    if (!change) return;
    view.dispatch({ changes: change, annotations: [externalDocument.of(true), Transaction.addToHistory.of(false)] });
  }

  useLayoutEffect(() => {
    if (!host.current) return;
    const format = (action: MarkdownAction) => (view: EditorView) => {
      if (view.composing || composing.current || view.state.readOnly) return false;
      view.dispatch(markdownTransaction(view.state, action));
      return true;
    };
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({ doc: current.current.value, extensions: [
        history(), drawSelection(), dropCursor(), EditorView.lineWrapping,
        editability.current.of([EditorState.readOnly.of(Boolean(current.current.readOnly)), EditorView.editable.of(!current.current.readOnly)]),
        markdown({ base: markdownLanguage, completeHTMLTags: false }),
        syntaxHighlighting(highlights), editorTheme,
        search({ top: true }),
        EditorState.phrases.of({
          'Find': '查找', 'Replace': '替换为', 'next': '下一项', 'previous': '上一项', 'all': '全部',
          'match case': '区分大小写', 'by word': '完整词语', 'regexp': '正则表达式',
          'replace': '替换', 'replace all': '全部替换', 'close': '关闭', 'Search': '查找',
        }),
        keymap.of([
          { key: 'Mod-b', run: format('bold') }, { key: 'Mod-i', run: format('italic') },
          { key: 'Mod-k', run: format('link') }, { key: 'Mod-Alt-2', run: format('heading') },
          { key: 'Mod-s', run: () => { if (!composing.current) void current.current.onSave(); return true; } },
          ...historyKeymap, ...searchKeymap, ...defaultKeymap,
        ]),
        editorPlaceholder('需求会随对话逐步形成，也可以直接编辑。'),
        EditorView.contentAttributes.of({ 'aria-label': '需求 Markdown 正文', 'aria-multiline': 'true', spellcheck: 'false', autocorrect: 'off', autocapitalize: 'off' }),
        EditorView.domEventHandlers({
          compositionstart: () => { composing.current = true; current.current.onComposingChange(true); },
          compositionend: () => {
            composing.current = false;
            current.current.onComposingChange(false);
            if (pendingFrame.current !== null) cancelAnimationFrame(pendingFrame.current);
            pendingFrame.current = requestAnimationFrame(() => { pendingFrame.current = null; syncValue(); });
          },
          blur: () => { if (!composing.current) void current.current.onSave(); },
        }),
        EditorView.updateListener.of(update => {
          if (!update.docChanged) return;
          if (!update.transactions.every(transaction => transaction.annotation(externalDocument))) current.current.onChange(update.state.doc.toString());
          setHistoryState({ undo: undoDepth(update.state) > 0, redo: redoDepth(update.state) > 0 });
        }),
      ] }),
    });
    editor.current = view;
    setHistoryState({ undo: false, redo: false });
    return () => {
      if (pendingFrame.current !== null) cancelAnimationFrame(pendingFrame.current);
      pendingFrame.current = null;
      composing.current = false;
      current.current.onComposingChange(false);
      view.destroy();
      editor.current = null;
    };
  }, [documentId]);

  useLayoutEffect(() => { syncValue(); }, [value]);
  useLayoutEffect(() => { editor.current?.dispatch({ effects: editability.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]) }); }, [readOnly]);
  useEffect(() => {
    if (previousPreview.current && !preview) { editor.current?.requestMeasure(); editor.current?.focus(); }
    previousPreview.current = preview;
  }, [preview]);

  const run = (action: keyof typeof labels) => {
    const view = editor.current;
    if (!view || composing.current || view.composing || view.state.readOnly) return;
    if (action === 'undo') undo(view);
    else if (action === 'redo') redo(view);
    else if (action === 'search') { openSearchPanel(view); return; }
    else view.dispatch(markdownTransaction(view.state, action));
    view.focus();
  };
  const tool = (action: keyof typeof labels) => <button key={action} type="button" aria-label={labels[action]} title={labels[action]} disabled={readOnly || (action === 'undo' ? !historyState.undo : action === 'redo' ? !historyState.redo : false)} onMouseDown={event => event.preventDefault()} onClick={() => run(action)}><ToolIcon name={action} /></button>;

  return <div className="sw-markdown-editor">
    <div className="sw-md-toolbar" role="toolbar" aria-label="Markdown 格式工具" hidden={preview}>
      {tool('heading')}{tool('bold')}{tool('italic')}<span className="sw-md-separator" />
      {tool('list')}{tool('checklist')}{tool('link')}{tool('code')}{tool('table')}
      <span className="sw-md-toolbar-space" />{tool('search')}{tool('undo')}{tool('redo')}
    </div>
    <div className="sw-md-source" ref={host} hidden={preview} />
    {preview && <div className="sw-md-preview" aria-label="Markdown 预览" tabIndex={0}>{value.trim() ? <Markdown text={value} /> : <p className="sw-md-empty">需求文档还没有内容。</p>}</div>}
  </div>;
}
