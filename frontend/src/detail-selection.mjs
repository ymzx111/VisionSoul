/** 旧记录没有选帧信息时不显示；损坏记录不得产生错误的高清链接。 */
export function selectedDetailFrames(selection, frames, videoId, duration = 30) {
  if (!selection || !['general', 'text'].includes(selection.profile) || !Array.isArray(selection.frame_indices)
      || selection.frame_indices.length > 4 || new Set(selection.frame_indices).size !== selection.frame_indices.length
      || !Array.isArray(selection.source_indices) || new Set(selection.source_indices).size !== selection.source_indices.length
      || selection.source_indices.some(index => !selection.frame_indices.includes(index))) return [];
  if (selection.focus_range_ms != null) {
    if (!Array.isArray(selection.focus_range_ms) || selection.focus_range_ms.length !== 2) return [];
    const [start, end] = selection.focus_range_ms;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > duration * 1000
        || start >= end || end - start > 5000 || !/^[0-9a-f]{64}$/.test(videoId || '')) return [];
    const times = selection.focus_frames;
    if (!times || typeof times !== 'object' || Array.isArray(times) || Object.keys(times).length !== selection.frame_indices.length
        || new Set(Object.values(times)).size !== selection.frame_indices.length
        || selection.source_indices.length !== selection.frame_indices.length) return [];
    const focused = [];
    for (const index of selection.frame_indices) {
      const time = times[index];
      const original = frames.find(frame => frame.index === index);
      if (!Number.isInteger(index) || index < 0 || index > 64 || !Number.isInteger(time) || time < start || time > end
          || (index < 61 && !original) || (original && original.time_seconds !== time / 1000)) return [];
      const url = `/api/videos/${videoId}/focus-frames/${time}`;
      focused.push({ index, time_seconds: time / 1000, seen_at: [time / 1000], url, detail_url: url, adjustment: 'focus' });
    }
    return focused.sort((left, right) => left.time_seconds - right.time_seconds);
  }
  const selected = selection.frame_indices.map(index => frames.find(frame => frame.index === index));
  if (selected.some(frame => !frame || !Number.isFinite(frame.time_seconds))) return [];
  const adjustments = selection.adjustments ?? {};
  if (typeof adjustments !== 'object' || Array.isArray(adjustments)) return [];
  for (const [index, adjustment] of Object.entries(adjustments)) {
    const frame = selected.find(item => String(item.index) === index);
    if (!frame || !selection.source_indices.includes(frame.index) || !adjustment
        || !['nearby', 'enhanced'].includes(adjustment.kind) || !Number.isFinite(adjustment.time_seconds)
        || adjustment.time_seconds < 0 || adjustment.time_seconds > duration) return [];
    const offset = Math.abs(adjustment.time_seconds - frame.time_seconds);
    if (adjustment.kind === 'nearby' ? offset <= 0 || offset > 0.501 : offset !== 0) return [];
  }
  if (!/^[0-9a-f]{64}$/.test(videoId || '')) return selected;
  return selected.map(frame => {
    const adjustment = adjustments[frame.index];
    if (!adjustment) return frame;
    const url = `/api/videos/${videoId}/frames/${frame.index}/detail?variant=${adjustment.kind}`;
    return { ...frame, url, detail_url: url, adjustment: adjustment.kind,
      time_seconds: adjustment.time_seconds,
      seen_at: adjustment.kind === 'nearby' ? [adjustment.time_seconds] : frame.seen_at };
  });
}

export function analysisFrames(selection, frames, videoId, duration) {
  const adjusted = new Map(selectedDetailFrames(selection, frames, videoId, duration)
    .filter(frame => frame.adjustment).map(frame => [frame.index, frame]));
  const originals = frames.map(frame => adjusted.get(frame.index) || frame);
  const additional = [...adjusted.values()].filter(frame => !frames.some(original => original.index === frame.index));
  return [...originals, ...additional];
}
