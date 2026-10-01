import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Markdown } from '../Markdown';
import type { TaskDetail } from '../taskTypes';
import type { Interaction, ProposalReview } from './types';
import { Button, Icon } from './ui';
import { isNearConversationEnd } from './conversationState';

export function ConversationPane({ detail, composer, onProposal, onRetry, onDecision, sending = false }: {
  detail: TaskDetail; composer: ReactNode; onProposal: (value: ProposalReview) => void;
  onRetry: (value: Interaction) => void; onDecision: (value: { decisionId: string; expectedRevision: number; optionIndex: number }) => void; sending?: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null); const contents = useRef<HTMLDivElement>(null); const following = useRef(true);
  const [atEnd, setAtEnd] = useState(true); const [copied, setCopied] = useState('');
  const messages = detail.messages; const decision = detail.state?.project.researchDecision;
  const latest = messages.at(-1);
  const jump = () => { const el = scroller.current; if (!el) return; el.scrollTop = el.scrollHeight; following.current = true; setAtEnd(true); };
  useLayoutEffect(() => { following.current = true; jump(); }, [detail.task.id]);
  useLayoutEffect(() => { if (following.current || latest?.role === 'user') jump(); }, [latest?.id, latest?.content, detail.document.polishing]);
  useEffect(() => {
    const observer = new ResizeObserver(() => { if (following.current) jump(); });
    if (contents.current) observer.observe(contents.current); return () => observer.disconnect();
  }, []);
  const visibleInteractions = (detail.interactions || []).filter(item => item.showInConversation);
  const copy = async (id: string, text: string) => { try { await navigator.clipboard.writeText(text); setCopied(id); window.setTimeout(() => setCopied(current => current === id ? '' : current), 1600); } catch { setCopied(''); } };
  return <section className="sw-dialogue" aria-label="研究对话">
    <div ref={scroller} className="sw-dialogue-scroll" onScroll={() => { const el = scroller.current; if (!el) return; const close = isNearConversationEnd(el.scrollTop, el.scrollHeight, el.clientHeight); following.current = close; setAtEnd(close); }}>
      <div ref={contents} className="sw-dialogue-messages">
        {messages.map(message => { const interaction = visibleInteractions.find(item => item.id === message.interactionId); return <article className={`sw-message sw-message-${message.role}`} key={message.id} aria-label={message.role === 'user' ? '你的消息' : message.role === 'assistant' ? '研究助手' : '研究状态'}>
          {message.context && <div className="sw-message-context"><Icon name="link" /><span>{message.context.nodeTitle || message.context.artifactTitle || '研究记录'}</span><span>v{message.context.artifactRevision}{interaction?.stale || detail.workbench?.artifacts.some(a => a.id === message.context!.artifactId && a.revision !== message.context!.artifactRevision) ? ' · 历史版本' : ''}</span></div>}
          {['queued', 'running'].includes(message.status || '') ? <div className="sw-dialogue-pending" role="status"><span className="sw-thinking-dot" />{message.status === 'queued' ? '等待上一条回复' : '正在结合研究记录回复'}</div> : <Markdown text={message.content} />}
          {message.role === 'assistant' && message.content && <div className="sw-message-tools"><Button icon={copied === message.id ? 'check' : 'copy'} aria-label={copied === message.id ? '已复制' : '复制回复'} title={copied === message.id ? '已复制' : '复制回复'} onClick={() => { void copy(message.id, message.content); }} /></div>}
          {message.role === 'assistant' && interaction?.status === 'failed' && <div className="sw-message-failure" role="alert"><span>{interaction.error || '这次回复未完成'}</span><Button onClick={() => onRetry(interaction)}>重试</Button></div>}
          {message.role === 'user' && interaction?.proposal?.status === 'pending' && <Button className="sw-chat-proposal" variant="outline" onClick={() => onProposal({ proposal: interaction.proposal!, request: { kind: interaction.kind || 'deepen', text: interaction.text, target: interaction.target, nodeId: interaction.proposal?.command?.nodeId, replacement: interaction.proposal?.replacement, showInConversation: true } })}>查看调整影响 <Icon name="arrow" /></Button>}
        </article>; })}
        {(detail.document.polishing || sending) && <div className="sw-dialogue-pending" role="status"><span className="sw-thinking-dot" />{detail.document.polishing ? '正在整理需求' : '正在发送'}</div>}
        {detail.error && <div className="sw-message-failure" role="alert">{detail.error}</div>}
      </div>
    </div>
    {!atEnd && <Button className="sw-return-latest" icon="send" aria-label="回到最新消息" title="回到最新消息" onClick={jump} />}
    {decision && detail.state && <div className="sw-research-choice"><strong>{decision.question}</strong><div>{decision.options.map((option, index) => <Button key={option.label} variant="outline" title={option.effect} disabled={sending} onClick={() => onDecision({ decisionId: decision.id, expectedRevision: detail.state!.revision, optionIndex: index })}>{option.label}</Button>)}</div></div>}
    <div className="sw-dialogue-compose">{composer}</div>
  </section>;
}
