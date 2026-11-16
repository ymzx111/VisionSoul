import { useRef, useState } from 'react';
import { IMAGE_ACCEPT, VIDEO_ACCEPT } from './media-workflow.mjs';
import { MAX_CHAT_IMAGES } from './workflow.mjs';
import MediaChatPanel from './MediaChatPanel.jsx';
import VideoWorkspace from './VideoWorkspace.jsx';

function ImageOrEmptyPanel({ workflow, state, chooseImages, chooseVideo }) {
  const [dragging, setDragging] = useState(false);
  const busy = workflow.busy();
  const imageIds = state.image.uploads.length
    ? state.image.uploads.map(photo => photo.image_id) : state.restoredImageIds;
  const imageId = imageIds[0];
  return <section className={`panel image-panel ${dragging ? 'is-dragging' : ''}`} aria-labelledby="media-heading"
    onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
    onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) void workflow.selectFiles(event.dataTransfer.files); }}>
    <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="media-heading">图片或视频</h2></div>
      {state.kind && <button className="text-button" disabled={busy} onClick={() => workflow.clear()}>清空</button>}</div>
    <div className={`preview-zone ${imageId ? '' : 'is-empty'}`}>
      {imageIds.length > 1 ? <div className="media-photo-grid">{imageIds.map((id, index) =>
        <a href={`/api/images/${id}`} key={`${id}-${index}`} target="_blank" rel="noreferrer">
          <img src={`/api/images/${id}`} alt={`第 ${index + 1} 张主素材图片`}/>
        </a>)}</div> : imageId ? <img className="single-preview" src={`/api/images/${imageId}`} alt="当前分析图片"/> : <div className="empty-upload">
        <span className="empty-icon" aria-hidden="true">＋</span><h3>{state.selecting ? '正在准备素材…' : '上传图片或视频'}</h3>
        <p>按住 Ctrl 可一次选多张图片，最多 {MAX_CHAT_IMAGES} 张。</p>
        <div className="media-picker-actions">
          <button className="button button-primary" disabled={busy} onClick={chooseImages}>选择图片</button>
          <button className="button button-secondary" disabled={busy} onClick={chooseVideo}>选择视频</button>
        </div>
        <span className="format-note">图片 JPG / PNG / WebP · 单张不超过10 MB</span>
        <span className="format-note">视频 MP4（H.264 / H.265）· 最长30秒 · 不超过50 MB</span>
      </div>}
    </div>
    <div className="image-footer"><div className="image-info"><span className={`status-dot ${imageId ? 'is-ready' : ''}`}/>
      <span className="filename">{imageId ? `已上传 ${imageIds.length} / ${MAX_CHAT_IMAGES} 张图片` : state.file?.name || '尚未选择素材'}</span></div>
      {imageId && <div className="media-picker-actions">
        <button className="button button-secondary" disabled={busy} onClick={chooseImages}>重新选图</button>
        <button className="text-button" disabled={busy} onClick={chooseVideo}>换成视频</button>
      </div>}</div>
    <p className="media-upload-note">{imageId ? '图片已自动关联，直接分析或提问即可。' : '拖入图片或视频，也可以直接在右侧发送文字。'}上传不会自动调用付费模型。</p>
  </section>;
}

export default function MediaWorkspace({ workflow, state, draft, setDraft }) {
  const imageInput = useRef(null);
  const videoInput = useRef(null);
  const busy = workflow.busy();
  const chooseImages = () => imageInput.current?.click();
  const chooseVideo = () => videoInput.current?.click();
  const onSelect = event => {
    const files = Array.from(event.currentTarget.files);
    event.currentTarget.value = '';
    if (files.length) void workflow.selectFiles(files);
  };
  const chatPanel = <MediaChatPanel workflow={workflow} state={state} draft={draft} setDraft={setDraft}/>;
  const error = state.error || (state.kind === 'video' ? state.video.error : state.image.selectionError) || state.image.configError || state.video.configError;
  return <>
    <input ref={imageInput} className="sr-only" tabIndex={-1} type="file" accept={IMAGE_ACCEPT} multiple aria-label="选择图片文件" disabled={busy} onChange={onSelect}/>
    <input ref={videoInput} className="sr-only" tabIndex={-1} type="file" accept={VIDEO_ACCEPT} aria-label="选择视频文件" disabled={busy} onChange={onSelect}/>
    {error && <div className="notice notice-error media-error" role="alert"><span>{error}</span>
      {state.error && <button className="text-button" onClick={() => workflow.dismissError()}>关闭提示</button>}</div>}
    {state.selecting && <p className="media-progress" role="status">正在检查并处理素材，请稍候…</p>}
    {state.kind === 'video' ? <VideoWorkspace workflow={workflow.video} state={state.video} chatWorkflow={workflow.chat} chatState={state.chat}
      draft={draft} setDraft={setDraft} chatPanel={chatPanel} onLocalBusyChange={workflow.setExternalBusy}
      uploadControls={{ busy, chooseImages, chooseVideo, selectFiles: workflow.selectFiles, clear: workflow.clear }}/>
      : <div className="work-grid"><ImageOrEmptyPanel workflow={workflow} state={state} chooseImages={chooseImages} chooseVideo={chooseVideo}/>{chatPanel}</div>}
  </>;
}
