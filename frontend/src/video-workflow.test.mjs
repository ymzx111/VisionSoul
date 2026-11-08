import test from 'node:test';
import assert from 'node:assert/strict';
import { createVideoWorkflow } from './video-workflow.mjs';

const id = 'a'.repeat(64);
const config = { configured: true, max_bytes: 50 * 1024 * 1024, max_seconds: 30, max_frames: 30 };
const info = () => ({ video_id: id, duration_seconds: 6, sample_count: 2,
  frames: [{ index: 0, time_seconds: 0, seen_at: [0] }, { index: 1, time_seconds: 3, seen_at: [3] }], result: null });
const answer = () => ({ video_id: id, observations: [{ frame_index: 1, keywords: ['水面'], description: '可见水面。', uncertain: false }],
  summary: '拍到了水面。', reply: '水面也收进镜头啦！', cached: false });
const response = body => ({ ok: true, json: async () => body });
const file = () => new File(['fake-local-video'], 'test.mp4', { type: 'video/mp4' });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test('upload never analyzes; explicit analysis sends once and reuses success', async () => {
  const calls = [];
  const workflow = createVideoWorkflow({ storage: null, fetcher: async (url, options) => {
    calls.push([url, options]);
    return response(url.endsWith('status') ? config : url.endsWith('analyze') ? answer() : info());
  } });
  await workflow.initialize();
  assert.equal(await workflow.selectFiles([file()]), true);
  assert.equal(calls.length, 2);
  assert.equal(workflow.getState().video.video_url, `/api/videos/${id}/file`);
  assert.equal(await workflow.analyze(), true);
  assert.equal(await workflow.analyze(), true);
  assert.equal(calls.filter(([url]) => url.endsWith('analyze')).length, 1);
  assert.deepEqual(JSON.parse(calls[2][1].body), { video_id: id });
});

test('rejects multiple files, wrong extension and oversized input locally', async () => {
  const workflow = createVideoWorkflow({ storage: null, fetcher: () => assert.fail('must not send') });
  assert.equal(await workflow.selectFiles([file(), file()]), false);
  assert.equal(await workflow.selectFiles([new File(['x'], 'bad.avi')]), false);
  assert.equal(await workflow.selectFiles([{ name: 'big.mp4', size: 51 * 1024 * 1024 }]), false);
});

test('double click and changing selection are blocked during paid request', async () => {
  const gate = deferred();
  let paidCalls = 0;
  const workflow = createVideoWorkflow({ storage: null, fetcher: async url => {
    if (url.endsWith('analyze')) { paidCalls++; return gate.promise; }
    return response(url.endsWith('status') ? config : info());
  } });
  await workflow.initialize(); await workflow.selectFiles([file()]);
  const pending = workflow.analyze();
  assert.equal(await workflow.analyze(), false);
  assert.equal(await workflow.selectFiles([file()]), false);
  assert.equal(workflow.clear(), false);
  gate.resolve(response(answer())); await pending;
  assert.equal(paidCalls, 1);
});

test('failure is not retried automatically and warns about possible usage', async () => {
  let count = 0;
  const workflow = createVideoWorkflow({ storage: null, fetcher: async url => {
    if (url.endsWith('analyze')) { count++; throw new TypeError('network'); }
    return response(url.endsWith('status') ? config : info());
  } });
  await workflow.initialize(); await workflow.selectFiles([file()]);
  assert.equal(await workflow.analyze(), false);
  assert.equal(count, 1);
  assert.match(workflow.getState().error, /可能已有用量/);
  assert.equal(workflow.getState().analyzing, false);
});

test('refresh restores saved result without model request', async () => {
  const calls = [];
  const workflow = createVideoWorkflow({ storage: { getItem: () => id }, fetcher: async url => {
    calls.push(url);
    return response(url.endsWith('status') ? config : { ...info(), result: { ...answer(), cached: true } });
  } });
  await workflow.initialize();
  assert.equal(workflow.getState().result.cached, true);
  assert.equal(await workflow.analyze(), true);
  assert.equal(calls.length, 2);
});

