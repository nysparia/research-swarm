import type { DraftAction, DraftState } from './documentState';
import type { TaskDetail } from './taskTypes';

interface DraftPersistenceOptions {
  current: () => DraftState;
  composing: () => boolean;
  update: (action: DraftAction) => void;
  persist: (taskId: string, markdown: string, expectedRevision: number) => Promise<TaskDetail>;
  reload: (taskId: string) => Promise<TaskDetail>;
  accept: (detail: TaskDetail) => void;
  isConflict: (error: unknown) => boolean;
  describeError: (error: unknown) => string;
}

/** Serializes saves, but never grants a transition based on an older submitted edit. */
export function createDraftPersistence(options: DraftPersistenceOptions) {
  let pending: Promise<boolean> | null = null;
  const isCurrentSaved = (taskId: string) => {
    const draft = options.current();
    return draft.taskId === taskId && !draft.dirty && !draft.saving && !draft.conflict && !draft.error && !options.composing();
  };
  const save = async (): Promise<boolean> => {
    if (options.composing()) return false;
    if (pending) { const okay = await pending; if (!okay) return false; }
    if (options.composing()) return false;
    const value = options.current();
    if (!value.dirty) return !value.saving && !value.conflict && !value.error;
    if (!value.taskId || value.conflict) return false;
    const taskId = value.taskId;
    options.update({ type: 'saving' });
    const work = Promise.resolve().then(async () => {
      try {
        const next = await options.persist(taskId, value.text, value.baseRevision);
        if (options.current().taskId !== taskId) return false;
        options.update({ type: 'saved', document: next.document });
        options.accept(next);
        return isCurrentSaved(taskId);
      } catch (error) {
        if (options.current().taskId === taskId) {
          const conflict = options.isConflict(error);
          options.update({ type: 'failed', error: options.describeError(error), conflict });
          if (conflict) {
            try {
              const next = await options.reload(taskId);
              if (options.current().taskId === taskId) { options.update({ type: 'server', document: next.document }); options.accept(next); }
            } catch { /* Keep the local input and original conflict visible. */ }
          }
        }
        return false;
      } finally { pending = null; }
    });
    pending = work;
    return work;
  };
  return { save, isCurrentSaved };
}
