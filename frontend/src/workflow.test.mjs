import test from 'node:test';
import assert from 'node:assert/strict';
import { createImageWorkflow } from './workflow.mjs';

const firstId = '1'.repeat(32);
const secondId = '2'.repeat(32);
const file = name => new File(['image-bytes'], name, { type: 'image/png' });
const uploaded = image_id => ({ image_id, url: `/api/images/${image_id}`, width: 24, height: 16 });
const reply = (body, status = 200) => ({ ok: status >= 200 && status < 300, json: async () => body });
const answered = image_id => ({ image_id, answer: '这是测试回复。', model: 'deepseek-flash', usage: { total_tokens: 12 }, truncated: false });
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('single upload is linked automatically, and only explicit analysis calls the model', async () => {
  const calls = [];
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: async (url, options) => {
    calls.push({ url, options });
    return reply(url === '/api/images' ? uploaded(firstId) : answered(firstId));
  } });
  await workflow.selectFiles([file('photo.png')]);
  assert.equal(workflow.getState().upload.image_id, firstId);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.body.get('file').name, 'photo.png');
  await workflow.analyze('  图片是什么颜色？  ');
  assert.deepEqual(JSON.parse(calls[1].options.body), { image_id: firstId, question: '图片是什么颜色？' });
  assert.equal(workflow.getState().result.answer, '这是测试回复。');
});

test('multiple files are preview only, cannot call model, and removing to one uploads that exact file', async () => {
  const calls = [];
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: async (url, options) => {
    calls.push({ url, options }); return reply(uploaded(secondId));
  } });
  await workflow.selectFiles([file('one.png'), file('two.png')]);
  assert.equal(workflow.getState().files.length, 2);
  assert.equal(workflow.getState().uploadStage, 'multiple');
  assert.equal(calls.length, 0);
  assert.equal(await workflow.analyze('描述图片'), false);
  assert.match(workflow.getState().analysisError, /目前只能分析单个图像/);
  assert.equal(calls.length, 0);
  await workflow.removeFile(workflow.getState().files[0].id);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.body.get('file').name, 'two.png');
  assert.equal(workflow.getState().upload.image_id, secondId);
});

test('no image and upload in progress cannot trigger analysis', async () => {
  const pending = deferred(); const calls = [];
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: async url => { calls.push(url); return pending.promise; } });
  await workflow.analyze('描述图片');
  assert.equal(calls.length, 0);
  const upload = workflow.selectFiles([file('one.png')]);
  await workflow.analyze('描述图片');
  assert.deepEqual(calls, ['/api/images']);
  pending.resolve(reply(uploaded(firstId))); await upload;
  assert.equal(workflow.getState().uploadStage, 'ready');
});

test('late response from a replaced image cannot bind its identifier to the current image', async () => {
  const a = deferred(); const b = deferred(); let count = 0;
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: () => (++count === 1 ? a.promise : b.promise) });
  const first = workflow.selectFiles([file('old.png')]);
  const second = workflow.selectFiles([file('new.png')]);
  a.resolve(reply(uploaded(firstId))); await first;
  assert.equal(workflow.getState().files[0].file.name, 'new.png');
  assert.equal(workflow.getState().upload, null);
  b.resolve(reply(uploaded(secondId))); await second;
  assert.equal(workflow.getState().upload.image_id, secondId);
});

test('double analysis is one call and changing images during analysis is blocked', async () => {
  const pending = deferred(); let analyses = 0;
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: async url => {
    if (url === '/api/images') return reply(uploaded(firstId));
    analyses++; return pending.promise;
  } });
  await workflow.selectFiles([file('one.png')]);
  const first = workflow.analyze('描述图片');
  assert.equal(await workflow.analyze('再来一次'), false);
  assert.equal(await workflow.selectFiles([file('other.png')]), false);
  assert.equal(analyses, 1);
  assert.equal(workflow.getState().files[0].file.name, 'one.png');
  pending.resolve(reply(answered(firstId))); await first;
  assert.equal(workflow.getState().analyzing, false);
});

test('invalid, empty and oversized selections do not upload or call model', async () => {
  let calls = 0;
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: () => { calls++; } });
  for (const invalid of [file('photo.gif'), new File([], 'empty.png'), { name: 'large.png', size: 10 * 1024 * 1024 + 1 }]) {
    await workflow.selectFiles([invalid]);
    assert.equal(workflow.getState().files.length, 0);
    assert.ok(workflow.getState().selectionError);
  }
  assert.equal(calls, 0);
});

test('unconfigured model and invalid questions never call analysis endpoint', async () => {
  const calls = [];
  const workflow = createImageWorkflow({ fetcher: async url => {
    calls.push(url); return reply(url.includes('status') ? { configured: false } : uploaded(firstId));
  } });
  await workflow.initialize(); await workflow.selectFiles([file('one.png')]);
  assert.equal(await workflow.analyze('描述图片'), false);
  assert.deepEqual(calls, ['/api/vision/status', '/api/images']);
  const ready = createImageWorkflow({ initialConfigured: true, fetcher: async () => reply(uploaded(firstId)) });
  await ready.selectFiles([file('one.png')]);
  assert.equal(await ready.analyze('   '), false);
  assert.equal(await ready.analyze('字'.repeat(2001)), false);
});

test('API failure is displayed, clears loading and never fabricates a reply or retries', async () => {
  let calls = 0;
  const workflow = createImageWorkflow({ initialConfigured: true, fetcher: async url => {
    if (url === '/api/images') return reply(uploaded(firstId));
    calls++; return reply({ detail: 'DeepSeek API 余额不足，请检查开放平台账户。' }, 503);
  } });
  await workflow.selectFiles([file('one.png')]); await workflow.analyze('描述图片');
  assert.match(workflow.getState().analysisError, /余额不足/);
  assert.equal(workflow.getState().result, null);
  assert.equal(workflow.getState().analyzing, false);
  assert.equal(calls, 1);
});

test('invalid upload ID and mismatched analysis response cannot be displayed as success', async () => {
  const invalid = createImageWorkflow({ initialConfigured: true, fetcher: async () => reply({ image_id: 'bad' }) });
  await invalid.selectFiles([file('one.png')]);
  assert.equal(invalid.getState().uploadStage, 'error');
  const mismatch = createImageWorkflow({ initialConfigured: true, fetcher: async url => reply(url === '/api/images' ? uploaded(firstId) : answered(secondId)) });
  await mismatch.selectFiles([file('one.png')]); await mismatch.analyze('描述图片');
  assert.equal(mismatch.getState().result, null);
  assert.match(mismatch.getState().analysisError, /不完整/);
});
