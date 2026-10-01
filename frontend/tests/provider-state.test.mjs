import assert from 'node:assert/strict';
import test from 'node:test';
import { inferenceLocation, isLoopbackEndpoint, modelEnabled, providerFor } from '../src/providerState.ts';

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
