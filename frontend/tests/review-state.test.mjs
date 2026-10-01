import assert from 'node:assert/strict';
import test from 'node:test';
import { claimConfirmationLabel, claimReviewLabel, responsibilityPayload } from '../src/reviewState.ts';

test('confirmation requires both explicit responsibility acknowledgement and a nonempty signature', () => {
  assert.throws(() => responsibilityPayload(false, 'Reviewer'));
  assert.throws(() => responsibilityPayload(true, '  '));
  assert.throws(() => responsibilityPayload(true, 'a'.repeat(121)));
  assert.deepEqual(responsibilityPayload(true, ' Reviewer '), { responsibilityAcknowledged: true, responsibilityName: 'Reviewer' });
});

test('historical confirmations are never relabeled as signed responsibility', () => {
  const claim = { version: 2, assessment: { confirmedByUser: true }, decisions: [{ version: 1, decision: 'confirm', responsibilityAcknowledged: true, responsibilityName: 'Old reviewer' }] };
  assert.match(claimConfirmationLabel(claim), /历史记录无责任签名/);
  claim.decisions.push({ version: 2, decision: 'confirm', responsibilityAcknowledged: true, responsibilityName: 'Current reviewer' });
  assert.equal(claimConfirmationLabel(claim), '用户已签名承担判断责任');
});

test('missing independent-review metadata cannot be promoted by a supported status or user signature', () => {
  assert.equal(claimReviewLabel(), '模型立场（未独立复核）');
  assert.equal(claimReviewLabel({ assessment: { status: 'supported', confirmedByUser: true } }), '模型立场（未独立复核）');
});

test('only completed independent review receives the reviewed provenance label', () => {
  for (const review of [{ independent: true, status: 'failed' }, { independent: false, status: 'completed' }, { independent: true }, { independent: true, status: 'completed', role: 'main' }]) assert.equal(claimReviewLabel({ assessment: { review } }), '模型立场（未独立复核）');
  assert.match(claimReviewLabel({ assessment: { review: { independent: true, status: 'completed', role: 'judge' } } }), /已完成独立裁判复核/);
});
