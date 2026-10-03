import { forwardRef, type ComponentProps, type ReactNode } from 'react';
import { Button, Icon } from './ui';

/** One shared chrome family for navigation, output actions and model settings. */
export const GlassButton = forwardRef<HTMLButtonElement, ComponentProps<typeof Button>>(function GlassButton({ className = '', children, ...props }, ref) {
  return <Button ref={ref} {...props} className={`sw-glass-action ${children ? '' : 'is-icon'} ${className}`}>{children}</Button>;
});
export function GlassGroup({ children, label, className = '' }: { children: ReactNode; label: string; className?: string }) {
  return <div className={`sw-glass-group ${className}`} role="group" aria-label={label}>{children}</div>;
}
export function GlassSymbol({ name }: { name: string }) { return <span className="sw-glass-symbol" aria-hidden="true"><Icon name={name} /></span>; }

/** Original background artwork: visible behind the frosted chrome, never in
 * document content. No Apple wallpaper or other proprietary asset is embedded. */
export function WindowBackdrop() {
  return <div className="sw-window-backdrop" aria-hidden="true"><i /><i /></div>;
}
