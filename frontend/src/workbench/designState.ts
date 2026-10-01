import type { DraftBlock } from './types';
import type { ResearchNode } from '../types';

export function draftOverview(blocks: DraftBlock[], fallback: string) {
  const nonempty = blocks.filter(b => b.content.replace(/^#{1,6}[^\n]*\n?/gm, '').trim());
  const groups = [
    { title: '目标', icon: 'target', patterns: [/^目标$|研究问题/, /目的|研究目标/, /已知需求/, /背景/] },
    { title: '边界', icon: 'book', patterns: [/边界|约束/, /资源/] },
    { title: '验收', icon: 'check', patterns: [/验收/, /指标|交付/] },
  ];
  return groups.map(group => {
    const matches = group.patterns.map(pattern => nonempty.filter(b => pattern.test(b.title) || pattern.test(b.content.match(/^#{1,6}\s+([^\n]+)/m)?.[1] || ''))).find(found => found.length) || [];
    return { title: group.title, icon: group.icon, blockIds: matches.map(b => b.id), content: matches.map(b => b.content.replace(/^#{1,6}[^\n]*\n?/gm, '').trim()).join('\n\n') || (group.title === '目标' ? fallback : '') };
  });
}
export function representativeAgents(nodes: ResearchNode[]) {
  const category = (n: ResearchNode) => /实验|experiment|execute|reproduc/i.test(n.role + ' ' + n.title) ? '实验' : /文献|论文|检索|paper|literature|evidence/i.test(n.role + ' ' + n.title) ? '文献' : '理论';
  const current = nodes.filter(n => n.active).sort((a,b) => Number(b.status === 'running') - Number(a.status === 'running') || String(b.finishedAt || b.startedAt || '').localeCompare(String(a.finishedAt || a.startedAt || '')));
  return ['理论','实验','文献'].flatMap(role => { const node = current.find(n => category(n) === role); return node ? [{node,role}] : []; });
}
