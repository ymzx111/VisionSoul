import test from 'node:test';
import assert from 'node:assert/strict';
import { roleText, freeProfile } from './role-import.mjs';

test('legacy cards stay readable without mutating stored fields', () => {
  const old={name:'小灵',traits:'俏皮',background:'观察自然'};
  assert.equal(roleText(old),'性格：俏皮\n\n背景：观察自然');
  const next=freeProfile(old);
  assert.equal(next.mode,'free'); assert.equal(next.definition,roleText(old));
  assert.equal(next.traits,''); assert.equal(old.traits,'俏皮');
});
test('natural prose, Markdown and JSON are preserved verbatim without schema requirements', () => {
  for (const text of ['  # 世界观\n她来自遥远的月港。\n\n- 对话示例：“晚上好。”  ', '{"自定字段":"随意组织"}', '背景'.repeat(500), '']) {
    assert.equal(roleText(freeProfile({name:'望舒'},text)),text);
  }
});
test('technical capacity accepts long and Unicode definitions without silent truncation', () => {
  assert.equal(freeProfile({name:'新人物'},'😀'.repeat(20000)).definition.length,40000);
  assert.throws(()=>freeProfile({},'字'.repeat(20001)),/容量/);
  assert.throws(()=>freeProfile({},{}),/文字/);
});
