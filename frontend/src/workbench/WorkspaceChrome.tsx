import type { ReactNode } from 'react';
import { Icon } from './ui';
import { MacSegmentedControl } from './NativeControls';

/** A product mark, not an imitation of macOS window controls. */
export function SwarmMark({ large = false }: { large?: boolean }) {
  return <span className={`sw-mark ${large ? 'sw-mark-large' : ''}`} aria-hidden="true"><svg viewBox="0 0 48 48" fill="none"><path d="M24 9 37 16.5v15L24 39l-13-7.5v-15L24 9Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="m24 9 0 15m13-7.5L24 24 11 16.5M24 24v15m0-15 13 7.5M24 24l-13 7.5" stroke="currentColor" strokeWidth="1.5" /><circle cx="24" cy="24" r="4" fill="currentColor" /><circle cx="24" cy="9" r="2.5" fill="currentColor" /><circle cx="11" cy="31.5" r="2.5" fill="currentColor" /><circle cx="37" cy="31.5" r="2.5" fill="currentColor" /></svg></span>;
}

const suggestions = [
  { icon: 'search', label: '探索一个问题', prompt: '我想研究的问题是：\n\n请先帮我明确研究范围、已有证据和可验证的目标。' },
  { icon: 'book', label: '梳理相关文献', prompt: '我想梳理这个方向的研究文献：\n\n请区分已有共识、争议与值得继续验证的问题。' },
  { icon: 'lab', label: '复现一篇论文', prompt: '我想复现这篇论文：\n\n请先帮我确认数据、实验环境和评价指标。' },
];

export function EntrySurface({ children, onSuggestion, modelReady, onSettings }: { children: ReactNode; onSuggestion: (value: string) => void; modelReady: boolean; onSettings: () => void }) {
  return <section className="sw-entry" aria-label="新建研究">
    <div className="sw-entry-welcome"><SwarmMark large /><h1>你想研究什么？</h1></div>
    <div className="sw-entry-dock">
      <div className="sw-entry-input-meta"><span><Icon name="folder" />本机工作区</span><button type="button" onClick={onSettings}><Icon name="settings" />{modelReady ? '研究助手已配置' : '连接研究助手'}<Icon name="chevron" /></button></div>
      <div className="sw-entry-input">{children}</div>
      <div className="sw-entry-dock-footer"><div className="sw-suggestions" aria-label="研究起点">{suggestions.map(item => <button key={item.label} type="button" onClick={() => onSuggestion(item.prompt)}><Icon name={item.icon} />{item.label}</button>)}</div><span className="sw-keyboard-hint">Enter 发送 · Shift + Enter 换行</span></div>
      <p className="sw-entry-footnote">研究记录保存在本机，推理请求发送至你配置的模型服务。</p>
    </div>
  </section>;
}

export function WorkspaceTabs({ value, onChange }: { value: 'structure' | 'outputs'; onChange: (value: 'structure' | 'outputs') => void }) {
  return <div className="sw-workspace-toolbar"><MacSegmentedControl value={value} onChange={onChange} label="工作区视图" material="glass" options={[{ value: 'structure', label: '研究过程', icon: 'layers' }, { value: 'outputs', label: '研究成果', icon: 'book' }]} /><span className="sw-workspace-hint">{value === 'structure' ? '选择节点，查看依据或继续讨论' : '报告、证据与实验材料'}</span></div>;
}