test('late restoration does not replace newly uploaded video', async () => {
  const gate = deferred();
  const newId = 'b'.repeat(64);
  const workflow = createVideoWorkflow({ storage: { getItem: () => id, removeItem() {}, setItem() {} }, fetcher: async url => {
    if (url.endsWith('status')) return response(config);
    if (url.endsWith(id)) return gate.promise;
    return response({ ...info(), video_id: newId });
  } });
  const initialized = workflow.initialize();
  await workflow.selectFiles([file()]);
  gate.resolve(response(info())); await initialized;
  assert.equal(workflow.getState().video.video_id, newId);
});

test('invalid evidence and missing video block result display and paid requests', async () => {
  let count = 0;
  const workflow = createVideoWorkflow({ storage: null, fetcher: async url => {
    if (url.endsWith('analyze')) { count++; return response({ ...answer(), observations: [{ ...answer().observations[0], frame_index: 19 }] }); }
    return response(url.endsWith('status') ? config : info());
  } });
  await workflow.initialize();
  assert.equal(await workflow.analyze(), false);
  assert.equal(count, 0);
  await workflow.selectFiles([file()]);
  assert.equal(await workflow.analyze(), false);
  assert.equal(workflow.getState().result, null);
  assert.equal(count, 1);
});

test('30-frame upload and evidence at index 29 are accepted with 30-second limits', async () => {
  const video = { ...info(), duration_seconds: 30, sample_count: 30,
    frames: Array.from({ length: 30 }, (_, index) => ({ index, time_seconds: index, seen_at: [index] })) };
  const result = { ...answer(), observations: [{ ...answer().observations[0], frame_index: 29 }] };
  const workflow = createVideoWorkflow({ storage: null, fetcher: async url => response(
    url.endsWith('status') ? config : url.endsWith('analyze') ? result : video) });
  await workflow.initialize();
  assert.equal(workflow.getState().maxSeconds, 30);
  assert.equal(workflow.getState().maxFrames, 30);
  assert.equal(await workflow.selectFiles([file()]), true);
  assert.equal(workflow.getState().video.frames[29].index, 29);
  assert.equal(await workflow.analyze(), true);
  assert.equal(workflow.getState().result.observations[0].frame_index, 29);
});

test('15-second 24fps videos accept all 30 sampled frames including indices 20 to 29', async () => {
  const video = { ...info(), duration_seconds: 15, sample_count: 30,
    frames: Array.from({ length: 30 }, (_, index) => {
      const time = Math.round(Math.ceil(index * 359 / 29) / 24 * 1000) / 1000;
      return { index, time_seconds: time, seen_at: [time] };
    }) };
  const workflow = createVideoWorkflow({ storage: null, fetcher: async url => response(url.endsWith('status') ? config : video) });
  await workflow.initialize();
  assert.equal(await workflow.selectFiles([file()]), true);
  assert.equal(workflow.getState().video.frames.length, 30);
  assert.equal(workflow.getState().video.frames[29].time_seconds, 14.958);
  assert.equal(workflow.getState().error, '');
});

test('index and timestamp errors have distinct messages without any paid request', async () => {
  for (const [frame, message] of [
    [{ ...info().frames[1], index: 20 }, /采样编号/],
    [{ ...info().frames[1], time_seconds: 7 }, /采样时间/],
    [{ ...info().frames[1], time_seconds: NaN }, /采样时间/],
  ]) {
    let calls = 0;
    const workflow = createVideoWorkflow({ storage: null, fetcher: async url => {
      assert.ok(!url.endsWith('analyze'));
      calls++;
      return response(url.endsWith('status') ? config : { ...info(), frames: [info().frames[0], frame] });
    } });
    await workflow.initialize();
    assert.equal(await workflow.selectFiles([file()]), false);
    assert.match(workflow.getState().error, message);
    assert.equal(calls, 2);
  }
});
