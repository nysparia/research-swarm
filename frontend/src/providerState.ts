import type { ProviderRole, ProviderSettings, Settings } from './types';

export const providerRoleLabels: Record<ProviderRole, string> = { main: '主模型', judge: '裁判', redteam: '红队' };
export const providerRoles: ProviderRole[] = ['main', 'judge', 'redteam'];
export const emptyProvider: ProviderSettings = { type: 'openai', baseUrl: '', model: '', hasKey: false };

export function providerFor(settings: Settings | null, role: ProviderRole): ProviderSettings {
  return settings?.providers?.[role] || (role === 'main' ? settings?.provider : undefined) || emptyProvider;
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
