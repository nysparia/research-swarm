import assert from 'node:assert/strict';
import test from 'node:test';
import { conceptProgress, conceptSourceLabel, hasPendingConceptFacts, needsConceptClarification } from '../src/conceptState.ts';
test('core blockers prevent start even if presentation status changes', () => {
  assert.equal(needsConceptClarification({}), false);
  for (const status of ['needs_clarification', 'checking', 'drafting', 'ready', 'interrupted', 'failed']) {
    assert.equal(needsConceptClarification({ conceptUnderstanding: { status, blockers: [{ term: 'JEV', question: 'What does it mean?' }] } }), true);
  }
  assert.equal(needsConceptClarification({ conceptUnderstanding: { status: 'ready', blockers: [], unresolved: [{ term: 'optional' }] } }), false);
});
test('progress describes concept lookup separately from drafting', () => {
  const document = status => ({ polishing: true, conceptUnderstanding: { status, blockers: [] } });
  assert.equal(conceptProgress(document('checking')), '正在理解问题');
  assert.equal(conceptProgress(document('searching')), '正在查询术语含义');
  assert.equal(conceptProgress(document('drafting')), '正在整理需求');
  assert.equal(conceptProgress({ polishing: true }), '正在整理需求');
  assert.equal(conceptProgress({ polishing: false, conceptUnderstanding: { status: 'needs_clarification', blockers: [{}] } }), '请先明确核心研究对象');
});

test('interrupted lookup shows its failure before provisional core questions', () => {
  const document = status => ({ polishing: false, conceptUnderstanding: { status, blockers: [{ term: 'JEV', question: 'What does it mean?' }] } });
  assert.equal(conceptProgress(document('failed')), '概念理解未完成');
  assert.equal(conceptProgress(document('interrupted')), '概念理解已中断，请在对话中重试');
  assert.equal(needsConceptClarification(document('failed')), true);
  assert.equal(needsConceptClarification(document('interrupted')), true);
});

test('confirmed objects with pending official facts can start research', () => {
  const document = { polishing: false, conceptUnderstanding: { status: 'ready', blockers: [],
    unresolved: [{ term: 'dots', question: 'Official facts are pending.', identityStatus: 'confirmed', core: false }] } };
  assert.equal(needsConceptClarification(document), false);
  assert.equal(hasPendingConceptFacts(document), true);
  assert.equal(conceptProgress(document), '官方资料待核实，需求已整理');
  assert.equal(conceptSourceLabel('official'), '官方来源');
  assert.equal(conceptSourceLabel('third_party'), '第三方来源');
  assert.equal(conceptSourceLabel(), '来源类型未标注');
});
