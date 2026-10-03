import { useId, useRef, type CSSProperties } from 'react';
import { Icon } from './ui';

/** Hand-drawn web equivalents of the public macOS UI Kit's controls.
 * Geometry comes from the visible Light / Content Area / Regular references.
 * Frosted CSS materials replace native Liquid Glass; no Apple assets embedded.
 */
export function MacSegmentedControl<T extends string>({ value, onChange, options, label, material = 'content' }: {
  value: T; onChange: (value: T) => void;
  options: readonly { value: T; label: string; icon?: string }[];
  label: string; material?: 'content' | 'glass';
}) {
  const root = useRef<HTMLDivElement>(null);
  const index = Math.max(0, options.findIndex(option => option.value === value));
  return <div ref={root} className={`sw-native-segments sw-kit-segments ${material === 'glass' ? 'sw-glass-segments' : ''}`} role="group" aria-label={label} data-view={value} style={{ '--sw-segment-index': index, '--sw-segment-count': options.length } as CSSProperties}>
    <span className="sw-segment-thumb" aria-hidden="true" />
    {options.map((option, i) => <button key={option.value} type="button" aria-pressed={option.value === value} onClick={() => onChange(option.value)} onKeyDown={event => {
      const next = event.key === 'ArrowRight' ? (i + 1) % options.length : event.key === 'ArrowLeft' ? (i - 1 + options.length) % options.length : event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : null;
      if (next !== null) { event.preventDefault(); onChange(options[next].value); root.current?.querySelectorAll('button')[next]?.focus(); }
    }}>{option.icon && <Icon name={option.icon} />}<span>{option.label}</span></button>)}
  </div>;
}

export function MacSwitch({ checked, onChange, label, description }: {
  checked: boolean; onChange: (value: boolean) => void; label: string; description?: string;
}) {
  const id = useId();
  return <label className="sw-transparency-toggle sw-kit-switch-row"><span>{label}{description && <small id={id}>{description}</small>}</span><input className="sw-kit-switch" type="checkbox" role="switch" aria-label={label} aria-describedby={description ? id : undefined} checked={checked} onChange={event => onChange(event.target.checked)} /></label>;
}
