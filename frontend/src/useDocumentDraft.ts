import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, messageOf, taskPath } from './taskApi';
import { draftReducer, emptyDraft, type DraftAction } from './documentState';
import type { TaskDetail } from './taskTypes';
import { createDraftPersistence } from './documentPersistence';

export function useDocumentDraft(detail: TaskDetail | null, accept: (detail: TaskDetail) => void) {
  const [draft, setDraft] = useState(emptyDraft);
  const current = useRef(draft);
  const [composing, setComposingState] = useState(false);
  const composingRef = useRef(false);
  const setComposing = useCallback((value: boolean) => { composingRef.current = value; setComposingState(value); }, []);
  const update = useCallback((action: DraftAction) => { current.current = draftReducer(current.current, action); setDraft(current.current); }, []);
  useEffect(() => {
    const id = detail?.task.id || null;
    if (current.current.taskId !== id) update({ type: 'reset', taskId: id, document: detail?.document || null });
    else if (detail) update({ type: 'server', document: detail.document });
  }, [detail?.task.id, detail?.document.revision, detail?.document.markdown, detail?.document.polishing, detail?.document.polishedFrom, detail?.document.error, update]);

  const { save, isCurrentSaved } = useMemo(() => createDraftPersistence({
    current: () => current.current,
    composing: () => composingRef.current,
    update,
    persist: (taskId, markdown, expectedRevision) => api<TaskDetail>(taskPath(taskId, '/document'), { markdown, expectedRevision }),
    reload: taskId => api<TaskDetail>(taskPath(taskId)),
    accept,
    isConflict: error => error instanceof ApiError && error.status === 409,
    describeError: messageOf,
  }), [accept, update]);
  const editable = detail?.phase === 'requirements' || detail?.phase === 'empty';
  useEffect(() => {
    if (!editable || !draft.dirty || draft.saving || draft.conflict || draft.error || composing) return;
    const timer = window.setTimeout(() => { void save(); }, 1100);
    return () => window.clearTimeout(timer);
  }, [editable, draft.editVersion, draft.baseRevision, draft.dirty, draft.saving, draft.conflict, draft.error, composing, save]);
  useEffect(() => {
    if (!draft.dirty) return;
    const handler = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [draft.dirty]);
  return { draft, save, isCurrentSaved, setComposing, edit: (text: string) => update({ type: 'edit', text }), keepLocal: () => update({ type: 'keep-local' }), useServer: () => update({ type: 'use-server' }) };
}
