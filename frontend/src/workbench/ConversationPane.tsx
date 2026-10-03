import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Markdown } from '../Markdown';
import { conceptProgress } from '../conceptState';
import type { TaskDetail } from '../taskTypes';
import type { Interaction, ProposalReview } from './types';
import { TopicSelectionPanel } from './TopicSelectionPanel';
import type { TopicSelectionRequest } from './topicSelectionState';
import type { ResearchNode } from '../types';
import { executionNodes } from './executionState';
import { ModelWaitNotice } from './ModelWaitNotice';
import './model-recovery.css';
import { Button, Icon } from './ui';
import { isNearConversationEnd } from './conversationState';

export function ConversationPane({ detail, composer, onProposal, onRetry, onDecision, onTopicSelection, onRetryNode, onInspectNode, sending = false }: {
  detail: TaskDetail; composer: ReactNode; onProposal: (value: ProposalReview) => void;
  onRetry: (value: Interaction) => void; onDecision: (value: { decisionId: string; expectedRevision: number; optionIndex: number }) => void; onTopicSelection: (value: TopicSelectionRequest) => void; sending?: boolean;
  onRetryNode: (node: ResearchNode) => void; onInspectNode: (node: ResearchNode) => void;
}) {
  const pane = useRef<HTMLElement>(null); const dock = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null); const contents = useRef<HTMLDivElement>(null); const following = useRef(true);
  const [atEnd, setAtEnd] = useState(true); const [copied, setCopied] = useState('');
  const messages = detail.messages; const decision = detail.state?.project.researchDecision; const cycle = detail.state?.project.researchCycle; const topicPending = cycle?.topicSelection?.status === 'pending';
  const topicAnchor = messages.find(message => Boolean(cycle?.topicSelection?.id) && message.topicSelectionId === cycle?.topicSelection?.id && message.role === 'assistant');
  const latest = messages.at(-1);
  const jump = () => { const el = scroller.current; if (!el) return; el.scrollTop = el.scrollHeight; following.current = true; setAtEnd(true); };
  useLayoutEffect(() => { following.current = true; jump(); }, [detail.task.id]);
  useLayoutEffect(() => { if (following.current || latest?.role === 'user') jump(); }, [latest?.id, latest?.content, detail.document.polishing]);
  useEffect(() => {
    const observer = new ResizeObserver(() => { if (following.current) jump(); });
    if (contents.current) observer.observe(contents.current); return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const el = scroller.current, panel = el?.querySelector('.sw-topic-selection');
    if (!topicPending || !el || !panel) return;
    el.scrollTop += panel.getBoundingClientRect().top - el.getBoundingClientRect().top - 12;
    following.current = false;
    setAtEnd(isNearConversationEnd(el.scrollTop, el.scrollHeight, el.clientHeight));
  }, [detail.task.id, topicPending, cycle?.topicSelection?.id, topicAnchor?.id]);
  useLayoutEffect(() => {
    const element = dock.current; if (!element) return;
    const measure = () => {
      pane.current?.style.setProperty('--sw-dock-height', `${Math.ceil(element.getBoundingClientRect().height)}px`);
      if (following.current) jump();
    };
    measure(); const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const visibleInteractions = (detail.interactions || []).filter(item => item.showInConversation);
  const recoveryNodes = executionNodes(detail.state).filter(node => node.status === 'failed' || node.status === 'running' && node.modelWait);
  const copy = async (id: string, text: string) => { try { await navigator.clipboard.writeText(text); setCopied(id); window.setTimeout(() => setCopied(current => current === id ? '' : current), 1600); } catch { setCopied(''); } };
  const topicPanel = topicPending && cycle && detail.state && <TopicSelectionPanel key={`${detail.task.id}:${cycle.topicSelection?.id}`} cycle={cycle} revision={detail.state.revision} busy={sending} onSelect={onTopicSelection} />;
  return <section ref={pane} className="sw-dialogue" aria-label="研究对话"><header className="sw-dialogue-header"><span><Icon name="chat" />研究对话</span><span>随时补充你的想法</span></header>
    <div ref={scroller} className="sw-dialogue-scroll" onScroll={() => { const el = scroller.current; if (!el) return; const close = isNearConversationEnd(el.scrollTop, el.scrollHeight, el.clientHeight); following.current = close; setAtEnd(close); }}>
      <div ref={contents} className="sw-dialogue-messages">
        {messages.map(message => { const interaction = visibleInteractions.find(item => item.id === message.interactionId); return <article className={`sw-message sw-message-${message.role}`} key={message.id} aria-label={message.role === 'user' ? '你的消息' : message.role === 'assistant' ? '研究助手' : '研究状态'}>
          {message.role === 'assistant' && <div className="sw-assistant-label"><Icon name="layers" /><span>研究助手</span></div>}
          {message.context && <div className="sw-message-context"><Icon name="link" /><span>{message.context.nodeTitle || message.context.artifactTitle || '研究记录'}</span><span>v{message.context.artifactRevision}{interaction?.stale || detail.workbench?.artifacts.some(a => a.id === message.context!.artifactId && a.revision !== message.context!.artifactRevision) ? ' · 历史版本' : ''}</span></div>}
          {['queued', 'running'].includes(message.status || '') ? <div className="sw-dialogue-pending" role="status"><span className="sw-thinking-dot" />{message.status === 'queued' ? '等待上一条回复' : '正在结合研究记录回复'}</div> : <Markdown text={message.content} />}
          {message.role === 'assistant' && message.content && <div className="sw-message-tools"><Button icon={copied === message.id ? 'check' : 'copy'} aria-label={copied === message.id ? '已复制' : '复制回复'} title={copied === message.id ? '已复制' : '复制回复'} onClick={() => { void copy(message.id, message.content); }} /></div>}
          {topicAnchor?.id === message.id && topicPanel}
          {message.role === 'assistant' && interaction?.status === 'failed' && <div className="sw-message-failure" role="alert"><span>{interaction.error || '这次回复未完成'}</span><Button onClick={() => onRetry(interaction)}>重试</Button></div>}
          {message.role === 'user' && interaction?.proposal?.status === 'pending' && <Button className="sw-chat-proposal" variant="outline" onClick={() => onProposal({ proposal: interaction.proposal!, request: { kind: interaction.kind || 'deepen', text: interaction.text, target: interaction.target, nodeId: interaction.proposal?.command?.nodeId, replacement: interaction.proposal?.replacement, showInConversation: true } })}>查看调整影响 <Icon name="arrow" /></Button>}
        </article>; })}
        {!topicAnchor && topicPanel}
        {(detail.document.polishing || sending) && <div className="sw-dialogue-pending" role="status"><span className="sw-thinking-dot" />{detail.document.polishing ? conceptProgress(detail.document) : '正在发送'}</div>}
        {recoveryNodes.map(node => <section className="sw-recovery-node" key={`${node.id}:${node.version}`} aria-label={`恢复节点：${node.title}`}>
          <h3>{node.title}</h3>
          {node.modelWait && node.status === 'running' ? <ModelWaitNotice wait={node.modelWait} /> : <p role="alert">{node.error?.message || '节点执行受阻，请查看详情。'}{node.error?.retryExhausted && ' 自动恢复次数或等待预算已用完。'}</p>}
          <div>{node.status === 'failed' && node.error?.retryable !== false && <Button variant="outline" disabled={sending} onClick={() => onRetryNode(node)}>重试节点</Button>}<Button disabled={sending} onClick={() => onInspectNode(node)}>查看详情</Button></div>
        </section>)}
        {detail.error && !recoveryNodes.length && <div className="sw-message-failure" role="alert">{detail.error}</div>}
      </div>
    </div>
    {!atEnd && <Button className="sw-return-latest" icon="send" aria-label="回到最新消息" title="回到最新消息" onClick={jump} />}
    {decision && detail.state && <div className="sw-research-choice"><strong>{decision.question}</strong><div>{decision.options.map((option, index) => <Button key={option.label} variant="outline" title={option.effect} disabled={sending} onClick={() => onDecision({ decisionId: decision.id, expectedRevision: detail.state!.revision, optionIndex: index })}>{option.label}</Button>)}</div></div>}
    <div ref={dock} className="sw-dialogue-compose">{composer}</div>
  </section>;
}
