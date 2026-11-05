import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from '../../lib/api';
import type { AgentTarget, Conversation, ConversationContext } from '../../lib/api';

import { useConversationDrafts } from './conversationDrafts';
export { QUESTION_LIMIT } from './conversationDrafts';

export type PendingQuestion = { conversationId: string; question: string; count: number; images?: { name: string }[] };
const selectionKey = (project: string) => `video-parser:conversation:${project}`;
function savedSelection(project: string) { try { return localStorage.getItem(selectionKey(project)); } catch { return null; } }

/** One owner in AnalysisPanel feeds both the existing manager and the learning sidebar. */
export default function useConversations(projectId: string, resource: string, version: number, busy: boolean) {
  const key = `${encodeURIComponent(projectId)}:${encodeURIComponent(resource)}`;
  const liveKey = useRef(key); liveKey.current = key;
  const [data, setData] = useState<{ key: string; sessions: Conversation[]; active: string | null; context: ConversationContext | null }>({ key: '', sessions: [], active: null, context: null });
  const [failure, setFailure] = useState('');
  const [creating, setCreating] = useState(false);
  const [clearing, setClearing] = useState(false);
  const clearingNow = useRef(false), loadEpoch = useRef(0);
  const { drafts, setDraft, draftStorageFailed } = useConversationDrafts(projectId, resource);
  const [pending, setPending] = useState<PendingQuestion | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const refresh = () => setReload(value => value + 1);
    window.addEventListener('video-parser:subtitles-changed', refresh);
    return () => window.removeEventListener('video-parser:subtitles-changed', refresh);
  }, []);
  const operations = useRef<AbortController | null>(null);
  const creation = useRef(false);
  useEffect(() => {
    setPending(null); setCreating(false); creation.current = false; setClearing(false); clearingNow.current = false;
    return () => operations.current?.abort();
  }, [key]);
  useEffect(() => {
    if (!projectId || !resource) return;
    const controller = new AbortController(), epoch = ++loadEpoch.current;
    setFailure('');
    Promise.all([
      request<{ conversations: Conversation[] }>(`/projects/${projectId}/conversations${busy ? '' : '/migrate'}`, controller.signal, busy ? undefined : {}),
      request<ConversationContext>(`/projects/${projectId}/conversations/context?resource=${encodeURIComponent(resource)}`, controller.signal),
    ]).then(([list, context]) => {
      if (controller.signal.aborted || epoch !== loadEpoch.current) return;
      const sessions = list.conversations.filter(item => !item.video_resource || item.video_resource === resource);
      setData(old => {
        const preferred = old.key === key ? old.active : savedSelection(projectId);
        const active = sessions.find(item => item.id === preferred)?.id || sessions[0]?.id || null;
        return { key, sessions, active, context };
      });
      setPending(previous => previous && list.conversations.some(item => item.id === previous.conversationId && item.messages.length > previous.count) ? null : previous);
    }).catch((reason: unknown) => { if (!controller.signal.aborted && epoch === loadEpoch.current) setFailure(reason instanceof Error ? reason.message : '无法读取对话。'); });
    return () => controller.abort();
  }, [projectId, resource, key, version, busy, reload]);
  const selectConversation = useCallback((id: string) => {
    setData(old => old.key === key && old.sessions.some(item => item.id === id) ? { ...old, active: id } : old);
    try { localStorage.setItem(selectionKey(projectId), id); } catch { /* Optional preference only. */ }
  }, [projectId, key]);
  const newConversation = useCallback(async (agent?: AgentTarget) => {
    if (creation.current || clearingNow.current) throw new Error('正在更新对话，请稍候。');
    creation.current = true; setCreating(true); setFailure('');
    const controller = new AbortController(); operations.current = controller;
    try {
      const result = await request<{ conversation: Conversation }>(`/projects/${projectId}/conversations`, controller.signal, { resource, agent });
      if (liveKey.current !== key || controller.signal.aborted) throw new Error('当前视频已切换。');
      setData(old => ({ ...old, key, sessions: [result.conversation, ...old.sessions], active: result.conversation.id }));
      try { localStorage.setItem(selectionKey(projectId), result.conversation.id); } catch { /* Optional. */ }
      return result.conversation;
    } finally { if (operations.current === controller) { operations.current = null; creation.current = false; setCreating(false); } }
  }, [projectId, resource, key]);
  const clearContext = useCallback(async () => {
    const session = data.key === key ? data.sessions.find(item => item.id === data.active) : null;
    if (!session || creation.current || clearingNow.current || busy || pending) throw new Error('当前对话正在处理，请稍候再清空上下文。');
    const last = session.messages.at(-1)?.id || null;
    if (!last || session.context_after === last) return;
    clearingNow.current = true; setClearing(true); setFailure(''); ++loadEpoch.current;
    const controller = new AbortController(); operations.current = controller;
    try {
      const result = await request<{ conversation: Conversation }>(`/projects/${projectId}/conversations/${session.id}/clear-context`, controller.signal,
        { resource, expected_last_message_id: last, confirm: true });
      if (liveKey.current !== key || controller.signal.aborted) return;
      setData(old => old.key !== key ? old : { ...old, sessions: old.sessions.map(item => item.id === session.id ? result.conversation : item) });
      setReload(value => value + 1);
    } finally {
      if (operations.current === controller) { operations.current = null; clearingNow.current = false; setClearing(false); }
    }
  }, [projectId, resource, key, data, busy, pending]);
  const retry = useCallback(() => setReload(value => value + 1), []);
  return useMemo(() => {
  const matches = data.key === key;
  return {
    projectId, resource, ready: matches && !!data.context, available: matches && !!data.context?.available,
    missing: matches ? data.context?.missing || [] : [], sessions: matches ? data.sessions : [],
    session: matches ? data.sessions.find(item => item.id === data.active) || null : null,
    failure, creating, clearing, clearContext, drafts, draftStorageFailed, setDraft, pending, setPending, selectConversation, newConversation,
    retry,
  };
  }, [projectId, resource, data, key, failure, creating, clearing, clearContext, drafts, draftStorageFailed, pending, setDraft, selectConversation, newConversation, retry]);
}

export type ConversationState = ReturnType<typeof useConversations>;
