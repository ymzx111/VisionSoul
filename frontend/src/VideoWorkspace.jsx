import { useRef, useState } from 'react';

export function formatTime(seconds) {
  const tenths = Math.floor(seconds * 10 + 1e-6);
  return `${Math.floor(tenths / 600).toString().padStart(2, '0')}:${Math.floor(tenths / 10 % 60).toString().padStart(2, '0')}.${tenths % 10}`;
}

export default function VideoWorkspace({ workflow, state }) {
  const input = useRef(null);
  const player = useRef(null);
  const [position, setPosition] = useState('');
  const [playbackError, setPlaybackError] = useState('');
  const busy = state.uploading || state.analyzing;
  const video = state.video;
  const result = state.result;
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
            <h3>{state.uploading ? '正在上传并提取画面' : '让小灵，看看这段风景'}</h3>
            <p>{state.uploading ? '这一步在本机完成，不调用模型。' : '拖入一个短视频，或从设备中选择'}</p>
            {state.uploading ? <span className="video-processing" role="status">本地处理中，请稍等…</span>
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
    </section>

    <section className="panel video-result-panel" aria-labelledby="video-result-heading">
      <div className="panel-heading"><div className="section-heading"><span className="step">02</span><h2 id="video-result-heading">这一段，看见了什么？</h2></div><span className="soft-label">短视频观察</span></div>
      <div className="video-actions"><p>识别主要事物，按顺序整理，再听听小灵怎么说。</p>
        <button className="button button-primary" disabled={busy || !video || !!result || state.configured !== true} onClick={() => void workflow.analyze()}>
          {state.analyzing ? <><span className="spinner"/>小灵正在观察…</> : result ? '结果已保存' : '开始观察'}</button>
        <span className="cost-note">点击才调用 API · 成功结果自动复用</span></div>
      <div className="result-heading"><h3>{result ? '观察结果' : '等待观察'}</h3>
        {result && <span>{result.cached ? '复用已有结果 · 本次未调用模型' : result.usage ? `本次用量 ${result.usage.total_tokens} token` : '模型未返回用量'}</span>}</div>
      <div className="result-body video-results" aria-live="polite" aria-busy={state.analyzing}>
        {(state.error || state.configError || state.configured === false) && <div className="notice notice-error" role="alert">{state.error || state.configError || '模型尚未配置，请完成后端设置。'}</div>}
        {state.analyzing ? <div className="result-empty"><span className="result-orbit" aria-hidden="true">✧</span><h3>正在连接这一段画面</h3><p>采样画面合并为一次请求，<br/>不逐帧调用，不自动重试。</p></div>
          : result ? <>
            <article className="video-companion"><div className="answer-source"><span className="answer-avatar">✧</span>小灵<span>· 活泼俏皮，轻度幽默</span></div><p className="answer-text" data-testid="video-reply">{result.reply}</p></article>
            <div className="video-keywords" aria-label="识别到的主要事物">{keywords.map(word => <span key={word}>{word}</span>)}</div>
            <p className="video-summary">{result.summary}</p>
            <h3 className="timeline-heading">画面依据 <span>时间为采样点，不代表精确起止</span></h3>
            <div className="video-timeline">{result.observations.map(item => {
              const frame = video.frames.find(candidate => candidate.index === item.frame_index);
              return <button className="observation-card" key={item.frame_index} onClick={() => jump(frame)} aria-label={`回看 ${formatTime(frame.time_seconds)} ${item.keywords.join('、')}`}>
                <img src={frame.url} alt={item.keywords.join('、')}/><span className="observation-copy"><span className="observation-time">{formatTime(frame.time_seconds)}{item.uncertain && <em>待确认</em>}</span>
                  <strong>{item.keywords.join(' · ')}</strong><span>{item.description}</span></span><span className="jump-mark" aria-hidden="true">↗</span>
              </button>;
            })}</div>
          </> : !state.error && <div className="result-empty"><span className="result-orbit" aria-hidden="true">✧</span><h3>从月亮，到湖面</h3><p>上传视频，点击“开始观察”，<br/>把镜头里的发现留在这里。</p></div>}
      </div>
      <div className="analysis-footer video-limit-note"><span className="subtle-dot"/><span>整段均匀采样 {state.maxFrames} 次；不足则取实际画面，相同画面会合并。仅观察画面，可能漏掉短暂内容。</span></div>
    </section>
  </div>;
}
