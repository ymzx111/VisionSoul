import { useEffect, useRef, useState } from 'react';
import VideoChatPanel from './VideoChatPanel.jsx';
import VideoComparison from './VideoComparison.jsx';
import AudioEvidence from './AudioEvidence.jsx';
import AudioEditor from './AudioEditor.jsx';
import VideoCodeDebug from './VideoCodeDebug.jsx';
import { analysisFrames, selectedDetailFrames } from './detail-selection.mjs';

export function formatTime(seconds) {
  const millis = Math.round(seconds * 1000);
  const fraction = String(millis % 1000).padStart(3, '0').replace(/0{1,2}$/, '');
  return `${Math.floor(millis / 60000).toString().padStart(2, '0')}:${Math.floor(millis / 1000 % 60).toString().padStart(2, '0')}.${fraction}`;
}

export default function VideoWorkspace({ workflow, state, chatWorkflow, chatState, draft, setDraft, uploadControls, chatPanel, onLocalBusyChange }) {
  const input = useRef(null);
  const player = useRef(null);
  const [position, setPosition] = useState('');
  const [playbackError, setPlaybackError] = useState('');
  const [audioBusy, setAudioBusy] = useState(false);
  const busy = audioBusy || uploadControls?.busy || state.uploading || state.loadingVideo || state.analyzing || chatState.sending || chatState.loading;
  useEffect(() => { onLocalBusyChange?.(audioBusy); return () => onLocalBusyChange?.(false); }, [audioBusy,onLocalBusyChange]);
  const video = state.video;
  const result = chatState.videoId === video?.video_id ? chatState.videoContext || state.result : state.result;
  const stale = !!result && (result.analysis_stale || (result.analysis_audio_revision ?? result.audio_revision ?? 0) !== (video?.audio_revision || 0));
  const keywords = result ? [...new Set(result.observations.flatMap(item => item.keywords))] : [];
  const detailFrames = selectedDetailFrames(result?.detail_selection, video?.frames || [], video?.video_id, video?.duration_seconds);
  const evidenceFrames = analysisFrames(result?.detail_selection, video?.frames || [], video?.video_id, video?.duration_seconds);
  const seek = seconds => {
    if (!player.current) return;
    player.current.currentTime = seconds;
    setPosition(`已定位到原视频 ${formatTime(seconds)}`);
  };
  const jump = frame => seek(frame.time_seconds);
  const chooseFiles = files => {
    setPosition(''); setPlaybackError('');
    void (uploadControls ? uploadControls.selectFiles(files) : workflow.selectFiles(files));
  };

  return <div className={`work-grid video-grid ${uploadControls ? 'unified-video-grid' : ''}`}>
    <section className="panel video-panel" aria-labelledby="video-heading">
      <div className="panel-heading"><div className="section-heading"><span className="step">01</span><h2 id="video-heading">{uploadControls ? '图片或视频' : '你的视频'}</h2></div>
        {uploadControls && video && <div className="media-picker-actions">
          <button className="text-button" disabled={busy} onClick={uploadControls.chooseImages}>换成图片</button>
          <button className="text-button" disabled={busy} onClick={uploadControls.chooseVideo}>更换视频</button>
        </div>}
        {video && <button className="text-button" disabled={busy} onClick={() => { (uploadControls || workflow).clear(); setPosition(''); setPlaybackError(''); }}>清空</button>}</div>
      <div className="video-panel-content">
      {stale && <p className="notice notice-warning" role="status">对白已校对，旧综合分析待更新。新追问优先使用校对白；重新读题、细看比对或代码排错会重读画面并产生 API 用量。</p>}
      <div className="video-media">
      {!uploadControls && <input type="file" ref={input} className="sr-only" tabIndex={-1} accept="video/mp4,.mp4" aria-label="选择视频文件" disabled={busy}
        onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) chooseFiles(files); }}/>}
      <div className={`preview-zone video-preview ${video ? '' : 'is-empty'}`}
        onDragOver={event => event.preventDefault()}
        onDrop={event => { event.preventDefault(); if (!busy) chooseFiles(event.dataTransfer.files); }}>
        {video ? <video key={video.video_id} ref={player} controls playsInline preload="metadata" src={video.video_url}
          aria-label="上传的视频" onError={() => setPlaybackError('浏览器无法播放此视频，请尝试重新导出为 H.264 MP4。')}/>
          : <div className="empty-upload"><span className="empty-icon video-icon" aria-hidden="true">▷</span>
            <h3>{state.loadingVideo ? '正在恢复话题中的视频' : state.uploading ? '正在处理视频' : '分享画面，也能细看比对'}</h3>
            <p>{state.uploading || state.loadingVideo ? '画面与对白在本机处理，不调用付费 API。' : '拖入一个短视频，或从设备中选择'}</p>
            {state.uploading || state.loadingVideo ? <span className="video-processing" role="status">本地处理中，生成兼容预览和识别对白可能需要一点时间…</span>
              : <button className="button button-primary" disabled={busy} onClick={() => uploadControls ? uploadControls.chooseVideo() : input.current?.click()}>选择视频</button>}
            <span className="format-note">MP4（H.264 / H.265）· 最长 {state.maxSeconds} 秒 · 最大 {Math.round(state.maxBytes / 1024 / 1024)} MB</span>
            <span className="format-note">每 {state.sampleInterval} 秒采样，补上末帧 · 最多 {state.maxFrames} 次</span>
          </div>}
      </div>
      {playbackError && <div className="notice notice-error" role="alert">{playbackError}</div>}
      <div className="image-footer"><div className="image-info"><span className={`status-dot ${video ? 'is-ready' : ''}`}/>
        <span className="filename" title={state.filename}>{state.filename || '尚未选择视频'}</span>
        {video && <span>{video.duration_seconds.toFixed(1)} 秒</span>}</div>
        {!uploadControls && video && <button className="button button-secondary" disabled={busy} onClick={() => input.current?.click()}>更换视频</button>}</div>
      {video && <div className="video-sample-strip" aria-label="本地采样画面">{video.frames.map(frame => <button key={frame.index} className="sample-button" onClick={() => jump(frame)} aria-label={`查看 ${formatTime(frame.time_seconds)} 画面`}>
        <img src={frame.url} alt={`${formatTime(frame.time_seconds)} 采样截图`}/><span>{formatTime(frame.time_seconds)}</span></button>)}</div>}
      <div className="video-note" role="status">{position || (video ? `${video.sampling_policy !== 'one_second' ? '旧版采样，重新上传可用1秒采样与对白识别。' : '每1秒采样并保留末帧。'}采样 ${video.sample_count} 次，合并为 ${video.frames.length} 张画面。` : '上传后不会自动调用付费模型。')}</div>
      {!!detailFrames.length && <details className="video-evidence detail-frame-evidence"><summary>高清关键帧 · {result.detail_selection.focus_range_ms ? '重点区间' : result.detail_selection.profile === 'text' ? '读题/代码优先' : '画面细节'} · {detailFrames.length}张</summary>
        <p className="chat-history-note">点击缩略图查看细节，点击时间回看原视频。锐化辅助图上的不确定字符请以原画面核对。</p>
        <div className="detail-frame-list">{detailFrames.map(frame => <div key={frame.index}>
          <a href={frame.detail_url || `/api/videos/${video.video_id}/frames/${frame.index}/detail`} target="_blank" rel="noreferrer"><img src={frame.detail_url || frame.url} alt={`${formatTime(frame.time_seconds)} 高清候选预览`}/></a>
          <button className="text-button" onClick={() => jump(frame)}>回看 {formatTime(frame.time_seconds)}</button>
          <span>{frame.adjustment === 'focus' ? '区间补抽' : frame.adjustment === 'nearby' ? '邻近原帧' : frame.adjustment === 'enhanced' ? '轻度锐化' : result.detail_selection.source_indices.includes(frame.index) ? '原视频高清' : '已保存细节图'}</span>
        </div>)}</div>
      </details>}
      {!uploadControls && (state.error || state.configError) && <div className="notice notice-error" role="alert">{state.error || state.configError}</div>}
      {video && result && <details className="video-evidence"><summary>观察依据 · 点击展开</summary>
        <div className="video-keywords" aria-label="识别到的主要事物">{keywords.map(word => <span key={word}>{word}</span>)}</div>
        <p className="video-summary">{result.summary}</p>
        <p className="chat-history-note">画面时间为采样点；对白和对应画面的时间分别保留。</p>
        {result.observations.map((item, observationIndex) => {
          const frame = evidenceFrames.find(candidate => candidate.index === item.frame_index);
          return frame && <button className="observation-card" key={`${item.frame_index}-${observationIndex}`} onClick={() => jump(frame)} aria-label={`回看 ${formatTime(frame.time_seconds)} ${item.keywords.join('、')}`}>
            <img src={frame.url} alt={item.keywords.join('、')}/><span className="observation-copy"><span className="observation-time">{formatTime(frame.time_seconds)}{item.uncertain && <em>待确认</em>}</span><strong>{item.keywords.join(' · ')}</strong><span>{item.description}</span></span>
          </button>;
        })}</details>}
      </div>
      {video && result?.comparison && <details className="video-evidence"><summary>比对依据</summary>
        <VideoComparison comparison={result.comparison} frames={evidenceFrames} onSelect={jump} formatTime={formatTime} question={chatState.videoId === video.video_id ? chatState.videoContext?.comparison_question : ''}/>
      </details>}
      {video && result?.code_debug && <details className="video-evidence"><summary>排错依据</summary>
        <VideoCodeDebug report={result.code_debug} frames={evidenceFrames} onSelect={jump} formatTime={formatTime} stale={stale}/>
      </details>}
      {video?.audio?.status === 'ready' && <AudioEditor videoId={video.video_id} duration={video.duration_seconds} disabled={busy} onBusyChange={setAudioBusy} onSaved={async () => {
        await workflow.loadVideo(video.video_id);
        if (chatState.videoId === video.video_id && chatState.conversationId) await chatWorkflow.loadConversation(chatState.conversationId);
      }}/>}
      {video && <AudioEvidence key={`${video.video_id}-${video.audio_revision || 0}`} audio={result?.current_audio || video.audio || result?.audio} links={stale ? [] : result?.audio_links || []} frames={evidenceFrames} duration={video.duration_seconds} onSeek={seek} formatTime={formatTime} soundClues={video.sound_clues} notes={stale ? [] : result?.evidence_notes || []} warnings={video.audio_warnings || result?.audio_warnings || []}/>}
      </div>
    </section>
    {chatPanel || <VideoChatPanel workflow={chatWorkflow} state={chatState} videoState={{ ...state, loadingVideo: state.loadingVideo || audioBusy }} draft={draft} setDraft={setDraft}/>}
  </div>;
}
