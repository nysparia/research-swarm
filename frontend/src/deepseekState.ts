export interface DeepSeekModel { id: string }
export interface DeepSeekSelection {
  key: string;
  models: DeepSeekModel[];
  model: string;
  loading: boolean;
  fetched: boolean;
  error: string;
  requestId: number;
}
export const emptyDeepSeekSelection: DeepSeekSelection = { key: '', models: [], model: '', loading: false, fetched: false, error: '', requestId: 0 };
type Action =
  | { type: 'reset'; requestId: number }
  | { type: 'key'; key: string; requestId: number }
  | { type: 'loading'; requestId: number }
  | { type: 'loaded'; models: DeepSeekModel[]; requestId: number }
  | { type: 'failed'; error: string; requestId: number }
  | { type: 'select'; model: string };

export function deepseekSelection(state: DeepSeekSelection, action: Action): DeepSeekSelection {
  if (action.type === 'reset' || action.type === 'key') return { ...emptyDeepSeekSelection, requestId: action.requestId, key: action.type === 'key' ? action.key : '' };
  if (action.type === 'loading') return { ...state, models: [], model: '', fetched: false, error: '', loading: true, requestId: action.requestId };
  if (action.type === 'select') return { ...state, model: state.models.some(item => item.id === action.model) ? action.model : '' };
  if (action.requestId !== state.requestId) return state;
  if (action.type === 'loaded') return { ...state, models: action.models, model: '', loading: false, fetched: true };
  return { ...state, error: action.error, loading: false };
}
