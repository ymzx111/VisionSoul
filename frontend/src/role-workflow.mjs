const ROOT = '/api/roles/xiaoling';
const uuid = () => crypto.randomUUID().replaceAll('-', '');

export function createRoleWorkflow({ fetcher = globalThis.fetch, makeId = uuid } = {}) {
  let state = { loading: false, busy: false, card: null, form: null, dirty: false, configured: false,
    memories: [], sources: [], preview: null, error: '', notice: '', usage: null, cached: false };
  const listeners = new Set();
  const retries = new Map();
  let initialized = false;
  let disposed = false;
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; listeners.forEach(listener => listener()); } };
  async function api(path, body, method = 'POST') {
    const response = await fetcher(ROOT + path, body === undefined ? { cache: 'no-store' } : {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data;
    try { data = await response.json(); } catch { throw new Error('返回内容无法读取，请重新读取页面；刚才的模型请求可能已有用量。'); }
    if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : '内容格式有误，请检查后再操作。'); error.status = response.status; throw error; }
    return data;
  }
  const apply = card => publish({ card, form: { ...card.draft }, dirty: false });
  async function operate(action) {
    if (state.busy || state.loading || disposed) return false;
    publish({ busy: true, error: '', notice: '', usage: null, cached: false });
    try { await action(); return true; }
    catch (error) { publish({ error: error instanceof TypeError ? '连接中断，请核对保存状态。模型操作可能已有用量，不会自动重试。' : error.message }); return false; }
    finally { publish({ busy: false }); }
  }
  async function model(path, body) {
    const fingerprint = JSON.stringify(body);
    let retry = retries.get(path);
    if (!retry || retry.fingerprint !== fingerprint) retry = { fingerprint, request_id: makeId() };
    retries.set(path, retry);
    try {
      const result = await api(path, { ...body, request_id: retry.request_id });
      retries.delete(path);
      publish({ usage: result.usage || null, cached: result.cached === true });
      return result;
    } catch (error) { if (error.status === 409) retries.delete(path); throw error; }
  }
  async function saveDraft() {
    const card = await api('/draft', { profile: state.form, expected: state.card.draft_revision }, 'PUT');
    apply(card);
  }
  async function reload() {
    if (state.busy || state.loading || disposed) return false;
    publish({ loading: true, error: '' });
    try {
      const [data, memories, sources] = await Promise.all([api(''), api('/memories'), api('/sources')]);
      if (!data.card?.draft || !data.card?.profile) throw new Error('角色卡格式不完整。');
      apply(data.card);
      retries.clear();
      publish({ configured: data.configured === true, memories, sources, notice: '', preview: null, usage: null, cached: false });
      return true;
    } catch (error) { publish({ error: error instanceof TypeError ? '无法连接角色服务，请确认后端已运行。' : error.message }); return false; }
    finally { publish({ loading: false }); }
  }
  return {
    getState: () => state, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async initialize() {
      if (disposed) return;
      if (!initialized) { initialized = true; await reload(); }
      else await operate(async () => publish({ sources: await api('/sources') }));
    }, reload,
    update(field, value) { if (state.busy || state.loading || !state.form || !Object.hasOwn(state.form, field) || field === 'name') return; publish({ form: { ...state.form, [field]: value }, dirty: true }); },
    loadVersion(revision) { if (state.busy || state.loading) return; const version = state.card.versions.find(item => item.revision === Number(revision)); if (version) publish({ form: { ...version.profile }, dirty: true, notice: '历史版本已载入草稿，保存并生效后才会影响对话。' }); },
    save(publishNow = false) { return operate(async () => {
      if (publishNow) apply(await api('/publish', { profile: state.form, expected: state.card.draft_revision })); else await saveDraft();
      publish({ notice: publishNow ? '小灵的新设定已生效，后续新消息会使用这份设定。' : '草稿已保存，正在使用的设定未改变。' });
    }); },
    discuss(message) { if (!message.trim()) return Promise.resolve(false); return operate(async () => {
      if (state.dirty) await saveDraft();
      const result = await model('/discuss', { message: message.trim(), expected: state.card.draft_revision });
      apply(result.card); publish({ notice: '设定讨论已整理到右侧草稿，尚未生效。' });
    }); },
    preview(message) { if (!message.trim()) return Promise.resolve(false); return operate(async () => {
      const result = await model('/preview', { profile: state.form, message: message.trim() }); publish({ preview: result.answer, notice: '这是草稿预览，不会进入正式聊天或记忆。' });
    }); },
    extract(conversationId) { if (!conversationId) return Promise.resolve(false); return operate(async () => {
      const result = await model('/extract', { conversation_id: conversationId });
      publish({ memories: await api('/memories'), notice: result.cached ? '这段话题已整理，本次未再次调用模型。' : `已整理候选记忆和摘要，请检查来源后确认。${result.skipped_count ? ` ${result.skipped_count} 条缺少有效来源的建议已排除。` : ''}` });
    }); },
    addMemory(category, content) { if (!content.trim()) return Promise.resolve(false); return operate(async () => {
      await api('/memories', { category, content: content.trim() }); publish({ memories: await api('/memories'), notice: '手动记忆已保存并可用于后续对话。' });
    }); },
    changeMemory(memory, patch, remove = false) { return operate(async () => {
      if (remove) await api(`/memories/${memory.id}?expected=${memory.revision}`, {}, 'DELETE');
      else await api(`/memories/${memory.id}`, { expected: memory.revision, ...patch }, 'PATCH');
      publish({ memories: await api('/memories'), notice: remove ? '记忆条目已移除。' : '记忆已更新。' });
    }); },
    dispose() { disposed = true; listeners.clear(); },
  };
}
