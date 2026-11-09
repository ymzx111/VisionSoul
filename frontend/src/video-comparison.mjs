export function validateComparison(value, frames) {
  if (value == null) return null;
  const text = value => typeof value === 'string' && value.trim();
  if (!['plant','place','object','scene'].includes(value.category) || !['likely','insufficient'].includes(value.verdict)
      || value.basis !== 'visual_inference' || !text(value.target) || !text(value.conclusion) || !text(value.next_shot)
      || !Array.isArray(value.features) || value.features.length > 5 || !Array.isArray(value.candidates) || value.candidates.length > 3
      || !Array.isArray(value.missing_features) || !value.missing_features.every(text)) throw new Error('比对资料格式不完整。');
  if (value.verdict === 'likely' && !value.candidates.length) throw new Error('比对资料缺少候选。');
  for (const feature of value.features) {
    if (!frames.some(frame => frame.index === feature.frame_index) || !text(feature.detail)) throw new Error('比对依据缺少对应画面。');
  }
  for (const candidate of value.candidates) {
    if (!text(candidate.name) || !text(candidate.differences) || !Array.isArray(candidate.supporting_features) || !candidate.supporting_features.length
        || !candidate.supporting_features.every(index => Number.isInteger(index) && index >= 0 && index < value.features.length)) throw new Error('候选缺少有效特征依据。');
  }
  return value;
}
