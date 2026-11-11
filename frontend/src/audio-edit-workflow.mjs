import { validateAudioEvidence } from './audio-evidence.mjs';

export function createAudioEditWorkflow({ videoId, duration, fetcher = globalThis.fetch, onSaved = async () => {} }) {
  let state = { open: false, loading: false, saving: false, record: null, segments: [], error: '' };
  let disposed = false;
  const listeners = new Set();
  const publish = patch => { if (!disposed) { state = { ...state, ...patch }; listeners.forEach(fn => fn()); } };
  const validate = record => {
    if (!Number.isInteger(record.revision) || record.revision < 0) throw new Error('校对版本无效。');
    validateAudioEvidence(record.audio, [], [], duration);
    validateAudioEvidence(record.original, [], [], duration);
    if (record.audio.status !== 'ready' || record.original.segments.length !== record.audio.segments.length) throw new Error('当前没有可校对的对白。');
    return record;
  };
  const request = async options => {
    const response = await fetcher(`/api/videos/${videoId}/audio`, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || '对白校对未完成。');
    return validate(body);
  };
  return {
    getState: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    async open() {
      if (disposed || state.loading || state.saving) return false;
      publish({ open: true, loading: true, error: '' });
      try {
        const record = await request();
        publish({ record, segments: record.audio.segments.map(({ id, start, end, text }) => ({ id, start, end, text })) });
        return true;
      } catch (error) { publish({ error: error.message || '读取失败，请稍后重试。' }); return false; }
      finally { publish({ loading: false }); }
    },
    close() { if (!state.loading && !state.saving) publish({ open: false, error: '' }); },
    edit(id, patch) {
      if (!state.open || state.loading || state.saving) return;
      const allowed = Object.fromEntries(Object.entries(patch).filter(([key]) => ['text', 'start', 'end'].includes(key)));
      publish({ segments: state.segments.map(segment => segment.id === id ? { ...segment, ...allowed } : segment), error: '' });
    },
    async save() {
      if (disposed || state.loading || state.saving || !state.record) return false;
      try {
        validateAudioEvidence({ ...state.record.audio, segments: state.segments }, [], [], duration);
        if (state.segments.some(segment => segment.start >= segment.end || segment.text.length > 300)
            || state.segments.reduce((total, segment) => total + segment.text.length, 0) > 6000) throw new Error('请检查对白长度和起止时间，结束须晚于开始。');
      } catch (error) { publish({ error: error.message }); return false; }
      publish({ saving: true, error: '' });
      try {
        const record = await request({ method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ expected_revision: state.record.revision, segments: state.segments }) });
        publish({ record, open: false });
        await onSaved(record);
        return true;
      } catch (error) { publish({ error: error.message || '保存失败，未自动重试。' }); return false; }
      finally { publish({ saving: false }); }
    },
    dispose() { disposed = true; listeners.clear(); },
  };
}
