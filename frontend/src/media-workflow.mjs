import { createImageWorkflow, validateFiles } from './workflow.mjs';
import { createVideoWorkflow } from './video-workflow.mjs';
import { createChatWorkflow } from './chat-workflow.mjs';

export const SINGLE_IMAGE_MESSAGE = '目前只支持单张图片上传';
export const VIDEO_DURATION_MESSAGE = '超过视频时长上限，上限为30秒';
export const MEDIA_ACCEPT = 'image/jpeg,image/png,image/webp,video/mp4,.jpg,.jpeg,.png,.webp,.mp4';
const kindOf = file => /\.(jpe?g|png|webp)$/i.test(file.name) ? 'image' : /\.mp4$/i.test(file.name) ? 'video' : null;

export function readVideoDuration(file, { signal } = {}) {
  if (typeof document === 'undefined') return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    const url = URL.createObjectURL(file);
    let timer;
    const cleanup = () => {
      clearTimeout(timer); signal?.removeEventListener('abort', abort);
      video.onloadedmetadata = video.onerror = null; video.removeAttribute('src'); video.load(); URL.revokeObjectURL(url);
    };
    const finish = value => { cleanup(); resolve(value); };
    const abort = () => { cleanup(); reject(new DOMException('Aborted', 'AbortError')); };
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, { once: true });
    video.preload = 'metadata'; video.onloadedmetadata = () => finish(video.duration); video.onerror = () => finish(null);
    timer = setTimeout(() => finish(null), 8000); video.src = url;
  });
}

/** 单一入口协调素材、话题和旧记录；原上传/分析服务仍分别复用。 */
export function createMediaWorkflow({ fetcher = globalThis.fetch, storage = globalThis.localStorage, durationReader = readVideoDuration, makeId, space } = {}) {
  const image = createImageWorkflow({ fetcher });
  const video = createVideoWorkflow({ fetcher, storage: null });
  const chat = createChatWorkflow({ fetcher, storage, makeId, mode: 'media', space });
  let own = { space, kind: null, file: null, restoredImageId: null, selecting: false, error: '', externalBusy: false };
  let snapshot;
  let disposed = false, reconciling = false, version = 0, syncedConversation = '';
  let durationController;
  const listeners = new Set();
  const publish = patch => {
    if (disposed) return;
    own = { ...own, ...patch };
    snapshot = { ...own, image: image.getState(), video: video.getState(), chat: chat.getState() };
    listeners.forEach(listener => listener());
  };
  const busy = () => own.selecting || own.externalBusy || image.getState().analyzing || image.getState().uploadStage === 'uploading'
    || video.getState().uploading || video.getState().loadingVideo || video.getState().analyzing || chat.getState().sending || chat.getState().loading;
  const reconcile = () => {
    const current = chat.getState();
    if (disposed || reconciling || own.selecting || current.loading || !current.conversationId) return;
    const key = `${current.conversationId}:${current.imageId || current.videoId || ''}`;
    if (key === syncedConversation) return;
    syncedConversation = key;
    if (current.videoId && own.kind === 'video' && video.getState().video?.video_id === current.videoId) return;
    if (current.imageId && own.kind === 'image' && image.getState().upload?.image_id === current.imageId) return;
    reconciling = true;
    image.clear(); video.clear();
    publish({ kind: current.videoId ? 'video' : current.imageId ? 'image' : null, restoredImageId: current.imageId, file: null, error: '' });
    if (current.videoId) void video.loadVideo(current.videoId);
    reconciling = false;
  };
  const subscriptions = [image.subscribe(() => publish({})), video.subscribe(() => publish({})), chat.subscribe(() => { publish({}); reconcile(); })];
  publish({});

  async function selectFiles(input) {
    if (disposed || busy()) return false;
    const files = Array.from(input);
    if (!files.length) return false;
    const kinds = files.map(kindOf);
    if (files.length > 1) {
      const photosOnly = files.every(file => file.type?.startsWith('image/') || /\.(jpe?g|png|webp|gif|bmp|heic|tiff?)$/i.test(file.name));
      publish({ error: photosOnly ? SINGLE_IMAGE_MESSAGE : '一次只能上传一张图片或一个视频' }); return false;
    }
    const [file] = files, kind = kinds[0];
    const invalid = !kind ? '请选择 JPG、PNG、WebP 图片或 H.264 MP4 视频。'
      : kind === 'image' ? validateFiles(files) : !file.size || file.size > video.getState().maxBytes ? '请选择不超过50 MB的有效MP4视频。' : null;
    if (invalid) { publish({ error: invalid }); return false; }
    const current = ++version;
    publish({ selecting: true, error: '' });
    try {
      if (kind === 'video') {
        durationController = new AbortController();
        const seconds = await durationReader(file, { signal: durationController.signal });
        if (disposed || current !== version) return false;
        if (Number.isFinite(seconds) && seconds > 30) { publish({ error: VIDEO_DURATION_MESSAGE }); return false; }
      }
      reconciling = true;
      chat.newConversation(); image.clear(); video.clear(); syncedConversation = '';
      publish({ kind, file, restoredImageId: null });
      reconciling = false;
      const ok = await (kind === 'image' ? image.selectFiles(files) : video.selectFiles(files));
      if (!ok && !disposed && current === version) publish({ error: kind === 'image' ? image.getState().selectionError : video.getState().error });
      return ok;
    } catch (error) {
      if (error.name !== 'AbortError') publish({ error: '素材暂时无法读取，请重新选择。' });
      return false;
    } finally {
      reconciling = false;
      if (!disposed && current === version) publish({ selecting: false });
    }
  }

  function clear() {
    if (disposed || busy()) return false;
    reconciling = true; version++; syncedConversation = '';
    chat.newConversation(); image.clear(); video.clear();
    publish({ kind: null, file: null, restoredImageId: null, error: '' });
    reconciling = false; return true;
  }

  async function send(message, { inspectVideo = false, debugCode = false, pythonTest = null } = {}) {
    if (disposed || busy() || own.error) return false;
    const current = chat.getState();
    const imageId = own.kind === 'image' ? image.getState().upload?.image_id || own.restoredImageId : null;
    const videoId = own.kind === 'video' ? video.getState().video?.video_id : null;
    if (own.kind && !(imageId || videoId)) return false;
    if ((inspectVideo || debugCode) && !videoId) return false;
    let content = message.trim();
    if (!content && !current.conversationId) {
      if (debugCode) content = '请结合视频中的源码和终端错误，给出有依据的可能原因与修改步骤。';
      else if (inspectVideo) content = '请概括视频画面与对白，分别说明能确认和不确定的内容。';
      else if (imageId) content = '请说明这张图片的主要内容和可见细节。';
    }
    return chat.send(content, imageId, { videoId, imageCount: imageId ? 1 : 0, uploadReady: true, inspectVideo, debugCode, pythonTest });
  }

  return { image, video, chat, getState: () => snapshot, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize: () => Promise.all([image.initialize(), video.initialize(), chat.initialize()]), selectFiles, clear, send, busy,
    dismissError() { publish({ error: '' }); }, setExternalBusy(value) { if (own.externalBusy !== value) publish({ externalBusy: value }); },
    loadConversation(id) { if (busy()) return false; return chat.loadConversation(id); },
    dispose() { disposed = true; version++; durationController?.abort(); subscriptions.forEach(unsubscribe => unsubscribe()); image.dispose(); video.dispose(); chat.dispose(); listeners.clear(); },
  };
}
