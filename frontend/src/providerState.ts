import type { ProviderRole, ProviderSettings, Settings } from './types';

export const providerRoleLabels: Record<ProviderRole, string> = { main: '主模型', judge: '裁判', redteam: '红队' };
export const providerRoles: ProviderRole[] = ['main', 'judge', 'redteam'];
export const emptyProvider: ProviderSettings = { type: 'openai', baseUrl: '', model: '', hasKey: false };

export function providerFor(settings: Settings | null, role: ProviderRole): ProviderSettings {
  return settings?.providers?.[role] || (role === 'main' ? settings?.provider : undefined) || emptyProvider;
}

export function configuredProviderFor(settings: Settings | null, role: ProviderRole): ProviderSettings {
  return settings?.providerConfigurations?.[role] || (providerFor(settings, role).sharedWithMain ? emptyProvider : providerFor(settings, role));
}

export function hasSavedDeepSeekKey(settings: Settings | null): boolean {
  const provider = configuredProviderFor(settings, 'main');
  try {
    const url = new URL(provider.baseUrl);
    return provider.hasKey && provider.type === 'openai' && url.protocol === 'https:' && url.hostname === 'api.deepseek.com'
      && (!url.port || url.port === '443') && ['', '/v1'].includes(url.pathname.replace(/\/+$/, ''))
      && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

export interface ProviderDraft { type: string; baseUrl: string; model: string; apiKey: string }
export function providerDraftsFrom(settings: Settings | null): Record<ProviderRole, ProviderDraft> {
  return Object.fromEntries(providerRoles.map(role => {
    const { type, baseUrl, model } = configuredProviderFor(settings, role);
    return [role, { type, baseUrl, model, apiKey: '' }];
  })) as Record<ProviderRole, ProviderDraft>;
}

export function providerDraftPayload(settings: Settings | null, drafts: Record<ProviderRole, ProviderDraft>, cleared: ProviderRole[], routing: 'shared_main' | 'per_role') {
  return Object.fromEntries(providerRoles.filter(role => routing !== 'shared_main' || role === 'main').filter(role =>
    cleared.includes(role) || drafts[role].baseUrl.trim() || drafts[role].model.trim() || drafts[role].apiKey.trim()
  ).map(role => {
    if (cleared.includes(role)) return [role, null];
    const { type, baseUrl, model, apiKey } = drafts[role];
    const endpointChanged = baseUrl.trim().replace(/\/+$/, '') !== configuredProviderFor(settings, role).baseUrl.replace(/\/+$/, '');
    return [role, { type, baseUrl: baseUrl.trim(), model: model.trim(), ...(apiKey.trim()
      ? { apiKey: apiKey.trim(), ...(endpointChanged ? { apiKeyEnv: '' } : {}) }
      : endpointChanged ? { clearKey: true } : {}) }];
  }));
}

export function isLoopbackEndpoint(baseUrl: string): boolean {
  try { return ['127.0.0.1', 'localhost', '[::1]', '::1'].includes(new URL(baseUrl).hostname.toLowerCase()); }
  catch { return false; }
}

export function inferenceLocation(settings: Settings | null): string {
  if (!settings) return '正在读取运行配置';
  if (settings.mode === 'evidence') return '本地资料核验 · 不调用模型';
  const configured = providerRoles.map(role => providerFor(settings, role)).filter(provider => provider.baseUrl && provider.model);
  if (!configured.length) return '本地编排 · 模型尚未配置';
  return configured.every(provider => isLoopbackEndpoint(provider.baseUrl))
    ? '本地编排 · 回环模型端点'
    : '本地编排 + 远程模型推理';
}

export function modelEnabled(settings: Settings | null, taskReady?: boolean): boolean {
  if (settings?.mode === 'evidence') return false;
  return taskReady ?? settings?.capabilities.modelReady === true;
}
