import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
import { renderToStaticMarkup } from 'react-dom/server';
import React from 'react';
import { pendingTopic, topicSelectionRequest } from '../src/workbench/topicSelectionState.ts';

test('candidate, custom and edited selections carry the displayed revision and original input', () => {
  assert.deepEqual(topicSelectionRequest(7, 'candidate', 'T2'), { expectedRevision: 7, mode: 'candidate', candidateId: 'T2' });
  assert.deepEqual(topicSelectionRequest(8, 'custom', ' 新课题\n边界 '), { expectedRevision: 8, mode: 'custom', customText: ' 新课题\n边界 ' });
  assert.deepEqual(topicSelectionRequest(9, 'edited', '修改的问题', 'T3'), { expectedRevision: 9, mode: 'edited', customText: '修改的问题', baseCandidateId: 'T3' });
  assert.throws(() => topicSelectionRequest(1, 'custom', '  '));
  assert.throws(() => topicSelectionRequest(1, 'edited', 'x'));
});

test('only a pending topic selection exposes the selection form', () => {
  assert.equal(pendingTopic(undefined), false);
  assert.equal(pendingTopic({ topicSelection: { status: 'pending' } }), true);
  assert.equal(pendingTopic({ topicSelection: { status: 'selected' } }), false);
});

test('candidate panel renders questions, gaps, study plans and sources, and disappears after selection', async () => {
  const compiled = await build({ entryPoints: ['frontend/src/workbench/TopicSelectionPanel.tsx'], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external', jsx: 'automatic', loader: { '.css': 'empty' } });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', compiled.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports);
  const Panel = module.exports.TopicSelectionPanel;
  const candidates = [1, 2, 3].map(i => ({ id: 'T'+i, title: '候选'+i, question: '问题'+i, researchGap: '缺口'+i, rationale: '正反依据'+i, evidenceIds: ['a', 'b'], minimalStudy: '最小方案'+i, feasibility: '可行性'+i, limitations: '摘要证据'+i }));
  const props = { cycle: { topicCandidates: candidates, topicSelection: { id: 'selection', status: 'pending' } }, revision: 8, busy: false, onSelect() {} };
  const html = renderToStaticMarkup(React.createElement(Panel, props));
  for (const text of ['候选1', '候选3', '问题2', '缺口2', '最小方案3', '可行性1', '2 条证据', '摘要证据1', '选择此课题', '编辑候选', '使用自定义课题']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('先验证现有方案与边界'));
  const selected = renderToStaticMarkup(React.createElement(Panel, { ...props, cycle: { ...props.cycle, topicSelection: { id: 'selection', status: 'selected' } } }));
  assert.equal(selected, '');
});
