import assert from 'node:assert/strict';
import test from 'node:test';
import { failureReason, researchAssessment } from '../src/researchStatus.ts';

test('restored failures explain the actual old log rather than a generic count', () => {
  assert.equal(failureReason({ logs: [{ level: 'error', message: '任务数量上限' }] }), '任务数量上限');
  assert.equal(failureReason({ error: { message: 'Python exit 1' }, logs: [] }), 'Python exit 1');
});
test('old reports distinguish unexecuted experiments and missing evidence from completion', () => {
  const result = researchAssessment({ report: { claims: [{ evidenceIds: [] }] }, nodes: [{ id: 'ex', active: true, kind: 'experiment', output: { structured: { status: 'missing_input' } } }], evidence: [] });
  assert.equal(result.incomplete, true);
  assert.equal(result.unexecuted, 1);
  assert.equal(result.unsupported, 1);
});
