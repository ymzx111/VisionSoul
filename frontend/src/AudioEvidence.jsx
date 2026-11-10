import { useState } from 'react';
import { validateAudioEvidence } from './audio-evidence.mjs';

const relations = { supports: '模型判断：内容相符', contradicts: '模型判断：内容不一致', uncertain: '关联待确认', audio_only: '仅对白依据' };

export default function AudioEvidence({ audio, links = [], frames, duration, onSeek, formatTime, soundClues, notes = [], warnings = [] }) {
  const [showAll, setShowAll] = useState(false);
  if (!audio) return <p className="chat-history-note">旧视频尚未处理音频，重新上传可启用。</p>;
  try { validateAudioEvidence(audio, links, frames, duration); }
  catch { return <p className="notice notice-error">音画依据格式异常，暂不提供跳转；不会自动重新分析。</p>; }
  if (audio.status !== 'ready') return <p className="chat-history-note" role="status">{audio.message}</p>;
  return <details className="video-evidence audio-evidence">
    <summary>对白与音画依据 · {audio.segments.length}段</summary>
    <p className="chat-history-note">{audio.source === 'corrected' ? '当前为用户校对白。' : '自动转写可能有误。'}时间接近不代表内容已验证。点击时间回听。</p>
    {!!warnings.length && <p className="notice notice-warning">部分转写重复或与时长不符，分析时已标为待校对。请回听并使用“校对白”修正。</p>}
    {notes.map((note, index) => <p className="chat-history-note" key={index}>{note}</p>)}
    <div className="audio-evidence-scroll">
      {(showAll ? links : links.slice(0, 4)).map((link, index) => {
        const speech = audio.segments[link.segment_id];
        return <article className="audio-link-card" key={index}>
          <strong>{relations[link.relation]}</strong><p>{link.detail}</p>
          <button className="text-button" onClick={() => onSeek(speech.start)}>对白 {formatTime(speech.start)}—{formatTime(speech.end)}</button>
          {link.frame_time != null && <button className="text-button" onClick={() => onSeek(link.frame_time)}>画面 {formatTime(link.frame_time)}</button>}
        </article>;
      })}
      {links.length > 4 && <button className="text-button" aria-expanded={showAll} onClick={() => setShowAll(value => !value)}>{showAll ? '收起更多依据' : `展开其余${links.length - 4}条依据`}</button>}
      {audio.segments.map(speech => <div className="audio-transcript-row" key={speech.id}>
        <button className="text-button" onClick={() => onSeek(speech.start)} aria-label={`回听 ${formatTime(speech.start)} 对白`}>{formatTime(speech.start)}—{formatTime(speech.end)}</button>
        <p>{speech.text}</p>
        {warnings.some(row => row.segment_id === speech.id) && <p className="chat-history-note">此段转写待核对，不作可靠内容依据。</p>}
        {soundClues?.segments?.find(row => row.segment_id === speech.id) && (() => {
          const clue = soundClues.segments.find(row => row.segment_id === speech.id);
          const volume = { quieter: '音量相对较轻', louder: '音量相对较响', similar: '音量相近', not_compared: '单段不比较音量' }[clue.volume];
          return <p className="chat-history-note">{volume} · 转写密度约{clue.text_units_per_second}字词/秒{clue.gap_before != null ? ` · 与前句间隔约${clue.gap_before}秒` : ''}。仅供参考，不代表情绪。</p>;
        })()}
      </div>)}
    </div>
  </details>;
}
