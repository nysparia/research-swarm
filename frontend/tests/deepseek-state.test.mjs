import assert from 'node:assert/strict';
import test from 'node:test';
import { deepseekSelection, emptyDeepSeekSelection } from '../src/deepseekState.ts';

const models = [{ id: 'deepseek-flash' }, { id: 'deepseek-v4-pro' }];
const loading = deepseekSelection(emptyDeepSeekSelection, { type: 'loading', requestId: 1 });

test('fetched official models never select a default model', () => {
  const fetched = deepseekSelection(loading, { type: 'loaded', requestId: 1, models });
  assert.equal(fetched.model, '');
  assert.equal(fetched.loading, false);
  assert.equal(deepseekSelection(fetched, { type: 'select', model: 'deepseek-v4-pro' }).model, 'deepseek-v4-pro');
  assert.equal(deepseekSelection(fetched, { type: 'select', model: 'custom-model' }).model, '');
});

test('changing the key clears options, selection, errors and invalidates late responses', () => {
  const fetched = deepseekSelection(loading, { type: 'loaded', requestId: 1, models });
  const selected = deepseekSelection(fetched, { type: 'select', model: 'deepseek-flash' });
  const changed = deepseekSelection(selected, { type: 'key', key: 'different-key', requestId: 2 });
  assert.deepEqual(changed.models, []);
  assert.equal(changed.model, '');
  assert.equal(changed.key, 'different-key');
  assert.equal(changed.error, '');
  assert.equal(changed.fetched, false);
  assert.equal(deepseekSelection(changed, { type: 'loaded', requestId: 1, models }), changed);
  assert.equal(deepseekSelection(changed, { type: 'failed', requestId: 1, error: 'stale failure' }), changed);
});

test('reopening and refreshing require another manual choice', () => {
  const fetched = deepseekSelection(loading, { type: 'loaded', requestId: 1, models });
  const selected = deepseekSelection(fetched, { type: 'select', model: 'deepseek-flash' });
  const refreshed = deepseekSelection(selected, { type: 'loading', requestId: 2 });
  assert.equal(refreshed.model, '');
  assert.equal(deepseekSelection(refreshed, { type: 'loaded', requestId: 1, models }), refreshed);
  const reset = deepseekSelection(selected, { type: 'reset', requestId: 3 });
  assert.equal(reset.model, '');
  assert.equal(reset.key, '');
  assert.equal(deepseekSelection(reset, { type: 'loaded', requestId: 1, models }), reset);
});

test('empty model lists and failures retain the key and support retry', () => {
  const state = deepseekSelection(emptyDeepSeekSelection, { type: 'key', key: 'test-key', requestId: 1 });
  const pending = deepseekSelection(state, { type: 'loading', requestId: 2 });
  const empty = deepseekSelection(pending, { type: 'loaded', requestId: 2, models: [] });
  assert.equal(empty.fetched, true);
  assert.equal(empty.model, '');
  const failed = deepseekSelection(pending, { type: 'failed', requestId: 2, error: 'authentication failed' });
  assert.equal(failed.key, 'test-key');
  assert.equal(failed.loading, false);
  assert.equal(failed.error, 'authentication failed');
  assert.equal(deepseekSelection(failed, { type: 'loading', requestId: 3 }).error, '');
});
