import { useRef, useState } from 'react';
import VideoChatPanel from './VideoChatPanel.jsx';

export function formatTime(seconds) {
  const tenths = Math.floor(seconds * 10 + 1e-6);
  return `${Math.floor(tenths / 600).toString().padStart(2, '0')}:${Math.floor(tenths / 10 % 60).toString().padStart(2, '0')}.${tenths % 10}`;
}

export default function VideoWorkspace({ workflow, state, chatWorkflow, chatState, draft, setDraft }) {
  const input = useRef(null);
  const player = useRef(null);
  const [position, setPosition] = useState('');
  const [playbackError, setPlaybackError] = useState('');
  const busy = state.uploading || state.loadingVideo || state.analyzing || chatState.sending || chatState.loading;
  const video = state.video;
  const result = chatState.videoId === video?.video_id ? chatState.videoContext || state.result : state.result;
  const keywords = result ? [...new Set(result.observations.flatMap(item => item.keywords))] : [];
  const jump = frame => {
    if (!player.current) return;
    player.current.currentTime = frame.time_seconds;
    setPosition(`已定位到 ${formatTime(frame.time_seconds)} 的采样画面`);
  };
  const chooseFiles = files => {
    setPosition(''); setPlaybackError('');
    void workflow.selectFiles(files);
  };

  return <div className="work-grid video-grid">
    <section className="panel video-panel" aria-labelledby="video-heading">
      <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="video-heading">你的视频</h2></div>
        {video && <button className="text-button" disabled={busy} onClick={() => { workflow.clear(); setPosition(''); setPlaybackError(''); }}>清空</button>}</div>
      <input type="file" ref={input} className="sr-only" tabIndex={-1} accept="video/mp4,.mp4" aria-label="选择视频文件" disabled={busy}
        onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) chooseFiles(files); }}/>
      <div className={`preview-zone video-preview ${video ? '' : 'is-empty'}`}
        onDragOver={event => event.preventDefault()}
        onDrop={event => { event.preventDefault(); if (!busy) chooseFiles(event.dataTransfer.files); }}>
        {video ? <video key={video.video_id} ref={player} controls playsInline preload="metadata" src={video.video_url}
          aria-label="上传的视频" onError={() => setPlaybackError('浏览器无法播放此视频，请尝试重新导出为 H.264 MP4。')}/>
          : <div className="empty-upload"><span className="empty-icon video-icon" aria-hidden="true">▷</span>
            <h3>{state.loadingVideo ? '正在恢复话题中的视频' : state.uploading ? '正在上传并提取画面' : '让小灵，看看这段风景'}</h3>
            <p>{state.uploading || state.loadingVideo ? '这一步在本机完成，不调用模型。' : '拖入一个短视频，或从设备中选择'}</p>
            {state.uploading || state.loadingVideo ? <span className="video-processing" role="status">本地处理中，请稍等…</span>
              : <button className="button button-primary" disabled={busy} onClick={() => input.current?.click()}>选择视频</button>}
            <span className="format-note">H.264 MP4 · 最长 {state.maxSeconds} 秒 · 最大 {Math.round(state.maxBytes / 1024 / 1024)} MB</span>
            <span className="format-note">整段均匀采样 {state.maxFrames} 次，短视频也采用相同采样数量</span>
          </div>}
      </div>
      {playbackError && <div className="notice notice-error" role="alert">{playbackError}</div>}
      <div className="image-footer"><div className="image-info"><span className={`status-dot ${video ? 'is-ready' : ''}`}/>
        <span className="filename" title={state.filename}>{state.filename || '尚未选择视频'}</span>
        {video && <span>{video.duration_seconds.toFixed(1)} 秒</span>}</div>
        {video && <button className="button button-secondary" disabled={busy} onClick={() => input.current?.click()}>更换视频</button>}</div>
      {video && <div className="video-sample-strip" aria-label="本地采样画面">{video.frames.map(frame => <button key={frame.index} className="sample-button" onClick={() => jump(frame)} aria-label={`查看 ${formatTime(frame.time_seconds)} 画面`}>
        <img src={frame.url} alt={`${formatTime(frame.time_seconds)} 采样截图`}/><span>{formatTime(frame.time_seconds)}</span></button>)}</div>}
      <div className="video-note" role="status">{position || (video ? `采样 ${video.sample_count} 次，合并为 ${video.frames.length} 张画面。点击截图可定位。` : '上传后不会自动调用模型。')}</div>
      {(state.error || state.configError) && <div className="notice notice-error" role="alert">{state.error || state.configError}</div>}
      {video && result && <details className="video-evidence"><summary>观察依据 · 点击展开</summary>
        <div className="video-keywords" aria-label="识别到的主要事物">{keywords.map(word => <span key={word}>{word}</span>)}</div>
        <p className="video-summary">{result.summary}</p>
        <p className="chat-history-note">时间为采样点，不代表精确起止；目前不分析音频。</p>
        {result.observations.map(item => {
          const frame = video.frames.find(candidate => candidate.index === item.frame_index);
          return frame && <button className="observation-card" key={item.frame_index} onClick={() => jump(frame)} aria-label={`回看 ${formatTime(frame.time_seconds)} ${item.keywords.join('、')}`}>
            <img src={frame.url} alt={item.keywords.join('、')}/><span className="observation-copy"><span className="observation-time">{formatTime(frame.time_seconds)}{item.uncertain && <em>待确认</em>}</span><strong>{item.keywords.join(' · ')}</strong><span>{item.description}</span></span>
          </button>;
        })}</details>}
    </section>
    <VideoChatPanel workflow={chatWorkflow} state={chatState} videoState={state} draft={draft} setDraft={setDraft}/>
  </div>;
}
