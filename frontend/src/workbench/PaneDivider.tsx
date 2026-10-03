import { useLayoutEffect, useRef, useState } from 'react';
import { clampPanePercent, DEFAULT_PANE_PERCENT, paneLimits, panePercentAt, panePercentForKey } from './splitPaneState';

/** One-point visible divider with an enlarged pointer target, keyboard support,
 * pointer capture and responsive limits. Does not remount either content pane. */
export function PaneDivider({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const element = useRef<HTMLDivElement>(null);
  const pointer = useRef<number | null>(null);
  const [width, setWidth] = useState(1000);
  useLayoutEffect(() => {
    const parent = element.current?.parentElement;
    if (!parent) return;
    const observer = new ResizeObserver(() => setWidth(parent.clientWidth));
    setWidth(parent.clientWidth); observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  const [min, max] = paneLimits(width);
  const stop = () => { pointer.current = null; element.current?.removeAttribute('data-dragging'); };
  return <div className="sw-pane-divider" ref={element} role="separator" aria-label="调整对话与工作区宽度" aria-orientation="vertical" aria-valuemin={Math.round(min)} aria-valuemax={Math.round(max)} aria-valuenow={Math.round(clampPanePercent(value, width))} aria-valuetext={`对话占 ${Math.round(clampPanePercent(value, width))}%`} tabIndex={0} title="拖动调整宽度；双击或按 Enter 恢复默认"
    onDoubleClick={() => onChange(DEFAULT_PANE_PERCENT)}
    onPointerDown={event => {
      if (event.button !== 0) return;
      event.preventDefault(); pointer.current = event.pointerId;
      event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId);
      event.currentTarget.setAttribute('data-dragging', 'true');
    }}
    onPointerMove={event => {
      if (pointer.current !== event.pointerId) return;
      const box = event.currentTarget.parentElement!.getBoundingClientRect();
      onChange(panePercentAt(event.clientX, box.left, box.width));
    }}
    onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); stop(); }}
    onPointerCancel={stop} onLostPointerCapture={stop}
    onKeyDown={event => {
      const next = panePercentForKey(value, event.key, width, event.shiftKey);
      if (next !== null) { event.preventDefault(); onChange(next); }
    }}><span aria-hidden="true" /></div>;
}
