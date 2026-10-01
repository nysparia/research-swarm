import { EditorSelection, EditorState, type TransactionSpec } from '@codemirror/state';
import { isolateHistory } from '@codemirror/commands';

export type MarkdownAction = 'heading' | 'bold' | 'italic' | 'list' | 'checklist' | 'link' | 'code' | 'table';

/** One transaction per toolbar action, so a single undo restores the selection and text. */
export function markdownTransaction(state: EditorState, action: MarkdownAction): TransactionSpec {
  const { from, to } = state.selection.main;
  const selected = state.sliceDoc(from, to);
  const spec = (insert: string, start: number, end = start, changeFrom = from, changeTo = to): TransactionSpec => ({
    changes: { from: changeFrom, to: changeTo, insert },
    selection: EditorSelection.single(start, end),
    scrollIntoView: true,
    annotations: isolateHistory.of('full'),
    userEvent: 'input.markdown-format',
  });

  if (action === 'bold' || action === 'italic') {
    const mark = action === 'bold' ? '**' : '*';
    if (selected.length >= mark.length * 2 && selected.startsWith(mark) && selected.endsWith(mark)) {
      const plain = selected.slice(mark.length, -mark.length);
      return spec(plain, from, from + plain.length);
    }
    if (from >= mark.length && state.sliceDoc(from - mark.length, from) === mark && state.sliceDoc(to, to + mark.length) === mark) {
      return spec(selected, from - mark.length, to - mark.length, from - mark.length, to + mark.length);
    }
    return spec(mark + selected + mark, from + mark.length, to + mark.length);
  }

  if (action === 'heading' || action === 'list' || action === 'checklist') {
    const first = state.doc.lineAt(from);
    // An end at the start of the next line must not format that next line.
    const last = state.doc.lineAt(to > from && state.doc.lineAt(to).from === to ? to - 1 : to);
    const lines = state.sliceDoc(first.from, last.to).split('\n');
    const marker = action === 'heading' ? '## ' : action === 'list' ? '- ' : '- [ ] ';
    const active = action === 'heading' ? /^(\s*)## / : action === 'list' ? /^(\s*)[-*+] (?!\[[ xX]\] )/ : /^(\s*)[-*+] \[[ xX]\] /;
    const remove = lines.every(line => active.test(line));
    const formatted = lines.map(line => {
      if (remove) return line.replace(active, '$1');
      const indentation = line.match(/^\s*/)?.[0] || '';
      const content = line.slice(indentation.length).replace(/^(?:#{1,6}\s+|(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)/, '');
      return indentation + marker + content;
    }).join('\n');
    if (from === to) {
      const delta = formatted.length - (last.to - first.from);
      const cursor = Math.max(first.from, Math.min(first.from + formatted.length, from + delta));
      return spec(formatted, cursor, cursor, first.from, last.to);
    }
    return spec(formatted, first.from, first.from + formatted.length, first.from, last.to);
  }

  if (action === 'link') {
    const insert = `[${selected}](https://)`;
    return selected
      ? spec(insert, from + selected.length + 3, from + insert.length - 1)
      : spec(insert, from + 1);
  }

  if (action === 'code') {
    const longest = Math.max(2, ...(selected.match(/`+/g) || []).map(value => value.length));
    const fence = '`'.repeat(longest + 1);
    const before = from > 0 && state.sliceDoc(from - 1, from) !== '\n' ? '\n' : '';
    const after = to < state.doc.length && state.sliceDoc(to, to + 1) !== '\n' ? '\n' : '';
    const prefix = before + fence + '\n';
    return spec(prefix + selected + '\n' + fence + after, from + prefix.length, from + prefix.length + selected.length);
  }

  const before = from > 0 && state.sliceDoc(from - 1, from) !== '\n' ? '\n\n' : '';
  const cell = selected.replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
  const prefix = before + '| 指标 | 验收标准 |\n| --- | --- |\n| ';
  const insert = prefix + cell + ' |  |\n';
  return spec(insert, from + prefix.length, from + prefix.length + cell.length);
}

/** Preserve unchanged text and map editor selection when a saved/AI version arrives. */
export function documentChange(current: string, next: string): { from: number; to: number; insert: string } | null {
  if (current === next) return null;
  let from = 0;
  while (from < current.length && from < next.length && current[from] === next[from]) from += 1;
  if (from > 0 && /[\uD800-\uDBFF]/.test(current[from - 1])) from -= 1;
  let oldEnd = current.length;
  let newEnd = next.length;
  while (oldEnd > from && newEnd > from && current[oldEnd - 1] === next[newEnd - 1]) { oldEnd -= 1; newEnd -= 1; }
  if (oldEnd < current.length && /[\uDC00-\uDFFF]/.test(current[oldEnd])) { oldEnd += 1; newEnd += 1; }
  return { from, to: oldEnd, insert: next.slice(from, newEnd) };
}
