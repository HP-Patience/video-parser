import MathText from '../../components/MathText';
import { useEffect, useId, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import { phases } from '../../lib/jobs';
import type { TaskInfo } from '../../lib/jobs';
import Icon from '../../components/Icon';
import Markdown from '../../components/Markdown';
import { chapterTime } from '../analysis/chapters';
import type { ConversationState } from './useConversations';
import { QUESTION_LIMIT, useConversationDrafts } from './conversationDrafts';
import type { AgentTarget } from '../../lib/api';
import type { AgentModelsState } from './useAgentModels';
import AgentModelPicker from './AgentModelPicker';
import SlashCommandMenu from './SlashCommandMenu';
import { completeCommand, matchingCommands, slashToken } from './slashCommands';
import type { SlashCommand } from './slashCommands';
import './ai-chat.css';

export type LearningChat = ConversationState & {
  configured: boolean; needsConsent: boolean; provider: string; host: string; models: AgentModelsState;
};
export type AIChatProps = {
  chat: LearningChat | null; busy: 'prepare' | 'analyze' | null; status: string; error: boolean; task?: TaskInfo;
  cancelDisabled: boolean; onAsk: (conversationId: string, question: string, position: number, agent?: AgentTarget) => void; onCancel: () => void;
  onOpenSettings: (button: HTMLButtonElement) => void; onManage: () => void; onGenerateSubtitles: () => void;
};
type Props = AIChatProps & {
  videoRef?: RefObject<HTMLVideoElement | null>; duration: number; onSeek?: (time: number) => void;
  floating?: boolean; visible?: boolean; pickerVisible?: boolean; onReveal?: () => void;
};
const LIMIT = QUESTION_LIMIT;

export default function AIChat({ chat, busy, status, error, task, cancelDisabled, onAsk, onCancel, onOpenSettings, onManage, onGenerateSubtitles, videoRef, duration, onSeek, floating = false, visible = true, pickerVisible = visible, onReveal }: Props) {
  const [failure, setFailure] = useState('');
  const [picker, setPicker] = useState(false), [sending, setSending] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null), log = useRef<HTMLOListElement>(null);
  const popup = useRef<HTMLDivElement>(null), ball = useRef<HTMLButtonElement>(null);
  const sendingNow = useRef(false), composingNow = useRef(false);
  const [composing, setComposing] = useState(false);
  const commandId = useId();
  const [commandState, setCommandState] = useState({ caret: 0, end: 0, focused: false, dismissed: false, index: 0 });
  const { drafts, setDraft, draftStorageFailed } = useConversationDrafts(chat?.projectId || '', chat?.resource || '');
  const session = chat?.session || null;
  const draftId = session?.id || 'new';
  const contextKey = `${chat?.projectId}:${chat?.resource}:${session?.id}`;
  const currentContext = useRef(contextKey); currentContext.current = contextKey;
  const question = drafts[draftId] || '';
  const pending = chat?.pending;
  const waiting = !!pending && pending.conversationId === session?.id;
  const answered = session?.messages.length || 0;
  const blocked = !chat?.available || !chat.configured || busy !== null || sending || chat.creating || chat.clearing || composing || waiting;
  const commandToken = chat?.models.native && chat.configured && visible && commandState.focused && !commandState.dismissed && !composing
    ? slashToken(question, commandState.caret, commandState.end) : null;
  const commands = commandToken && !chat?.models.refreshing ? matchingCommands(chat?.models.commands || [], commandToken.query) : [];
  const commandIndex = Math.min(commandState.index, Math.max(0, commands.length - 1));
  useEffect(() => {
    setCommandState({ caret: 0, end: 0, focused: false, dismissed: false, index: 0 });
  }, [chat?.projectId, chat?.resource, draftId, chat?.models.key, visible]);
  function trackCommandCaret(field: HTMLTextAreaElement, reset = false) {
    const caret = field.selectionStart, end = field.selectionEnd, focused = document.activeElement === field;
    setCommandState(old => !reset && old.caret === caret && old.end === end && old.focused === focused ? old
      : { ...old, caret, end, focused, dismissed: reset ? false : old.dismissed, index: 0 });
  }
  function pickCommand(command: SlashCommand) {
    if (!commandToken || composingNow.current) return;
    const next = completeCommand(question, commandToken, command), field = input.current;
    if (next.text.length > LIMIT) { setFailure('输入已达到长度上限，无法插入命令。'); return; }
    setDraft(draftId, next.text);
    setCommandState(old => ({ ...old, dismissed: true, caret: next.caret, end: next.caret }));
    requestAnimationFrame(() => { if (field === input.current) { field?.focus(); field?.setSelectionRange(next.caret, next.caret); } });
  }
  useEffect(() => { setFailure(''); setPicker(false); }, [chat?.projectId, chat?.resource, session?.id, chat?.host, chat?.provider, chat?.models.key]);
  useEffect(() => { composingNow.current = false; setComposing(false); }, [chat?.projectId, chat?.resource, draftId]);
  useEffect(() => { if (!pickerVisible) setPicker(false); }, [pickerVisible]);
  useEffect(() => {
    if (!picker) return;
    function outside(event: PointerEvent) { if (!popup.current?.contains(event.target as Node) && !ball.current?.contains(event.target as Node)) setPicker(false); }
    function escape(event: KeyboardEvent) { if (event.key === 'Escape') { setPicker(false); ball.current?.focus(); } }
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [picker]);
  useEffect(() => {
    if (!chat || !pending || busy || !error || composingNow.current) return;
    setDraft(pending.conversationId, drafts[pending.conversationId] || pending.question);
    chat.setPending(null); setFailure(status || '提问失败，请重试。');
  }, [pending, busy, error, status, chat, drafts, setDraft, composing]);
  const lastResponse = useRef({ answered, waiting, session: session?.id });
  useEffect(() => {
    const previous = lastResponse.current;
    const changed = previous.answered !== answered || previous.waiting !== waiting || previous.session !== session?.id;
    lastResponse.current = { answered, waiting, session: session?.id };
    const item = log.current?.lastElementChild;
    if (visible && changed && item && item.getClientRects().length && log.current) {
      const viewport = log.current.closest<HTMLElement>('.study-sidebar-body') || log.current;
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, [answered, waiting, session?.id, visible]);
  async function submit() {
    const text = (input.current?.value ?? question).trim(), position = videoRef?.current?.currentTime || 0;
    if (!chat || !text || blocked || composingNow.current || sendingNow.current) return;
    sendingNow.current = true; setSending(true); setFailure('');
    try {
      const selected = chat.models.selected;
      const differentEngine = !!session?.messages.length && (session.agent?.engine || 'api') !== selected.engine;
      const target = session && !differentEngine ? session : await chat.newConversation(selected);
      setDraft(draftId, '');
      chat.setPending({ conversationId: target.id, question: text, count: target.messages.length });
      onAsk(target.id, text, position, selected);
    } catch (reason) { setFailure(reason instanceof Error ? reason.message : '创建对话失败，请重试。'); }
    finally { sendingNow.current = false; setSending(false); }
  }
  async function newConversation() {
    if (!chat || busy !== null || chat.creating || chat.clearing) return;
    setFailure('');
    try { await chat.newConversation(chat.models.selected); setPicker(false); onReveal?.(); requestAnimationFrame(() => input.current?.focus()); }
    catch (reason) { setFailure(reason instanceof Error ? reason.message : '创建对话失败，请重试。'); }
  }
  async function clearContext() {
    if (!chat || !session?.messages.length || busy !== null || sending || waiting || chat.creating || chat.clearing) return;
    if (!window.confirm('清空当前对话的上下文？历史消息不会删除，后续提问将不再携带之前的问答；当前视频资料仍可使用。')) return;
    const scope = contextKey; setFailure('');
    try { await chat.clearContext(); }
    catch (reason) { if (currentContext.current === scope) setFailure(reason instanceof Error ? reason.message : '清空上下文失败，请重试。'); }
  }
  function insertTime() {
    if (!chat) return;
    const field = input.current, stamp = `[${chapterTime(videoRef?.current?.currentTime || 0)}] `;
    const start = field?.selectionStart ?? question.length, end = field?.selectionEnd ?? question.length;
    setDraft(draftId, (question.slice(0, start) + stamp + question.slice(end)).slice(0, LIMIT));
    requestAnimationFrame(() => { const at = Math.min(start + stamp.length, LIMIT); field?.focus(); field?.setSelectionRange(at, at); });
  }
  const phase = task?.phase && phases[task.phase] ? phases[task.phase] + ' · ' : '';
  const list = <>
    <button type="button" className="conversation-new" disabled={busy !== null || chat?.creating || chat?.clearing} onClick={() => { void newConversation(); }}>＋ 新建对话</button>
    <div className="conversation-list">{chat?.sessions.map(item => <button type="button" key={item.id} aria-pressed={item.id === session?.id}
      onClick={() => { chat.selectConversation(item.id); setPicker(false); onReveal?.(); }}>
      <MathText text={item.title} />{item.origin_record && <small>历史对话</small>}
    </button>)}</div>
  </>;
  const floatingPicker = floating && pickerVisible && chat?.ready && chat.available ? createPortal(<>
    <button type="button" ref={ball} className="conversation-ball" aria-label="选择或新建对话" aria-expanded={picker}
      aria-controls="conversation-picker" onClick={() => setPicker(value => !value)} title="选择或新建对话"><Icon name="messages-square" /></button>
    {picker && <div ref={popup} id="conversation-picker" className="conversation-picker" role="region" aria-label="当前视频的对话">
      <div className="conversation-picker-heading"><strong>当前视频的对话</strong><button type="button" aria-label="关闭对话列表" onClick={() => { setPicker(false); ball.current?.focus(); }}><Icon name="x" /></button></div>
      {list}{failure && <p role="alert" className="error-text">{failure}</p>}
    </div>}
  </>, document.body) : null;
  return <><section className="ai-chat" aria-label="AI 对话">
    {!chat?.ready ? <div className="ai-chat-empty">{chat?.failure ? <><p role="alert">{chat.failure}</p><button type="button" onClick={chat.retry}>重新载入对话</button></> : '正在读取对话…'}</div>
      : !chat.available ? <div className="ai-chat-empty">
        <p>{chat.missing.includes('subtitles') ? (chat.missing.includes('chapters') || chat.missing.includes('summary') ? '请先准备字幕（中文或英文均可），再生成章节时间轴或内容摘要。' : '当前视频缺少字幕，中文或英文字幕均可。') : '已找到字幕；还需生成章节时间轴或内容摘要后才能对话。'}</p>
        <button type="button" onClick={chat.missing.includes('subtitles') ? onGenerateSubtitles : onManage}>
          {chat.missing.includes('subtitles') ? '生成字幕' : '准备学习材料'}</button></div>
      : <>
        {/* The placeholder "新对话" is not shown: only a generated title carries information. */}
        {(session?.title_generated || !floating) && <div className="ai-chat-session-heading">
          {session?.title_generated && <strong><MathText text={session.title} /></strong>}
          {!floating && <button type="button" onClick={() => setPicker(value => !value)} aria-expanded={picker}>选择对话</button>}</div>}
        {!floating && picker && <div className="conversation-inline-picker">{list}</div>}
        <ol className="ai-chat-log" ref={log}>{session?.messages.map(item => <li key={item.id}>
          <div className="ai-chat-question"><span className="sr-only">我的问题：</span><Markdown text={item.question} onSeek={onSeek} maxSeconds={duration} /></div>
          <div className="ai-chat-answer"><span className="sr-only">AI 回答：</span><Markdown text={item.answer} onSeek={onSeek} maxSeconds={duration} className="ai-chat-md" /></div>
          {session.context_after === item.id && <p className="ai-chat-reset" role="status">上下文已清空</p>}
        </li>)}
          {waiting && <li className="is-pending"><div className="ai-chat-question"><Markdown text={pending.question} onSeek={onSeek} maxSeconds={duration} /></div>
            <p className="ai-chat-thinking" role="status"><Icon name="loader-circle" className="spinner" />{busy ? <>{phase}{status || 'AI 正在回答…'}</> : '正在载入回答…'}</p></li>}
        </ol>
        {(failure || chat.failure) && <p className="error-text ai-chat-failure" role="alert">{failure || chat.failure}</p>}
        {!chat.configured && !chat.models.native && <p className="notice">尚未配置 AI 模型，填写后无需重启。<button type="button" className="link-button" onClick={event => onOpenSettings(event.currentTarget)}>打开设置</button></p>}
        <form className="ai-chat-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
          <label className="sr-only" htmlFor={floating ? 'ai-chat-question' : 'analysis-conversation-question'}>向 AI 提问</label>
          <div className="ai-chat-input">
          {commandToken && <SlashCommandMenu id={commandId} commands={commands} selected={commandIndex}
            refreshing={chat.models.refreshing} hasCommands={chat.models.commands.length > 0} onPick={pickCommand}
            onSelect={index => setCommandState(old => old.index === index ? old : { ...old, index })} />}
          <textarea ref={input} id={floating ? 'ai-chat-question' : 'analysis-conversation-question'} rows={3} maxLength={LIMIT} value={question} disabled={!chat.configured || sending || chat.clearing}
            aria-autocomplete="list" aria-controls={commandToken ? commandId + '-list' : undefined}
            aria-activedescendant={commandToken && commands.length ? `${commandId}-option-${commandIndex}` : undefined}
            placeholder={chat.models.native ? '输入 / 查找命令或 Skills，也可以直接提问…' : '问问 AI，例如：这一段的公式是怎么推出来的？'}
            onFocus={event => { const field = event.currentTarget; setCommandState({ caret: field.selectionStart, end: field.selectionEnd, focused: true, dismissed: false, index: 0 }); }}
            onBlur={() => setCommandState(old => ({ ...old, focused: false }))}
            onSelect={event => trackCommandCaret(event.currentTarget)}
            onClick={event => trackCommandCaret(event.currentTarget, true)}
            onChange={event => { setDraft(draftId, event.target.value, composingNow.current || (event.nativeEvent as InputEvent).isComposing); trackCommandCaret(event.currentTarget, true); }}
            onCompositionStart={() => { composingNow.current = true; setComposing(true); }}
            onCompositionEnd={event => { composingNow.current = false; setComposing(false); setDraft(draftId, event.currentTarget.value); trackCommandCaret(event.currentTarget, true); }}
            onKeyDown={event => {
              if (composingNow.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
              if (commandToken && !event.ctrlKey && !event.metaKey && !event.altKey) {
                if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setCommandState(old => ({ ...old, dismissed: true })); return; }
                if (!event.shiftKey && commands.length && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
                  event.preventDefault(); const step = event.key === 'ArrowDown' ? 1 : -1;
                  setCommandState(old => ({ ...old, index: (commandIndex + step + commands.length) % commands.length })); return;
                }
                if (!event.shiftKey && (event.key === 'Enter' || (event.key === 'Tab' && commands.length))) {
                  event.preventDefault(); if (commands.length) pickCommand(commands[commandIndex]); return;
                }
              }
              if (event.key !== 'Enter') return;
              if (event.ctrlKey || event.metaKey) {
                event.preventDefault();
                const field = event.currentTarget, start = field.selectionStart, end = field.selectionEnd;
                const next = field.value.slice(0, start) + '\n' + field.value.slice(end);
                if (next.length > LIMIT) return;
                field.setRangeText('\n', start, end, 'end');
                setDraft(draftId, field.value); trackCommandCaret(field, true);
              } else if (!event.shiftKey && !event.altKey) {
                event.preventDefault(); void submit();
              }
            }} />
          </div>
          <div className="ai-chat-actions">
            {videoRef && <button type="button" disabled={!chat.configured || composing} onClick={insertTime}>插入当前时间</button>}
            <button type="button" disabled={busy !== null || sending || waiting || chat.creating || chat.clearing || !session?.messages.length || session.context_after === session.messages.at(-1)?.id}
              aria-busy={chat.clearing} onClick={() => { void clearContext(); }}>清空上下文</button>
            <div className="ai-chat-submit-actions"><small>{question.length} / {LIMIT}</small>
            {waiting && busy === 'analyze' && <button type="button" disabled={cancelDisabled} onClick={onCancel}>停止回答</button>}
            <button type="submit" className="primary" disabled={blocked || !question.trim()} aria-busy={waiting}>
              <Icon name={waiting ? 'loader-circle' : 'send'} className={waiting ? 'spinner' : ''} />提问</button></div>
          </div>
          <AgentModelPicker state={chat.models} disabled={busy !== null || sending || chat.creating || chat.clearing || composing} onManage={onOpenSettings} />
          {!chat.configured && chat.models.native && <p className="error-text" role="status">未找到所选本地 agent，请检查安装后刷新。</p>}
          {draftStorageFailed && <p className="error-text ai-chat-failure" role="status">草稿暂时无法保存在本浏览器，刷新前请先复制。</p>}
        </form>
      </>}
  </section>{floatingPicker}</>;
}
