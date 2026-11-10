export function validateCodeDebug(report, frames) {
  if (report == null) return;
  if (!['likely', 'insufficient'].includes(report.status) || !Array.isArray(report.evidence) || report.evidence.length > 4
      || !Array.isArray(report.causes) || report.causes.length > 3 || !Array.isArray(report.steps) || report.steps.length > 5
      || !Array.isArray(report.missing) || report.missing.length > 3) throw new Error('排错报告格式无效。');
  if ((report.status === 'likely' && (!report.evidence.length || !report.causes.length))
      || (report.status === 'insufficient' && !report.missing.length)) throw new Error('排错结论缺少依据或补充要求。');
  for (const item of report.evidence) {
    if (!frames.some(frame => frame.index === item.frame_index) || !['code','error','context'].includes(item.kind)
        || typeof item.excerpt !== 'string' || !item.excerpt.trim()
        || (item.line_number != null && (!Number.isInteger(item.line_number) || item.line_number < 1))) throw new Error('排错报告缺少有效画面依据。');
  }
  for (const cause of report.causes) {
    if (typeof cause.hypothesis !== 'string' || typeof cause.check !== 'string' || !Array.isArray(cause.evidence_ids)
        || !cause.evidence_ids.length || cause.evidence_ids.some(id => !Number.isInteger(id) || id < 0 || id >= report.evidence.length)) throw new Error('原因引用无效。');
  }
  if (report.steps.concat(report.missing).some(text => typeof text !== 'string' || !text.trim())
      || (report.suggested_code != null && typeof report.suggested_code !== 'string')
      || (report.visible_error != null && typeof report.visible_error !== 'string')) throw new Error('排错内容无效。');
}

