import { useLayoutEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { BlurText } from '../BlurReveal';
import { statusLabels } from './state';

const agentPortraits = [
  { name: '目白麦昆', src: new URL('../assets/avatars/mejiro-mcqueen.png', import.meta.url).href, background: '#f0edfb' },
  { name: '爱丽速子', src: new URL('../assets/avatars/agnes-tachyon.png', import.meta.url).href, background: '#fff3e7' },
  { name: '米浴', src: new URL('../assets/avatars/rice-shower.png', import.meta.url).href, background: '#eef2fc' },
];

const paths: Record<string, string> = {
  more: 'M5 12h.01M12 12h.01M19 12h.01', attach: 'm8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2', edit: 'm4 16 12-12 4 4-12 12H4v-4ZM14 6l4 4', target: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3M22 12h-3M12 22v-3M2 12h3M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  plus: 'M12 5v14M5 12h14', arrow: 'M7 17 17 7M7 7h10v10', send: 'M12 20V4m-7 7 7-7 7 7', panel: 'M3 4h18v16H3V4Zm11 0v16', copy: 'M9 9h12v12H9zM15 5V3H3v12h2',
  menu: 'M4 6h16M4 12h16M4 18h16', close: 'm6 6 12 12M6 18 18 6',
  search: 'm21 21-4.5-4.5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  board: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  book: 'M4 3h14a2 2 0 0 1 2 2v16H6a2 2 0 0 1-2-2V3Zm0 14h16M8 7h8M8 11h6',
  chat: 'M21 11a8 8 0 0 1-8 8H7l-5 3 2-6a8 8 0 1 1 17-5ZM8 10h8M8 14h5',
  layers: 'm12 3 10 6-10 6L2 9l10-6ZM2 13l10 6 10-6M2 17l10 6 10-6',
  check: 'm5 12 4 4L19 6', pause: 'M8 5v14M16 5v14', play: 'm8 4 12 8-12 8V4Z',
  settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Zm6 9a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  lab: 'M9 3h6M10 3v6l-7 11h18L14 9V3M7 15h10',
  link: 'm10 13 4-4M8 15l-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 5a3 3 0 0 0 4 0l5-5a3 3 0 0 0-4-4l-2 2',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  clock: 'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  retry: 'M3 11a9 9 0 1 1 2 7M3 4v7h7',
  chevron: 'm9 5 7 7-7 7', back: 'm15 5-7 7 7 7', folder: 'M3 5h7l2 3h9v12H3V5Z',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
};
export function Icon({ name, className = '' }: { name: string; className?: string }) { return <svg className={`sw-icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name] || paths.book} /></svg>; }
export function Button({ children, icon, variant = 'quiet', className = '', busy, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon?: string; variant?: 'quiet' | 'primary' | 'outline' | 'danger'; busy?: boolean }) {
  return <button {...props} disabled={props.disabled || busy} className={`sw-button sw-button-${variant} ${className}`}>{busy ? <span className="sw-spinner" /> : icon ? <Icon name={icon} /> : null}{children}</button>;
}
export function Badge({ status, text }: { status: string; text?: string }) { return <span className={`sw-badge sw-status-${status}`}><i /><BlurText kind="status" text={text || statusLabels[status] || status} /></span>; }
export function Empty({ icon = 'layers', title, children }: { icon?: string; title: string; children?: ReactNode }) { return <div className="sw-empty"><span className="sw-empty-icon"><Icon name={icon} /></span><h3>{title}</h3><div>{children}</div></div>; }
export function ErrorNote({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) { return <div className="sw-error" role="alert"><span>{children}</span>{onRetry && <Button icon="retry" onClick={onRetry}>重试</Button>}</div>; }
export function Avatar({ index = 0, small = false }: { index?: number; small?: boolean }) {
  const portrait = agentPortraits[index % agentPortraits.length] || agentPortraits[0];
  // Frame the face from the unchanged, locally bundled official character portrait.
  return <svg className={`sw-avatar ${small ? 'small' : ''}`} viewBox="30 30 236 236" aria-hidden="true" focusable="false" style={{ overflow: 'hidden' }}><title>{portrait.name} · ウマ娘 プリティーダービー · © Cygames, Inc.</title><rect x="30" y="30" width="236" height="236" fill={portrait.background} /><image href={portrait.src} width="296" height="389" /></svg>;
}
export function Composer({ value, onChange, onSend, busy, centered = false, onAttach, onHistory, context, controls, placeholder, disabled = false }: { value: string; onChange: (value: string) => void; onSend: () => void; busy: boolean; centered?: boolean; onAttach?: () => void; onHistory?: () => void; context?: ReactNode; controls?: ReactNode; placeholder?: string; disabled?: boolean }) {
  const composing = useRef(false); const input = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => { const element = input.current; if (!element) return; element.style.height = 'auto'; element.style.height = Math.min(180, Math.max(28, Math.ceil(element.scrollHeight) + 2)) + 'px'; }, [value]);
  return <form className={`sw-composer ${centered ? 'centered' : ''}`} onSubmit={event => { event.preventDefault(); if (!composing.current && !busy && !disabled && value.trim()) onSend(); }}>
    {context && <div className="sw-composer-context">{context}</div>}<textarea ref={input} aria-label="与研究助手对话" placeholder={placeholder || (centered ? '你想研究什么？' : '继续讨论…')} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && event.nativeEvent.keyCode !== 229 && !event.nativeEvent.isComposing && !composing.current) { event.preventDefault(); if (!busy && !disabled && value.trim()) onSend(); } }} rows={1} />
    <div className="sw-composer-footer"><div>{onAttach && <Button type="button" icon="plus" aria-label="添加研究材料" onClick={onAttach} />}{onHistory && <Button type="button" icon="chat" aria-label="查看完整对话" onClick={onHistory} />}{controls}</div><Button type="submit" variant="primary" icon="send" busy={busy} disabled={disabled || !value.trim()} aria-label="发送消息" /></div>
  </form>;
}
