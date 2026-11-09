export function validateAudioEvidence(audio, links = [], frames = [], duration = 30) {
  if (audio == null) {
    if (links?.length) throw new Error('音画依据缺少原始对白。');
    return;
  }
  const statuses = ['ready', 'no_audio', 'no_speech', 'unavailable', 'failed', 'legacy'];
  const validTime = time => Number.isFinite(time) && time >= 0 && time <= duration;
  if (!statuses.includes(audio.status) || typeof audio.message !== 'string' || !Array.isArray(audio.segments)
      || audio.segments.length > 64 || (audio.segments.length > 0) !== (audio.status === 'ready')) throw new Error('对白资料不完整。');
  audio.segments.forEach((segment, index) => {
    if (segment.id !== index || !validTime(segment.start) || !validTime(segment.end) || segment.end < segment.start
        || typeof segment.text !== 'string' || !segment.text.trim()
        || (index > 0 && segment.start < audio.segments[index - 1].start)) throw new Error('对白时间或内容无效。');
  });
  if (!Array.isArray(links) || links.length > 4) throw new Error('音画依据数量无效。');
  for (const link of links) {
    const segment = audio.segments.find(row => row.id === link.segment_id);
    if (!segment || !['supports', 'contradicts', 'uncertain', 'audio_only'].includes(link.relation)
        || typeof link.detail !== 'string' || !link.detail.trim()) throw new Error('音画依据缺少有效来源。');
    if (link.relation === 'audio_only') {
      if (link.frame_index != null || link.frame_time != null) throw new Error('纯对白依据不应包含画面。');
    } else {
      const frame = frames.find(row => row.index === link.frame_index);
      if (!frame || !validTime(link.frame_time) || !(frame.seen_at || [frame.time_seconds]).includes(link.frame_time)
          || Math.max(segment.start - link.frame_time, link.frame_time - segment.end, 0) > 3) throw new Error('音画引用不在有效时间范围内。');
    }
  }
}
