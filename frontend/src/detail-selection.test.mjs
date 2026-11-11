import test from 'node:test';
import assert from 'node:assert/strict';
import { analysisFrames, selectedDetailFrames } from './detail-selection.mjs';
const frames = [0, 1, 2, 3, 4].map(index => ({ index, time_seconds: index + 0.5 }));

test('adjusted image and evidence use the actual original video time', () => {
  const id = 'a'.repeat(64);
  const selection = { profile: 'text', frame_indices: [0, 1], source_indices: [0, 1], adjustments: {
    0: { kind: 'nearby', time_seconds: 0.625 }, 1: { kind: 'enhanced', time_seconds: 1.5 },
  } };
  const evidence = analysisFrames(selection, frames, id, 5);
  assert.deepEqual(evidence[0].seen_at, [0.625]);
  assert.equal(evidence[0].url, `/api/videos/${id}/frames/0/detail?variant=nearby`);
  assert.equal(evidence[1].time_seconds, 1.5);
  assert.equal(evidence[1].adjustment, 'enhanced');
  assert.equal(frames[0].time_seconds, 0.5);
  assert.deepEqual(selectedDetailFrames({ ...selection, adjustments: { 0: { kind: 'nearby', time_seconds: 3 } } }, frames, id, 5), []);
});

test('old reports remain readable and valid text selections link only to sampled frames', () => {
  assert.deepEqual(selectedDetailFrames(undefined, frames), []);
  assert.deepEqual(selectedDetailFrames({ profile: 'text', frame_indices: [0, 3], source_indices: [3] }, frames), [frames[0], frames[3]]);
});

test('unknown, duplicated or over-budget detail references cannot become clickable evidence', () => {
  for (const selection of [
    { profile: 'text', frame_indices: [60], source_indices: [] },
    { profile: 'text', frame_indices: [0, 0], source_indices: [] },
    { profile: 'text', frame_indices: [0, 1, 2, 3, 4], source_indices: [] },
    { profile: 'text', frame_indices: [0], source_indices: [3] },
    { profile: 'text', frame_indices: [0], source_indices: [0, 0] },
    { profile: 'unknown', frame_indices: [0], source_indices: [] },
  ]) assert.deepEqual(selectedDetailFrames(selection, frames), []);
});
