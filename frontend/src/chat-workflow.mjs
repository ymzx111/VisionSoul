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

export function createChatWorkflow({ fetcher = globalThis.fetch, storage = browserStorage(), makeId = uuid, mode = 'image', space } = {}) {
  if (space && !['work', 'companion'].includes(space)) throw new Error('未知对话空间');
  const scoped = url => space ? `${url}${url.includes('?') ? '&' : '?'}space=${space}` : url;
  const key = space ? `visionsoul.${space}.current-conversation` : mode === 'media' ? 'visionsoul.current-media-conversation' : mode === 'video' ? 'visionsoul.current-video-conversation' : KEY;
  const recentUrl = scoped(mode === 'media' ? '/api/chat/conversations?kind=all' : mode === 'video' ? '/api/chat/conversations?kind=video' : '/api/chat/conversations');
  let state = { conversationId: null, imageId: null, messages: [], recent: [], sending: false,
    videoId: null, videoContext: null,
    loading: false, error: '', pending: null, usage: null, truncated: false, replayed: false, localSource: null,
    space, legacy: [],
    persona: { name: space === 'work' ? '解析助手' : '小灵', style: space === 'work' ? '客观、清晰、直接' : '活泼俏皮，轻度幽默', context_turns: 6 } };
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
    try { if (id) storage?.setItem(key, id); else storage?.removeItem(key); } catch { /* 不支持本地存储时仍可继续聊天。 */ }
  };
  const applyConversation = data => {
    if (space && data.space !== space) throw new Error('话题不属于当前空间。');
    if (!ID.test(data.id) || !Array.isArray(data.messages)) throw new Error('话题记录不完整，请新建话题。');
    if (data.messages.some(item => !['user', 'assistant'].includes(item.role) || typeof item.content !== 'string')) {
      throw new Error('话题记录格式异常，请新建话题。');
    }
    if (mode === 'video' && !/^[0-9a-f]{64}$/.test(data.video_id || '')) throw new Error('这不是视频话题，请选择视频话题或新建。');
    if (mode === 'media' && ((data.image_id && !ID.test(data.image_id)) || (data.video_id && !/^[0-9a-f]{64}$/.test(data.video_id))
        || (data.image_id && data.video_id))) throw new Error('话题素材关联异常，请检查本机记录。');
    remember(data.id);
    publish({ conversationId: data.id, imageId: data.image_id || null, videoId: data.video_id || null, videoContext: data.video_context || null, messages: data.messages,
      usage: null, truncated: false, replayed: false, localSource: null, error: '', hasOlder: data.has_older === true });
  };
  async function initialize() {
    if (initialized || disposed) return;
    initialized = true; publish({ loading: true });
    const current = epoch;
    controller = new AbortController();
    try {
      const [persona, recent] = await Promise.all([
        read(fetcher, scoped('/api/chat/status'), { signal: controller.signal }),
        read(fetcher, recentUrl, { signal: controller.signal }),
      ]);
      if (current !== epoch || disposed) return;
      publish({ persona, recent });
      if (space) await refreshLegacy();
      let saved;
      try {
        saved = storage?.getItem(key);
        if (!saved && mode === 'media' && !space) {
          const candidates = [storage?.getItem('visionsoul.current-video-conversation'), storage?.getItem(KEY)].filter(Boolean);
          saved = recent.find(row => candidates.includes(row.id))?.id || candidates[0];
        }
      } catch { /* 当前页内仍可使用。 */ }
      if (saved && ID.test(saved)) {
        try {
          const data = await read(fetcher, scoped(`/api/chat/conversations/${saved}`), { signal: controller.signal });
          if (current === epoch && !disposed) applyConversation(data);
        } catch (error) {
          if ([404, 409].includes(error.status)) remember(null); else throw error;
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
      const data = await read(fetcher, scoped(`/api/chat/conversations/${id}`), { signal: controller.signal });
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
    publish({ conversationId: null, imageId: null, videoId: null, videoContext: null, messages: [], pending: null,
      usage: null, truncated: false, replayed: false, localSource: null, error: '', hasOlder: false });
    return true;
  }
  async function send(message, imageId = null, { imageCount = imageId ? 1 : 0, uploadReady = true, videoId = null, inspectVideo = false, debugCode = false, pythonTest = null } = {}) {
    if (state.sending || state.loading || disposed) return false;
    if (inspectVideo && debugCode) { publish({ error: '一次请选择一种视频分析任务。' }); return false; }
    if ((mode === 'video' || (mode === 'media' && !imageId && (videoId || state.videoId))) && (!uploadReady || (!videoId && !state.videoId))) {
      publish({ error: uploadReady ? '请先选择并上传一个视频。' : '请等待视频上传完成，或清空未上传成功的选择后继续原话题。' }); return false;
    }
    if (imageCount > 1) { publish({ error: '目前只支持单张图片上传' }); return false; }
    if (imageCount === 1 && (!imageId || !uploadReady)) { publish({ error: '请等待图片上传成功，或清空图片后聊文字。' }); return false; }
    const content = message.trim();
    const autoVideo = mode === 'media' && !state.conversationId && videoId && !imageId && !inspectVideo && !debugCode;
    if ((!content && !autoVideo) || content.length > 2000) { publish({ error: '请填写 1 至 2000 个字符的消息。' }); return false; }
    const changedImage = state.imageId && imageId && state.imageId !== imageId;
    const changedVideo = mode !== 'image' && state.videoId && videoId && state.videoId !== videoId;
    const changedKind = mode === 'media' && ((imageId && state.videoId) || (videoId && state.imageId));
    if (changedImage || changedVideo || changedKind) newConversation();
    const body = { conversation_id: state.conversationId, message: content, image_id: imageId };
    if (space) body.space = space;
    if (mode === 'video' || (mode === 'media' && (videoId || state.videoId))) body.video_id = videoId || state.videoId;
    if (body.video_id && inspectVideo) body.inspect_video = true;
    if (body.video_id && debugCode) body.debug_code = true;
    if (mode === 'media' && space === 'work' && (imageId || state.imageId || body.video_id) && !inspectVideo && !debugCode) {
      body.open_in_ide = true;
      if (pythonTest) body.python_test = pythonTest;
    }
    const fingerprint = JSON.stringify(body);
    if (!retry || retry.fingerprint !== fingerprint) retry = { fingerprint, request_id: makeId() };
    const current = epoch;
    controller = new AbortController();
    publish({ sending: true, error: '', pending: { role: 'user', content: content || '分析这段视频', image_id: imageId, video_id: state.videoId ? null : body.video_id }, usage: null, truncated: false, localSource: null });
    try {
      const data = await read(fetcher, '/api/chat/messages', { method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body, request_id: retry.request_id }) });
      if (current !== epoch || disposed) return false;
      if (space && data.space !== space) throw new Error('回复空间不一致，请重试恢复原消息。');
      if (!ID.test(data.conversation_id) || data.user_message?.role !== 'user'
          || data.assistant_message?.role !== 'assistant' || !data.assistant_message.content?.trim()) {
        throw new Error('回复内容不完整，请重试以恢复这条消息。');
      }
      if (mode === 'video' && data.video_id !== body.video_id) throw new Error('视频关联结果不一致，请重试恢复原消息。');
      if (mode === 'media' && ((data.video_id || null) !== (body.video_id || null)
          || (data.image_id || null) !== (body.image_id || state.imageId || null))) throw new Error('素材关联结果不一致，请重试恢复原消息。');
      const existing = new Set(state.messages.map(item => item.id));
      const additions = [data.user_message, data.assistant_message].filter(item => !existing.has(item.id));
      remember(data.conversation_id); retry = null;
      publish({ conversationId: data.conversation_id, imageId: data.image_id || null, videoId: data.video_id || null, videoContext: data.video_context || null,
        messages: [...state.messages, ...additions], usage: data.usage, truncated: data.truncated,
        replayed: data.replayed, localSource: data.local_source || null, pending: null, sending: false });
      void read(fetcher, recentUrl).then(recent => {
        if (current === epoch) publish({ recent });
      }).catch(() => { /* 回复已保存，列表读取失败不能把成功消息变成失败。 */ });
      return true;
    } catch (error) {
      if (current === epoch && error.name !== 'AbortError') publish({ error: readableError(error) + (mode === 'video' ? ' 请求失败或超时也可能已有用量；不会自动重试。' : ''), sending: false, pending: null });
      return false;
    }
  }
  async function refreshLegacy() {
    if (!space || disposed) return;
    try { const legacy = await read(fetcher, '/api/chat/conversations?kind=all&space=legacy'); publish({ legacy }); }
    catch { publish({ error: '旧话题列表暂时无法读取，已有话题仍可使用。' }); }
  }
  async function classifyLegacy(id) {
    if (!space || disposed || state.sending || state.loading || !ID.test(id)) return false;
    publish({ loading: true, error: '' });
    try {
      await read(fetcher, `/api/chat/conversations/${id}/classify`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ space }) });
      const recent = await read(fetcher, recentUrl);
      publish({ recent });
      await refreshLegacy();
      const data = await read(fetcher, scoped(`/api/chat/conversations/${id}`));
      applyConversation(data); return true;
    } catch (error) { publish({ error: readableError(error) }); await refreshLegacy(); return false; }
    finally { publish({ loading: false }); }
  }
  return { getState: () => state, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize, loadConversation, newConversation, send,
    refreshLegacy, classifyLegacy,
    async refreshPersona() { try { const persona = await read(fetcher,scoped('/api/chat/status')); publish({ persona }); } catch { /* 不丢失已有对话和输入。 */ } },
    dispose() { disposed = true; epoch++; controller?.abort(); listeners.clear(); } };
}
