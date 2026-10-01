import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Research material is Markdown data, never executable HTML. */
export const Markdown = memo(function Markdown({ text, className = '' }: { text: string; className?: string }) {
  return <div className={`markdown-content ${className}`} data-motion-skip="true"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    a: ({ node: _node, children, ...props }) => <a {...props} target={props.href?.startsWith('#') ? undefined : '_blank'} rel="noreferrer">{children}</a>,
    table: ({ node: _node, children, ...props }) => <div className="markdown-table"><table {...props}>{children}</table></div>,
    img: ({ src, alt }) => typeof src === 'string' && src.startsWith('/api/') ? <img src={src} alt={alt || '研究图像'} loading="lazy" /> : <a href={typeof src === 'string' ? src : undefined} target="_blank" rel="noreferrer">{alt || '查看图像'}</a>,
  }}>{text}</ReactMarkdown></div>;
});
