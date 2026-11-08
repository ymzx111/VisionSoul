const ID = /^[0-9a-f]{32}$/;
const KEY = 'visionsoul.current-conversation';
const uuid = () => crypto.randomUUID().replaceAll('-', '');
const readableError = error => error instanceof TypeError ? '连接中断，请稍后重试。' : error.message || '暂时无法完成请求。';

function browserStorage() { try { return globalThis.localStorage; } catch { return null; } }

async function read(fetcher, url, options = {}) {
  const response = await fetcher(url, options);
  let data;
  try { data = await response.json(); }
  catch { throw new Error('返回内容无法读取，请稍后重试。'); }
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '消息格式不正确，请检查后再发送。');
    error.status = response.status;
    throw error;
  }
  return data;
}

export function createChatWorkflow({ fetcher = globalThis.fetch, storage = browserStorage(), makeId = uuid } = {}) {
  let state = { conversationId: null, imageId: null, messages: [], recent: [], sending: false,
    loading: false, error: '', pending: null, usage: null, truncated: false, replayed: false,
    persona: { name: '小灵', style: '活泼俏皮，轻度幽默', context_turns: 6 } };
  const listeners = new Set();
  let disposed = false;
  let initialized = false;
  let epoch = 0;
  let controller;
  let retry = null;
  const publish = change => {
    if (disposed) return;
    state = { ...state, ...change }; listeners.forEach(listener => listener());
  };
  const remember = id => {
    try { if (id) storage?.setItem(KEY, id); else storage?.removeItem(KEY); } catch { /* 不支持本地存储时仍可继续聊天。 */ }
  };
  const applyConversation = data => {
    if (!ID.test(data.id) || !Array.isArray(data.messages)) throw new Error('话题记录不完整，请新建话题。');
    if (data.messages.some(item => !['user', 'assistant'].includes(item.role) || typeof item.content !== 'string')) {
      throw new Error('话题记录格式异常，请新建话题。');
    }
    remember(data.id);
    publish({ conversationId: data.id, imageId: data.image_id || null, messages: data.messages,
      usage: null, truncated: false, replayed: false, error: '', hasOlder: data.has_older === true });
  };
  async function initialize() {
    if (initialized || disposed) return;
    initialized = true; publish({ loading: true });
    const current = epoch;
    controller = new AbortController();
    try {
      const [persona, recent] = await Promise.all([
        read(fetcher, '/api/chat/status', { signal: controller.signal }),
        read(fetcher, '/api/chat/conversations', { signal: controller.signal }),
      ]);
      if (current !== epoch || disposed) return;
      publish({ persona, recent });
      let saved;
      try { saved = storage?.getItem(KEY); } catch { /* 当前页内仍可使用。 */ }
      if (saved && ID.test(saved)) {
        try {
          const data = await read(fetcher, `/api/chat/conversations/${saved}`, { signal: controller.signal });
          if (current === epoch && !disposed) applyConversation(data);
        } catch (error) {
          if (error.status === 404) remember(null); else throw error;
        }
      } else if (saved) remember(null);
    } catch (error) {
      if (error.name !== 'AbortError' && current === epoch) publish({ error: readableError(error) });
    } finally { if (current === epoch) publish({ loading: false }); }
  }
  async function loadConversation(id) {
    if (state.sending || state.loading || disposed || !ID.test(id)) return false;
    const current = ++epoch; retry = null;
    publish({ loading: true, error: '' }); controller = new AbortController();
    try {
      const data = await read(fetcher, `/api/chat/conversations/${id}`, { signal: controller.signal });
      if (current !== epoch || disposed) return false;
      applyConversation(data); return true;
    } catch (error) {
      if (error.name !== 'AbortError') publish({ error: readableError(error) });
      return false;
    } finally { if (current === epoch) publish({ loading: false }); }
  }
  function newConversation() {
    if (state.sending || state.loading || disposed) return false;
    epoch++; retry = null; remember(null);
    publish({ conversationId: null, imageId: null, messages: [], pending: null,
      usage: null, truncated: false, replayed: false, error: '', hasOlder: false });
    return true;
  }
  async function send(message, imageId = null, { imageCount = imageId ? 1 : 0, uploadReady = true } = {}) {
    if (state.sending || state.loading || disposed) return false;
    if (imageCount > 1) { publish({ error: '目前只能分析单个图像，请仅保留一张或清空图片后聊文字。' }); return false; }
    if (imageCount === 1 && (!imageId || !uploadReady)) { publish({ error: '请等待图片上传成功，或清空图片后聊文字。' }); return false; }
    const content = message.trim();
    if (!content || content.length > 2000) { publish({ error: '请填写 1 至 2000 个字符的消息。' }); return false; }
    const changedImage = state.imageId && imageId && state.imageId !== imageId;
    if (changedImage) newConversation();
    const body = { conversation_id: state.conversationId, message: content, image_id: imageId };
    const fingerprint = JSON.stringify(body);
    if (!retry || retry.fingerprint !== fingerprint) retry = { fingerprint, request_id: makeId() };
    const current = epoch;
    controller = new AbortController();
    publish({ sending: true, error: '', pending: { role: 'user', content, image_id: imageId }, usage: null, truncated: false });
    try {
      const data = await read(fetcher, '/api/chat/messages', { method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, request_id: retry.request_id }) });
      if (current !== epoch || disposed) return false;
      if (!ID.test(data.conversation_id) || data.user_message?.role !== 'user'
          || data.assistant_message?.role !== 'assistant' || !data.assistant_message.content?.trim()) {
        throw new Error('回复内容不完整，请重试以恢复这条消息。');
      }
      const existing = new Set(state.messages.map(item => item.id));
      const additions = [data.user_message, data.assistant_message].filter(item => !existing.has(item.id));
      remember(data.conversation_id); retry = null;
      publish({ conversationId: data.conversation_id, imageId: data.image_id || null,
        messages: [...state.messages, ...additions], usage: data.usage, truncated: data.truncated,
        replayed: data.replayed, pending: null, sending: false });
      void read(fetcher, '/api/chat/conversations').then(recent => {
        if (current === epoch) publish({ recent });
      }).catch(() => { /* 回复已保存，列表读取失败不能把成功消息变成失败。 */ });
      return true;
    } catch (error) {
      if (current === epoch && error.name !== 'AbortError') publish({ error: readableError(error), sending: false, pending: null });
      return false;
    }
  }
  return { getState: () => state, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize, loadConversation, newConversation, send,
    dispose() { disposed = true; epoch++; controller?.abort(); listeners.clear(); } };
}
