import test from 'node:test';
import assert from 'node:assert/strict';
import { validateAudioEvidence } from './audio-evidence.mjs';

const audio = { status: 'ready', message: '自动转写', segments: [{ id: 0, start: 10, end: 10.3, text: '看看这片叶子。' }] };
const frames = [{ index: 0, time_seconds: 0, seen_at: [0, 11] }];
const link = { segment_id: 0, frame_index: 0, frame_time: 11, relation: 'uncertain', detail: '可能有关，尚未确认。' };

test('valid delayed visual reference keeps original audio and repeated-frame times', () => {
  validateAudioEvidence(audio, [link], frames, 15);
  assert.equal(audio.segments[0].start, 10);
  assert.equal(link.frame_time, 11);
});
test('unknown speech, fabricated time and out-of-window reference cannot become seek links', () => {
  for (const change of [{ segment_id: 1 }, { frame_time: 11.1 }, { frame_time: 0 }]) {
    assert.throws(() => validateAudioEvidence(audio, [{ ...link, ...change }], frames, 15));
  }
});
test('audio-only and missing or silent audio remain explicit without false visual proof', () => {
  validateAudioEvidence(audio, [{ segment_id: 0, relation: 'audio_only', detail: '画面没有对应内容。' }], frames, 15);
  validateAudioEvidence(undefined, [], frames, 15);
  validateAudioEvidence({ status: 'no_speech', message: '无可靠对白', segments: [] }, [], frames, 15);
  assert.throws(() => validateAudioEvidence(audio, [{ ...link, relation: 'audio_only' }], frames, 15));
});
test('invalid transcript ranges and mismatched status are rejected', () => {
  assert.throws(() => validateAudioEvidence({ ...audio, status: 'no_speech' }, [], frames, 15));
  assert.throws(() => validateAudioEvidence({ ...audio, segments: [{ ...audio.segments[0], start: NaN }] }, [], frames, 15));
  assert.throws(() => validateAudioEvidence({ ...audio, segments: [{ ...audio.segments[0], end: 20 }] }, [], frames, 15));
});
