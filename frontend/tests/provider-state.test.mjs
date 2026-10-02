import assert from 'node:assert/strict';
import test from 'node:test';
import { configuredProviderFor, hasSavedDeepSeekKey, inferenceLocation, isLoopbackEndpoint, modelEnabled, providerDraftPayload, providerDraftsFrom, providerFor } from '../src/providerState.ts';

const provider = { type: 'openai', baseUrl: 'https://api.example.test/v1', model: 'test-model', hasKey: true };
const settings = { mode: 'llm', provider, capabilities: { modelReady: true } };

test('evidence mode disables inference even with a saved ready model or a stale task readiness flag', () => {
  assert.equal(modelEnabled({ ...settings, mode: 'evidence' }, true), false);
  assert.equal(inferenceLocation({ ...settings, mode: 'evidence' }), '本地资料核验 · 不调用模型');
});

test('task readiness has precedence over settings within enabled model mode', () => {
  assert.equal(modelEnabled(settings, false), false);
  assert.equal(modelEnabled(settings), true);
  assert.equal(modelEnabled(null), false);
});

test('loopback labels require an exact hostname, including IPv6', () => {
  for (const value of ['http://localhost:11434/v1', 'http://127.0.0.1:1234/v1', 'http://[::1]:8000']) assert.equal(isLoopbackEndpoint(value), true);
  for (const value of ['https://localhost.example.com', 'https://127.0.0.1.attacker.test', 'invalid']) assert.equal(isLoopbackEndpoint(value), false);
});

test('a remote judge makes a loopback main configuration disclose remote inference', () => {
  const local = { ...provider, baseUrl: 'http://127.0.0.1:11434/v1' };
  assert.equal(inferenceLocation({ ...settings, provider: local }), '本地编排 · 回环模型端点');
  assert.equal(inferenceLocation({ ...settings, providers: { main: local, judge: provider } }), '本地编排 + 远程模型推理');
});

test('legacy main configuration never silently populates independent roles', () => {
  assert.equal(providerFor(settings, 'main'), provider);
  assert.equal(providerFor(settings, 'judge').model, '');
  assert.equal(providerFor(settings, 'redteam').hasKey, false);
});

test('only a saved official OpenAI connection offers credential reuse', () => {
  for (const baseUrl of ['https://api.deepseek.com', 'https://api.deepseek.com/v1/', 'https://api.deepseek.com:443/']) {
    assert.equal(hasSavedDeepSeekKey({ ...settings, provider: { ...provider, baseUrl } }), true);
  }
  for (const baseUrl of ['https://api.example.com/v1', 'https://api.deepseek.com:8443', 'https://api.deepseek.com/proxy', 'https://api.deepseek.com/?key=x', 'https://user@api.deepseek.com', 'invalid']) {
    assert.equal(hasSavedDeepSeekKey({ ...settings, provider: { ...provider, baseUrl } }), false);
  }
  assert.equal(hasSavedDeepSeekKey({ ...settings, provider: { ...provider, baseUrl: 'https://api.deepseek.com', type: 'anthropic' } }), false);
  assert.equal(hasSavedDeepSeekKey(null), false);
});

test('advanced drafts edit saved independent connections rather than effective shared ones', () => {
  const independentJudge = { ...provider, model: 'judge-model', baseUrl: 'https://judge.example.com/v1' };
  const shared = { ...settings, providerRouting: 'shared_main', providers: { main: provider, judge: { ...provider, sharedWithMain: true }, redteam: { ...provider, sharedWithMain: true } }, providerConfigurations: { main: provider, judge: independentJudge, redteam: { type: 'openai', baseUrl: '', model: '', hasKey: false } } };
  assert.equal(configuredProviderFor(shared, 'judge'), independentJudge);
  const drafts = providerDraftsFrom(shared);
  assert.equal(drafts.judge.model, 'judge-model');
  assert.equal(drafts.redteam.model, '');
  drafts.main.apiKey = 'new-key';
  const payload = providerDraftPayload(shared, drafts, [], 'shared_main');
  assert.deepEqual(Object.keys(payload), ['main']);
  assert.equal(payload.main.apiKey, 'new-key');
  const separate = providerDraftPayload(shared, drafts, [], 'per_role');
  assert.equal(separate.judge.model, 'judge-model');
  assert.equal(separate.judge.apiKey, undefined);
  assert.equal(separate.redteam, undefined);
});

test('saving inherited roles never materializes a main connection into secondary slots', () => {
  const inherited = { ...settings, providers: { main: provider, judge: { ...provider, sharedWithMain: true }, redteam: { ...provider, sharedWithMain: true } } };
  const drafts = providerDraftsFrom(inherited);
  assert.equal(drafts.judge.baseUrl, '');
  assert.deepEqual(Object.keys(providerDraftPayload(inherited, drafts, [], 'per_role')), ['main']);
});

test('endpoint changes clear old keys and independent resets remain scoped to their role', () => {
  const drafts = providerDraftsFrom(settings);
  drafts.main.baseUrl = 'https://new.example.com/v1';
  assert.equal(providerDraftPayload(settings, drafts, [], 'per_role').main.clearKey, true);
  drafts.main.apiKey = 'new-key';
  const payload = providerDraftPayload(settings, drafts, ['judge'], 'per_role');
  assert.equal(payload.main.apiKeyEnv, '');
  assert.equal(payload.main.clearKey, undefined);
  assert.equal(payload.judge, null);
  assert.equal(providerDraftPayload(settings, drafts, ['judge'], 'shared_main').judge, undefined);
});
