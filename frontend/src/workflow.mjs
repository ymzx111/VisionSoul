const MAX_BYTES = 10 * 1024 * 1024;
const IMAGE_ID = /^[0-9a-f]{32}$/;

function messageFor(error) {
  return error instanceof Error && error.name !== 'TypeError'
    ? error.message : '暂时无法连接后端，请确认服务运行后再试。';
}

async function jsonResponse(fetcher, url, options) {
  const response = await fetcher(url, options);
  let body;
  try { body = await response.json(); }
  catch { throw new Error('服务器返回了无法读取的内容，请稍后再试。'); }
  if (!response.ok) {
    throw new Error(typeof body.detail === 'string' ? body.detail : '请求内容有误，请检查图片和问题后再试。');
  }
  return body;
}

export function validateFiles(files) {
  if (files.length > 1) return '目前只支持单张图片上传';
  for (const file of files) {
    if (!/\.(jpe?g|png|webp)$/i.test(file.name)) return '请选择 JPG、PNG 或 WebP 静态图片。';
    if (!file.size) return '图片内容为空，请重新选择。';
    if (file.size > MAX_BYTES) return '图片超过 10 MB，请缩小后再选择。';
  }
  return null;
}

/** 一次选择对应一个版本，过期的上传结果不会绑定到新图片。 */
export function createImageWorkflow({ fetcher = globalThis.fetch, initialConfigured = null } = {}) {
  let state = {
    files: [], upload: null, uploadStage: 'empty', selectionError: '',
    analyzing: false, result: null, analysisError: '',
    configured: initialConfigured, configError: '',
  };
  let version = 0;
  let serial = 0;
  let disposed = false;
  let uploadAbort;
  let analysisAbort;
  let configAbort;
  const listeners = new Set();
  const publish = change => {
    if (disposed) return;
    state = { ...state, ...change };
    listeners.forEach(listener => listener());
  };

  async function initialize() {
    if (configAbort || disposed) return;
    configAbort = new AbortController();
    try {
      const config = await jsonResponse(fetcher, '/api/vision/status', { signal: configAbort.signal });
      publish({ configured: config.configured === true, configError: '' });
    } catch (error) {
      if (error.name !== 'AbortError') publish({ configured: null, configError: messageFor(error) });
    }
  }

  async function selectFiles(input) {
    if (state.analyzing || disposed) return false;
    const current = ++version;
    uploadAbort?.abort();
    const files = Array.from(input);
    const invalid = validateFiles(files);
    const entries = invalid ? [] : files.map(file => ({ id: `file-${++serial}`, file }));
    publish({ files: entries, upload: null, result: null, analysisError: '',
      selectionError: invalid || '', uploadStage: invalid ? 'error' : !entries.length ? 'empty' : entries.length > 1 ? 'multiple' : 'uploading' });
    if (invalid || entries.length !== 1) return false;
    const controller = new AbortController();
    uploadAbort = controller;
    const form = new FormData();
    form.append('file', entries[0].file, entries[0].file.name);
    try {
      const uploaded = await jsonResponse(fetcher, '/api/images', { method: 'POST', body: form, signal: controller.signal });
      if (current !== version || disposed) return false;
      if (!IMAGE_ID.test(uploaded.image_id)) throw new Error('图片上传结果不完整，请重新选择图片。');
      publish({ upload: { ...uploaded, url: `/api/images/${uploaded.image_id}` }, uploadStage: 'ready' });
      return true;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') {
        publish({ upload: null, uploadStage: 'error', selectionError: messageFor(error) });
      }
      return false;
    }
  }

  async function analyze(question) {
    if (disposed || state.analyzing) return false;
    let invalid;
    if (state.files.length > 1) invalid = '目前只支持单张图片上传';
    else if (!state.files.length) invalid = '请先选择一张图片。';
    else if (state.uploadStage !== 'ready' || !state.upload) invalid = '请等待图片上传成功后再分析。';
    else if (state.configured !== true) invalid = state.configError || '模型尚未配置，请先完成后端密钥设置。';
    else if (!question.trim()) invalid = '请填写你想了解的问题。';
    else if (question.trim().length > 2000) invalid = '问题最多为 2000 个字符。';
    if (invalid) { publish({ analysisError: invalid }); return false; }

    const current = version;
    const imageId = state.upload.image_id;
    const asked = question.trim();
    analysisAbort = new AbortController();
    publish({ analyzing: true, result: null, analysisError: '' });
    try {
      const answer = await jsonResponse(fetcher, '/api/vision/analyze', {
        method: 'POST', signal: analysisAbort.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId, question: asked }),
      });
      if (current !== version || disposed) return false;
      if (answer.image_id !== imageId || typeof answer.answer !== 'string' || !answer.answer.trim()) {
        throw new Error('模型返回了不完整的结果，请稍后再试。');
      }
      publish({ result: { ...answer, question: asked }, analyzing: false });
      return true;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') {
        publish({ analyzing: false, analysisError: messageFor(error) });
      }
      return false;
    }
  }

  return {
    getState: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize, selectFiles, analyze,
    removeFile(id) { return selectFiles(state.files.filter(entry => entry.id !== id).map(entry => entry.file)); },
    clear() { return selectFiles([]); },
    dispose() { disposed = true; version++; uploadAbort?.abort(); analysisAbort?.abort(); configAbort?.abort(); listeners.clear(); },
  };
}
