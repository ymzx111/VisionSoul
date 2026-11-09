import { validateAudioEvidence } from './audio-evidence.mjs';

const relations = { supports: '模型判断：内容相符', contradicts: '模型判断：内容不一致', uncertain: '关联待确认', audio_only: '仅对白依据' };

export default function AudioEvidence({ audio, links = [], frames, duration, onSeek, formatTime }) {
  if (!audio) return <p className="chat-history-note">旧视频尚未处理音频，重新上传可启用。</p>;
  try { validateAudioEvidence(audio, links, frames, duration); }
  catch { return <p className="notice notice-error">音画依据格式异常，暂不提供跳转；不会自动重新分析。</p>; }
  if (audio.status !== 'ready') return <p className="chat-history-note" role="status">{audio.message}</p>;
  return <details className="video-evidence audio-evidence">
    <summary>对白与音画依据 · {audio.segments.length}段</summary>
    <p className="chat-history-note">自动转写可能有误；时间接近不代表内容已验证。点击时间回听。</p>
    <div className="audio-evidence-scroll">
      {links.map((link, index) => {
        const speech = audio.segments[link.segment_id];
        return <article className="audio-link-card" key={index}>
          <strong>{relations[link.relation]}</strong><p>{link.detail}</p>
          <button className="text-button" onClick={() => onSeek(speech.start)}>对白 {formatTime(speech.start)}—{formatTime(speech.end)}</button>
          {link.frame_time != null && <button className="text-button" onClick={() => onSeek(link.frame_time)}>画面 {formatTime(link.frame_time)}</button>}
        </article>;
      })}
      {audio.segments.map(speech => <div className="audio-transcript-row" key={speech.id}>
        <button className="text-button" onClick={() => onSeek(speech.start)} aria-label={`回听 ${formatTime(speech.start)} 对白`}>{formatTime(speech.start)}—{formatTime(speech.end)}</button>
        <p>{speech.text}</p>
      </div>)}
    </div>
  </details>;
}
