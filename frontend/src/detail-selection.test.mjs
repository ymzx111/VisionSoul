import test from 'node:test';
import assert from 'node:assert/strict';
import { selectedDetailFrames } from './detail-selection.mjs';
const frames = [0, 1, 2, 3, 4].map(index => ({ index, time_seconds: index + 0.5 }));

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
