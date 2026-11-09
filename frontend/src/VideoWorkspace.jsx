import { useEffect, useRef, useState } from 'react';
import VideoChatPanel from './VideoChatPanel.jsx';
import VideoComparison from './VideoComparison.jsx';
import AudioEvidence from './AudioEvidence.jsx';
import AudioEditor from './AudioEditor.jsx';

export function formatTime(seconds) {
  const tenths = Math.floor(seconds * 10 + 1e-6);
  return `${Math.floor(tenths / 600).toString().padStart(2, '0')}:${Math.floor(tenths / 10 % 60).toString().padStart(2, '0')}.${tenths % 10}`;
}

export default function VideoWorkspace({ workflow, state, chatWorkflow, chatState, draft, setDraft }) {
  const input = useRef(null);
  const player = useRef(null);
  const [position, setPosition] = useState('');
  const [playbackError, setPlaybackError] = useState('');
  const [view, setView] = useState('video');
  const [audioBusy, setAudioBusy] = useState(false);
  const busy = audioBusy || state.uploading || state.loadingVideo || state.analyzing || chatState.sending || chatState.loading;
  const video = state.video;
  const result = chatState.videoId === video?.video_id ? chatState.videoContext || state.result : state.result;
  const stale = !!result && (result.analysis_stale || (result.analysis_audio_revision ?? result.audio_revision ?? 0) !== (video?.audio_revision || 0));
  const keywords = result ? [...new Set(result.observations.flatMap(item => item.keywords))] : [];
  useEffect(() => { setView(result?.comparison ? 'comparison' : 'video'); }, [result?.comparison,video?.video_id]);
  const seek = seconds => {
    if (!player.current) return;
    setView('video');
    player.current.currentTime = seconds;
    setPosition(`已定位到原视频 ${formatTime(seconds)}`);
  };
  const jump = frame => seek(frame.time_seconds);
  const chooseFiles = files => {
    setPosition(''); setPlaybackError('');
    void workflow.selectFiles(files);
  };

  return <div className="work-grid video-grid">
    <section className="panel video-panel" aria-labelledby="video-heading">
      <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="video-heading">你的视频</h2></div>
        {video && <div className="video-view-switch" aria-label="视频查看内容"><button aria-pressed={view === 'video'} onClick={() => setView('video')}>画面</button><button aria-pressed={view === 'comparison'} disabled={!result?.comparison} onClick={() => setView('comparison')}>比对</button></div>}
        {video && <button className="text-button" disabled={busy} onClick={() => { workflow.clear(); setPosition(''); setPlaybackError(''); }}>清空</button>}</div>
      <div className="video-panel-content">
      {stale && <p className="notice notice-warning" role="status">对白已校对，旧综合分析待更新。新追问优先使用校对白；点击“细看并比对”才会重新读取画面并产生 API 用量。</p>}
      <div className="video-media" hidden={view === 'comparison'}>
      <input type="file" ref={input} className="sr-only" tabIndex={-1} accept="video/mp4,.mp4" aria-label="选择视频文件" disabled={busy}
        onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) chooseFiles(files); }}/>
      <div className={`preview-zone video-preview ${video ? '' : 'is-empty'}`}
        onDragOver={event => event.preventDefault()}
        onDrop={event => { event.preventDefault(); if (!busy) chooseFiles(event.dataTransfer.files); }}>
        {video ? <video key={video.video_id} ref={player} controls playsInline preload="metadata" src={video.video_url}
          aria-label="上传的视频" onError={() => setPlaybackError('浏览器无法播放此视频，请尝试重新导出为 H.264 MP4。')}/>
          : <div className="empty-upload"><span className="empty-icon video-icon" aria-hidden="true">▷</span>
            <h3>{state.loadingVideo ? '正在恢复话题中的视频' : state.uploading ? '正在提取画面和识别对白' : '分享画面，也能细看比对'}</h3>
            <p>{state.uploading || state.loadingVideo ? '画面与对白在本机处理，不调用付费 API。' : '拖入一个短视频，或从设备中选择'}</p>
            {state.uploading || state.loadingVideo ? <span className="video-processing" role="status">本地处理中；对白识别可能需要一些时间…</span>
              : <button className="button button-primary" disabled={busy} onClick={() => input.current?.click()}>选择视频</button>}
            <span className="format-note">H.264 MP4 · 最长 {state.maxSeconds} 秒 · 最大 {Math.round(state.maxBytes / 1024 / 1024)} MB</span>
            <span className="format-note">每 {state.sampleInterval} 秒采样，补上末帧 · 最多 {state.maxFrames} 次</span>
          </div>}
      </div>
      {playbackError && <div className="notice notice-error" role="alert">{playbackError}</div>}
      <div className="image-footer"><div className="image-info"><span className={`status-dot ${video ? 'is-ready' : ''}`}/>
        <span className="filename" title={state.filename}>{state.filename || '尚未选择视频'}</span>
        {video && <span>{video.duration_seconds.toFixed(1)} 秒</span>}</div>
        {video && <button className="button button-secondary" disabled={busy} onClick={() => input.current?.click()}>更换视频</button>}</div>
      {video && <div className="video-sample-strip" aria-label="本地采样画面">{video.frames.map(frame => <button key={frame.index} className="sample-button" onClick={() => jump(frame)} aria-label={`查看 ${formatTime(frame.time_seconds)} 画面`}>
        <img src={frame.url} alt={`${formatTime(frame.time_seconds)} 采样截图`}/><span>{formatTime(frame.time_seconds)}</span></button>)}</div>}
      <div className="video-note" role="status">{position || (video ? `${video.sampling_policy !== 'one_second' ? '旧版采样，重新上传可用1秒采样与对白识别。' : '每1秒采样并保留末帧。'}采样 ${video.sample_count} 次，合并为 ${video.frames.length} 张画面。` : '上传后不会自动调用付费模型。')}</div>
      {(state.error || state.configError) && <div className="notice notice-error" role="alert">{state.error || state.configError}</div>}
      {video && result && <details className="video-evidence"><summary>观察依据 · 点击展开</summary>
        <div className="video-keywords" aria-label="识别到的主要事物">{keywords.map(word => <span key={word}>{word}</span>)}</div>
        <p className="video-summary">{result.summary}</p>
        <p className="chat-history-note">画面时间为采样点；对白和对应画面的时间分别保留。</p>
        {result.observations.map((item, observationIndex) => {
          const frame = video.frames.find(candidate => candidate.index === item.frame_index);
          return frame && <button className="observation-card" key={`${item.frame_index}-${observationIndex}`} onClick={() => jump(frame)} aria-label={`回看 ${formatTime(frame.time_seconds)} ${item.keywords.join('、')}`}>
            <img src={frame.url} alt={item.keywords.join('、')}/><span className="observation-copy"><span className="observation-time">{formatTime(frame.time_seconds)}{item.uncertain && <em>待确认</em>}</span><strong>{item.keywords.join(' · ')}</strong><span>{item.description}</span></span>
          </button>;
        })}</details>}
      </div>
      {view === 'comparison' && video && result?.comparison && <VideoComparison comparison={result.comparison} frames={video.frames} onSelect={jump} formatTime={formatTime} question={chatState.videoId === video.video_id ? chatState.videoContext?.comparison_question : ''}/>}
      {video?.audio?.status === 'ready' && <AudioEditor videoId={video.video_id} duration={video.duration_seconds} disabled={busy} onBusyChange={setAudioBusy} onSaved={async () => {
        await workflow.loadVideo(video.video_id);
        if (chatState.videoId === video.video_id && chatState.conversationId) await chatWorkflow.loadConversation(chatState.conversationId);
      }}/>}
      {video && <AudioEvidence audio={result?.current_audio || video.audio || result?.audio} links={stale ? [] : result?.audio_links || []} frames={video.frames} duration={video.duration_seconds} onSeek={seek} formatTime={formatTime} soundClues={video.sound_clues}/>}
      </div>
    </section>
    <VideoChatPanel workflow={chatWorkflow} state={chatState} videoState={{ ...state, loadingVideo: state.loadingVideo || audioBusy }} draft={draft} setDraft={setDraft}/>
  </div>;
}
