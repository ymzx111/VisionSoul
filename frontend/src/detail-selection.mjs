/** 旧记录没有选帧信息时不显示；损坏记录不得产生错误的高清链接。 */
export function selectedDetailFrames(selection, frames) {
  if (!selection || !['general', 'text'].includes(selection.profile) || !Array.isArray(selection.frame_indices)
      || selection.frame_indices.length > 4 || new Set(selection.frame_indices).size !== selection.frame_indices.length
      || !Array.isArray(selection.source_indices) || new Set(selection.source_indices).size !== selection.source_indices.length
      || selection.source_indices.some(index => !selection.frame_indices.includes(index))) return [];
  const selected = selection.frame_indices.map(index => frames.find(frame => frame.index === index));
  return selected.some(frame => !frame || !Number.isFinite(frame.time_seconds)) ? [] : selected;
}
