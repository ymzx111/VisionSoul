import { useRef, useState } from 'react';
import { MEDIA_ACCEPT } from './media-workflow.mjs';
import MediaChatPanel from './MediaChatPanel.jsx';
import VideoWorkspace from './VideoWorkspace.jsx';

function ImageOrEmptyPanel({ workflow, state }) {
  const input = useRef(null);
  const [dragging, setDragging] = useState(false);
  const busy = workflow.busy();
  const imageId = state.image.upload?.image_id || state.restoredImageId;
  return <section className={`panel image-panel ${dragging ? 'is-dragging' : ''}`} aria-labelledby="media-heading"
    onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
    onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!busy) void workflow.selectFiles(event.dataTransfer.files); }}>
    <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="media-heading">图片或视频</h2></div>
      {state.kind && <button className="text-button" disabled={busy} onClick={() => workflow.clear()}>清空</button>}</div>
    <input ref={input} className="sr-only" tabIndex={-1} type="file" accept={MEDIA_ACCEPT} multiple aria-label="选择图片或视频" disabled={busy}
      onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) void workflow.selectFiles(files); }}/>
    <div className={`preview-zone ${imageId ? '' : 'is-empty'}`}>
      {imageId ? <img className="single-preview" src={`/api/images/${imageId}`} alt="当前分析图片"/> : <div className="empty-upload">
        <span className="empty-icon" aria-hidden="true">＋</span><h3>{state.selecting ? '正在准备素材…' : '上传图片或视频'}</h3>
        <p>一次一张图片，或一段最长30秒的视频</p>
        <button className="button button-primary" disabled={busy} onClick={() => input.current?.click()}>选择图片或视频</button>
        <span className="format-note">图片 JPG / PNG / WebP · 单张不超过10 MB</span>
        <span className="format-note">视频 H.264 MP4 · 最长30秒 · 不超过50 MB</span>
      </div>}
    </div>
    <div className="image-footer"><div className="image-info"><span className={`status-dot ${imageId ? 'is-ready' : ''}`}/>
      <span className="filename">{state.file?.name || (imageId ? '话题中的图片' : '尚未选择素材')}</span></div>
      {state.kind && <button className="button button-secondary" disabled={busy} onClick={() => input.current?.click()}>更换素材</button>}</div>
    <p className="media-upload-note">{imageId ? '图片已自动关联，直接分析或提问即可。' : '拖入图片或视频，也可以直接在右侧发送文字。'}上传不会自动调用付费模型。</p>
  </section>;
}

export default function MediaWorkspace({ workflow, state, draft, setDraft }) {
  const chatPanel = <MediaChatPanel workflow={workflow} state={state} draft={draft} setDraft={setDraft}/>;
  const error = state.error || (state.kind === 'video' ? state.video.error : state.image.selectionError) || state.image.configError || state.video.configError;
  return <>
    {error && <div className="notice notice-error media-error" role="alert"><span>{error}</span>
      {state.error && <button className="text-button" onClick={() => workflow.dismissError()}>关闭提示</button>}</div>}
    {state.selecting && <p className="media-progress" role="status">正在检查并处理素材，请稍候…</p>}
    {state.kind === 'video' ? <VideoWorkspace workflow={workflow.video} state={state.video} chatWorkflow={workflow.chat} chatState={state.chat}
      draft={draft} setDraft={setDraft} chatPanel={chatPanel} onLocalBusyChange={workflow.setExternalBusy}
      uploadControls={{ accept: MEDIA_ACCEPT, busy: workflow.busy(), selectFiles: workflow.selectFiles, clear: workflow.clear }}/>
      : <div className="work-grid"><ImageOrEmptyPanel workflow={workflow} state={state}/>{chatPanel}</div>}
  </>;
}
