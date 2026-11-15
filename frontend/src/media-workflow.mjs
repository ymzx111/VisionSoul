import { createImageWorkflow, IMAGE_LIMIT_MESSAGE, MAX_CHAT_IMAGES, uploadImage, validateFiles } from './workflow.mjs';
import { createVideoWorkflow } from './video-workflow.mjs';
import { createChatWorkflow } from './chat-workflow.mjs';

export const VIDEO_DURATION_MESSAGE = '超过视频时长上限，上限为30秒';
export const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp';
export const MEDIA_ACCEPT = IMAGE_ACCEPT + ',video/mp4,.mp4';
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
  const image = createImageWorkflow({ fetcher, maxImages: MAX_CHAT_IMAGES });
  const video = createVideoWorkflow({ fetcher, storage: null });
  const chat = createChatWorkflow({ fetcher, storage, makeId, mode: 'media', space });
  const emptyAttachments = { draftImages: [], attachmentError: '' };
  let own = { space, kind: null, file: null, restoredImageId: null, restoredImageIds: [],
    selecting: false, attaching: false, error: '', externalBusy: false, ...emptyAttachments };
  let snapshot;
  let disposed = false, reconciling = false, version = 0, syncedConversation = '';
  let durationController, attachmentController;
  const listeners = new Set();
  const publish = patch => {
    if (disposed) return;
    own = { ...own, ...patch };
    snapshot = { ...own, image: image.getState(), video: video.getState(), chat: chat.getState() };
    listeners.forEach(listener => listener());
  };
  const busy = () => own.selecting || own.attaching || own.externalBusy || image.getState().analyzing || image.getState().uploadStage === 'uploading'
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
    publish({ kind: current.videoId ? 'video' : current.imageId ? 'image' : null,
      restoredImageId: current.imageId, restoredImageIds: current.imageIds, file: null, error: '' });
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
    if (files.length > 1 && kinds.includes('video')) {
      publish({ error: '一次只能选择一段视频，或最多5张图片' }); return false;
    }
    const [file] = files, kind = files.length > 1 ? 'image' : kinds[0];
    const invalid = !kind ? '请选择 JPG、PNG、WebP 图片或 H.264/H.265 MP4 视频。'
      : kind === 'image' ? validateFiles(files, MAX_CHAT_IMAGES) : !file.size || file.size > video.getState().maxBytes ? '请选择不超过50 MB的有效MP4视频。' : null;
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
      publish({ kind, file, restoredImageId: null, restoredImageIds: [], ...emptyAttachments });
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

  async function selectAttachments(input) {
    if (disposed || busy()) return false;
    const files = Array.from(input);
    if (!files.length) return false;
    const invalid = own.draftImages.length + files.length > MAX_CHAT_IMAGES ? IMAGE_LIMIT_MESSAGE : validateFiles(files, MAX_CHAT_IMAGES);
    if (invalid) { publish({ attachmentError: invalid }); return false; }
    const controller = new AbortController();
    attachmentController = controller;
    publish({ attaching: true, attachmentError: '' });
    try {
      const uploaded = await Promise.all(files.map(async file => ({
        ...await uploadImage(fetcher, file, controller.signal), name: file.name,
      })));
      if (disposed) return false;
      const unique = new Map([...own.draftImages, ...uploaded].map(photo => [photo.image_id, photo]));
      publish({ draftImages: [...unique.values()] });
      return true;
    } catch (error) {
      controller.abort();
      if (error.name !== 'AbortError') publish({ attachmentError: error.message || '图片上传失败，请重试。' });
      return false;
    } finally {
      publish({ attaching: false });
    }
  }

  function clear() {
    if (disposed || busy()) return false;
    reconciling = true; version++; syncedConversation = '';
    chat.newConversation(); image.clear(); video.clear();
    publish({ kind: null, file: null, restoredImageId: null, restoredImageIds: [], error: '', ...emptyAttachments });
    reconciling = false; return true;
  }

  async function send(message, { inspectVideo = false, debugCode = false, pythonTest = null } = {}) {
    if (disposed || busy() || own.error) return false;
    const current = chat.getState();
    const mainImages = own.kind === 'image' ? (image.getState().uploads.length
      ? image.getState().uploads.map(photo => photo.image_id) : own.restoredImageIds) : [];
    const imageId = mainImages[0] || null;
    const draftIds = own.draftImages.map(photo => photo.image_id);
    const initialImages = !current.conversationId && (mainImages.length > 1 || draftIds.length) ? mainImages : [];
    const imageIds = [...new Set([...initialImages, ...draftIds])];
    if (imageIds.length > MAX_CHAT_IMAGES) {
      publish({ attachmentError: '这一轮最多发送5张图片。可先分析主素材，再补图。' }); return false;
    }
    const videoId = own.kind === 'video' ? video.getState().video?.video_id : null;
    if (own.kind && !(imageId || videoId)) return false;
    if ((inspectVideo || debugCode) && !videoId) return false;
    let content = message.trim();
    if (!content && !current.conversationId) {
      if (debugCode) content = '请结合视频中的源码和终端错误，给出有依据的可能原因与修改步骤。';
      else if (inspectVideo) content = '请概括视频画面与对白，分别说明能确认和不确定的内容。';
      else if (mainImages.length > 1) content = '请按上传顺序结合这组图片分析。若是编程题，请按照片中的条件交付完整程序。';
      else if (imageId) content = space === 'work'
        ? '请分析这张图片。如果是条件明确、要求编写完整程序的题目，请解题并交付可运行源码；其他图片请说明主要内容和可见细节。'
        : '请说明这张图片的主要内容和可见细节。';
    }
    const sent = await chat.send(content, imageId, { videoId, imageIds, imageCount: imageIds.length || (imageId ? 1 : 0),
      uploadReady: true, inspectVideo, debugCode, pythonTest });
    if (sent) publish(emptyAttachments);
    return sent;
  }

  return { image, video, chat, getState: () => snapshot, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    initialize: () => Promise.all([image.initialize(), video.initialize(), chat.initialize()]), selectFiles, selectAttachments, clear, send, busy,
    removeAttachment(id) { if (!busy()) publish({ draftImages: own.draftImages.filter(item => item.image_id !== id), attachmentError: '' }); },
    dismissError() { publish({ error: '' }); }, setExternalBusy(value) { if (own.externalBusy !== value) publish({ externalBusy: value }); },
    async loadConversation(id) { if (busy()) return false; const loaded = await chat.loadConversation(id); if (loaded) publish(emptyAttachments); return loaded; },
    dispose() { disposed = true; version++; durationController?.abort(); attachmentController?.abort(); subscriptions.forEach(unsubscribe => unsubscribe()); image.dispose(); video.dispose(); chat.dispose(); listeners.clear(); },
  };
}
