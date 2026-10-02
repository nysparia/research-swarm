import assert from 'node:assert/strict';
import test from 'node:test';
import { conceptProgress, needsConceptClarification } from '../src/conceptState.ts';
test('core blockers prevent start even if presentation status changes', () => {
  assert.equal(needsConceptClarification({}), false);
  for (const status of ['needs_clarification', 'checking', 'drafting', 'ready', 'interrupted']) {
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
