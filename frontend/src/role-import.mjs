// 仅用于兼容旧卡；新人物直接使用整段文字，不解析或改写内容。
const LEGACY_FIELDS = [['identity','身份定位'],['traits','性格'],['background','背景'],
  ['speaking_style','说话方式'],['interests','兴趣'],['relationship','相处方式'],['avoidances','其他设定']];
export const ROLE_TEXT_LIMIT = 20000;
export function roleText(profile) {
  if (profile.mode === 'free') return profile.definition || '';
  return LEGACY_FIELDS.filter(([key]) => profile[key]).map(([key,label]) => `${label}：${profile[key]}`).join('\n\n');
}
export function freeProfile(profile, text = roleText(profile)) {
  if (typeof text !== 'string') throw new Error('请粘贴人物设定文字。');
  if ([...text].length > ROLE_TEXT_LIMIT) throw new Error('内容超过当前单次设定的容量，请先精简到两万字以内。');
  return { name: profile.name || '未命名角色', mode: 'free', definition: text,
    ...Object.fromEntries(LEGACY_FIELDS.map(([key]) => [key,''])) };
}
