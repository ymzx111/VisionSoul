import { validateComparison } from './video-comparison.mjs';
const VIDEO_ID = /^[0-9a-f]{64}$/;
const STORAGE_KEY = 'visionsoul.currentVideo';

const errorMessage = error => error instanceof Error && error.name !== 'TypeError'
  ? error.message : '暂时无法连接后端，请确认服务运行后再试。';

async function request(fetcher, url, options) {
  const response = await fetcher(url, options);
  let body;
  try { body = await response.json(); }
  catch { throw new Error('服务器结果无法读取。分析请求可能已有用量，请勿连续重试。'); }
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '视频请求未完成，请检查文件后再试。');
  return body;
}

function validatedResult(result, video) {
  validateComparison(result.comparison,video.frames);
  if (result.video_id !== video.video_id || !Array.isArray(result.observations) || !result.observations.length
      || typeof result.summary !== 'string' || typeof result.reply !== 'string') {
    throw new Error('视频分析结果不完整，请检查后端记录；不会自动重新分析。');
  }
  for (const item of result.observations) {
    if (!video.frames.some(frame => frame.index === item.frame_index)
        || !Array.isArray(item.keywords) || !item.keywords.every(word => typeof word === 'string')
        || typeof item.description !== 'string') throw new Error('分析结果缺少对应截图，无法展示。');
  }
  return result;
}

function validatedVideo(data, maxFrames) {
  if (!VIDEO_ID.test(data.video_id) || !Array.isArray(data.frames) || !data.frames.length
      || !Number.isFinite(data.duration_seconds) || data.duration_seconds <= 0) {
    throw new Error('视频上传结果不完整，请重新上传。');
  }
  if (data.frames.length > maxFrames) {
    throw new Error('视频采样数量与当前页面不匹配，请刷新页面后重新选择视频。');
  }
  const video = { ...data, video_url: `/api/videos/${data.video_id}/file`, frames: data.frames.map((frame, index) => {
    // 编号随实际采样数组校验，不再和旧版 20 张或新版 30 张上限重复绑定。
    if (!Number.isInteger(frame.index) || frame.index !== index) {
      throw new Error('视频采样编号不连续，请刷新页面后重新选择视频。');
    }
    if (!Number.isFinite(frame.time_seconds) || frame.time_seconds < 0 || frame.time_seconds > data.duration_seconds) {
      throw new Error('视频采样时间超出时长或格式无效，请重新选择视频。');
    }
    return { ...frame, url: `/api/videos/${data.video_id}/frames/${frame.index}`,
      detail_url: frame.detail_url ? `/api/videos/${data.video_id}/frames/${frame.index}/detail` : null };
  }) };
  if (data.result) video.result = validatedResult(data.result, video);
  return video;
}

export function createVideoWorkflow({ fetcher = globalThis.fetch, storage = globalThis.localStorage } = {}) {
  let state = { video: null, filename: '', uploading: false, loadingVideo: false, analyzing: false, result: null,
    error: '', configError: '', configured: null, maxBytes: 50 * 1024 * 1024, maxSeconds: 30, maxFrames: 30 };
  let disposed = false;
  let initialized = false;
  let version = 0;
  const listeners = new Set();
  const controllers = new Set();
  const publish = patch => {
    if (disposed) return;
    state = { ...state, ...patch };
    listeners.forEach(listener => listener());
  };
  const remember = id => { try { if (id) storage?.setItem(STORAGE_KEY, id); else storage?.removeItem(STORAGE_KEY); } catch { /* 存储禁用不阻断使用。 */ } };
  const call = async (url, options = {}) => {
    const controller = new AbortController();
    controllers.add(controller);
    try { return await request(fetcher, url, { ...options, signal: controller.signal }); }
    finally { controllers.delete(controller); }
  };

  async function initialize() {
    if (initialized || disposed) return;
    initialized = true;
    const current = version;
    const config = async () => {
      try {
        const info = await call('/api/videos/status');
        publish({ configured: info.configured === true, maxBytes: info.max_bytes,
          maxSeconds: info.max_seconds, maxFrames: info.max_frames, configError: '' });
      } catch (error) { if (error.name !== 'AbortError') publish({ configError: errorMessage(error) }); }
    };
    const restore = async () => {
      let id;
      try { id = storage?.getItem(STORAGE_KEY); } catch { return; }
      if (!VIDEO_ID.test(id || '')) return;
      try {
        const video = validatedVideo(await call(`/api/videos/${id}`), state.maxFrames);
        if (current === version) publish({ video, result: video.result || null, filename: '上次的视频' });
      } catch (error) {
        if (current === version && error.name !== 'AbortError') publish({ error: `上次的视频未能恢复：${errorMessage(error)}` });
      }
    };
    await Promise.all([config(), restore()]);
  }

  async function selectFiles(input) {
    if (disposed || state.analyzing || state.uploading || state.loadingVideo) return false;
    const files = Array.from(input);
    if (files.length !== 1) { publish({ error: '请一次选择一个 MP4 视频。' }); return false; }
    const file = files[0];
    if (!/\.mp4$/i.test(file.name) || !file.size || file.size > state.maxBytes) {
      publish({ error: `请选择有效 MP4，大小不超过 ${Math.round(state.maxBytes / 1024 / 1024)} MB。` });
      return false;
    }
    const current = ++version;
    remember(null);
    publish({ filename: file.name, video: null, result: null, uploading: true, error: '' });
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const video = validatedVideo(await call('/api/videos', { method: 'POST', body: form }), state.maxFrames);
      if (current !== version || disposed) return false;
      publish({ video, result: video.result || null, uploading: false });
      remember(video.video_id);
      return true;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') publish({ uploading: false, error: errorMessage(error) });
      return false;
    }
  }

  async function loadVideo(id) {
    if (disposed || state.uploading || state.analyzing || !VIDEO_ID.test(id || '')) return false;
    const current = ++version;
    publish({ video: null, result: null, filename: '', loadingVideo: true, error: '' });
    try {
      const video = validatedVideo(await call(`/api/videos/${id}`), state.maxFrames);
      if (current !== version || disposed) return false;
      publish({ video, result: video.result || null, filename: '话题中的视频', loadingVideo: false });
      remember(id); return true;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') publish({ loadingVideo: false, error: errorMessage(error) });
      return false;
    }
  }

  async function analyze() {
    if (disposed || state.uploading || state.analyzing) return false;
    if (state.result) return true;
    if (!state.video || state.configured !== true) {
      publish({ error: state.video ? '模型尚未配置或配置状态未能读取。' : '请先上传一个短视频。' });
      return false;
    }
    const current = version;
    const video = state.video;
    publish({ analyzing: true, error: '' });
    try {
      const result = validatedResult(await call('/api/videos/analyze', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_id: video.video_id }) }), video);
      if (current !== version || disposed) return false;
      publish({ result, analyzing: false });
      return true;
    } catch (error) {
      if (current === version && error.name !== 'AbortError') publish({ analyzing: false,
        error: `${errorMessage(error)} 分析失败或超时也可能已有用量；系统不会自动重试。` });
      return false;
    }
  }

  return { getState: () => state, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize, selectFiles, analyze, loadVideo,
    clear() {
      if (disposed || state.uploading || state.loadingVideo || state.analyzing) return false;
      version++; remember(null); publish({ video: null, filename: '', result: null, error: '' }); return true;
    },
    dispose() { disposed = true; version++; controllers.forEach(controller => controller.abort()); listeners.clear(); },
  };
}
