import { useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { BlurText } from '../BlurReveal';
import { statusLabels } from './state';

const paths: Record<string, string> = {
  more: 'M5 12h.01M12 12h.01M19 12h.01', attach: 'm8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2', edit: 'm4 16 12-12 4 4-12 12H4v-4ZM14 6l4 4', target: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3M22 12h-3M12 22v-3M2 12h3M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  plus: 'M12 5v14M5 12h14', arrow: 'M7 17 17 7M7 7h10v10', send: 'm3 3 19 9-19 9 4-9-4-9Zm4 9h15',
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
  const colors = ['#dfece7', '#e2e9f1', '#f1e5d9', '#e9e3f2'];
  return <svg className={`sw-avatar ${small ? 'small' : ''}`} viewBox="0 0 48 48" aria-hidden="true"><rect width="48" height="48" rx="16" fill={colors[index % colors.length]} /><path d="M7 48c0-13 8-19 17-19s17 6 17 19" fill={['#415e57', '#475a72', '#82694f', '#6e5d7d'][index % 4]} /><path d="m18 31 6 7 6-7" fill="#fff" /><ellipse cx="24" cy="21" rx="10" ry="12" fill="#e8bb99" /><path d={index % 2 ? 'M13 22C10 4 37 1 35 23l-6-12-11 8-5 3Z' : 'M13 23C10 9 19 6 26 7c10 0 10 11 9 17l-4-10-15 3-3 6Z'} fill="#394044" /><path d="M20 22h1m6 0h1m-6 6h4" stroke="#705548" strokeWidth="1.7" strokeLinecap="round" />{index % 3 === 0 && <path d="M16 21h7v5h-7zm10 0h7v5h-7m-3 2h3" stroke="#536065" fill="none" />}</svg>;
}
export function Composer({ value, onChange, onSend, busy, centered = false, onAttach, onHistory }: { value: string; onChange: (value: string) => void; onSend: () => void; busy: boolean; centered?: boolean; onAttach?: () => void; onHistory?: () => void }) {
  const composing = useRef(false);
  return <form className={`sw-composer ${centered ? 'centered' : ''}`} onSubmit={event => { event.preventDefault(); if (!composing.current && !busy && value.trim()) onSend(); }}>
    {!centered && onAttach && <Button type="button" icon="attach" aria-label="添加研究材料" onClick={onAttach} />}<textarea aria-label="与研究助手对话" placeholder={centered ? '描述你想研究的问题，或想复现的论文…' : '随时补充想法，调整研究方向…'} value={value} onChange={event => onChange(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current) { event.preventDefault(); if (!busy && value.trim()) onSend(); } }} rows={centered ? 3 : 1} />
    <div className="sw-composer-footer">{!centered && onHistory && <Button type="button" icon="chat" aria-label="查看完整对话" onClick={onHistory} />}<span><Icon name="spark" />{centered ? '从问题出发，一起形成有证据的研究' : 'Enter 发送 · Shift + Enter 换行'}</span><Button type="submit" variant="primary" icon="send" busy={busy} disabled={!value.trim()} aria-label="发送消息" /></div>
  </form>;
}
