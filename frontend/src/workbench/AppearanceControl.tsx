import { useState, type CSSProperties } from 'react';
import { Popover } from 'antd';
import { Button } from './ui';
import { GlassButton } from './GlassChrome';
import { MacSwitch } from './NativeControls';

/** Web material preference, calibrated against Apple's macOS UI kit preview.
 * Preferences are local to this open workspace; no settings API is mutated.
 * Ref: https://developer.apple.com/design/resources/#macos-apps
 */
export function AppearanceControl({ tint, onTint, reduced, onReduced }: {
  tint: number; onTint: (value: number) => void;
  reduced: boolean; onReduced: (value: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  return <Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomRight" classNames={{ root: 'sw-appearance-popover' }} content={
    <section className="sw-appearance" aria-label="外观设置" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.sw-appearance-trigger')?.focus()); } }}>
      <header><div><strong>外观</strong><p>让玻璃质感适合你的阅读习惯。</p></div><Button icon="close" aria-label="关闭外观设置" onClick={() => setOpen(false)} /></header>
      <div className="sw-glass-sample" data-solid={reduced} style={{ '--sample-alpha': .42 + tint * .0048 } as CSSProperties}><span>研究工作区</span></div>
      <label className="sw-tint-label" htmlFor="sw-glass-tint"><span>玻璃染色</span><output>{Math.round(tint)}%</output></label>
      <input id="sw-glass-tint" type="range" min="0" max="100" step="5" value={tint} disabled={reduced} onChange={event => onTint(Number(event.target.value))} aria-valuetext={`${tint}% 染色`} />
      <div className="sw-tint-endpoints"><span>通透</span><span>浓郁</span></div>
      <MacSwitch checked={reduced} onChange={onReduced} label="减少透明度" description="使用实色背景，保持内容清晰。" />
      <p className="sw-appearance-note">仅调整当前窗口的导航与控件，不改变文档内容。</p>
    </section>
  }><GlassButton className="sw-appearance-trigger" icon="appearance" aria-label="调整外观" aria-expanded={open} title="调整外观" /></Popover>;
}
