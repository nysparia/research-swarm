import type { CanonicalClaim } from './types';

export const responsibilityStatement = '我理解确认仅记录本人对当前判断的责任承认，不代表系统已证明科学正确性，也不构成阅读证明。';

export function responsibilityPayload(acknowledged: boolean, name: string) {
  const signature = name.trim();
  if (!acknowledged || !signature || signature.length > 120) throw new Error('请勾选责任说明并填写 1–120 字的签名后确认。');
  return { responsibilityAcknowledged: true, responsibilityName: signature };
}

export function claimReviewLabel(claim?: CanonicalClaim): string {
  if (claim?.assessment.review?.independent === true && claim.assessment.review.status === 'completed' && claim.assessment.review.role === 'judge') return '已完成独立裁判复核（仍需证据核验）';
  return '模型立场（未独立复核）';
}

export function claimConfirmationLabel(claim: CanonicalClaim): string {
  const signed = claim.decisions?.some(decision => decision.version === claim.version && decision.decision === 'confirm' && decision.responsibilityAcknowledged === true && Boolean(decision.responsibilityName?.trim()));
  return signed ? '用户已签名承担判断责任' : '用户已确认（历史记录无责任签名）';
}
