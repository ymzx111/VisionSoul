import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatWorkflow } from './chat-workflow.mjs';

const idA = 'a'.repeat(32), idB = 'b'.repeat(32), imageA = '1'.repeat(32), imageB = '2'.repeat(32);
const reply = (body, status = 200) => ({ status, ok: status < 300, json: async () => body });
const pair = (id = idA, message = '你好', picture = null, offset = 0) => ({ conversation_id: id, image_id: picture,
  user_message: { id: offset + 1, role: 'user', content: message, image_id: picture },
  assistant_message: { id: offset + 2, role: 'assistant', content: '小灵在这儿。', image_id: null }, usage: { total_tokens: 20 } });
function storage(seed) { const map = new Map(seed ? [['visionsoul.current-conversation', seed]] : []); return { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) }; }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('text chat needs no image and sends only user content with automatic IDs', async () => {
  const calls = [], memory = storage();
  const flow = createChatWorkflow({ storage: memory, makeId: () => '3'.repeat(32), fetcher: async (url, options) => {
    calls.push({ url, options }); return reply(url.endsWith('/messages') ? pair() : []);
  } });
  assert.equal(await flow.send('  你好  '), true);
  const body = JSON.parse(calls[0].options.body);
  assert.deepEqual(body, { conversation_id: null, message: '你好', image_id: null, request_id: '3'.repeat(32) });
  assert.equal(flow.getState().messages.length, 2);
  assert.equal(memory.getItem('visionsoul.current-conversation'), idA);
});

test('follow-up uses same conversation while switching pictures creates a new topic', async () => {
  const bodies = []; let sequence = 0;
  const flow = createChatWorkflow({ storage: storage(), makeId: () => (++sequence).toString(16).padStart(32, '0'), fetcher: async (url, options) => {
    if (!url.endsWith('/messages')) return reply([]);
    const body = JSON.parse(options.body); bodies.push(body);
    return reply(pair(bodies.length < 3 ? idA : idB, body.message, body.image_id, bodies.length * 10));
  } });
  await flow.send('看图', imageA);
  await flow.send('继续说', imageA);
  assert.equal(bodies[1].conversation_id, idA);
  assert.notEqual(bodies[0].request_id, bodies[1].request_id);
  await flow.send('另一张', imageB);
  assert.equal(bodies[2].conversation_id, null);
  assert.equal(flow.getState().conversationId, idB);
  assert.equal(flow.getState().messages.length, 2);
});

test('multiple or unfinished uploads never send model requests', async () => {
  let calls = 0;
  const flow = createChatWorkflow({ storage: storage(), fetcher: async () => { calls++; } });
  await flow.send('看图', imageA, { imageCount: 2 });
  assert.match(flow.getState().error, /目前只能分析单个图像/);
  await flow.send('看图', null, { imageCount: 1, uploadReady: false });
  await flow.send(' ');
  assert.equal(calls, 0);
});

test('double clicks, reset and history changes are blocked during a request', async () => {
  const pending = deferred(); let posts = 0;
  const flow = createChatWorkflow({ storage: storage(), fetcher: async url => {
    if (!url.endsWith('/messages')) return reply([]);
    posts++; return pending.promise;
  } });
  const first = flow.send('你好');
  assert.equal(await flow.send('重复'), false);
  assert.equal(flow.newConversation(), false);
  assert.equal(await flow.loadConversation(idB), false);
  assert.equal(posts, 1);
  pending.resolve(reply(pair())); await first;
});

test('a failed transport keeps the same request ID for explicit recovery, with no automatic retry', async () => {
  const bodies = []; let ids = 0;
  const flow = createChatWorkflow({ storage: storage(), makeId: () => (++ids).toString(16).padStart(32, '0'), fetcher: async (url, options) => {
    if (!url.endsWith('/messages')) return reply([]);
    bodies.push(JSON.parse(options.body));
    if (bodies.length === 1) throw new TypeError('network failed');
    return reply({ ...pair(), replayed: true });
  } });
  assert.equal(await flow.send('你好'), false);
  assert.equal(bodies.length, 1);
  assert.equal(flow.getState().messages.length, 0);
  assert.equal(await flow.send('你好'), true);
  assert.equal(bodies[0].request_id, bodies[1].request_id);
  assert.equal(flow.getState().replayed, true);
  assert.equal(flow.getState().messages.length, 2);
});

test('reload restores local transcript without calling the model, and new topic stays isolated', async () => {
  const calls = [], memory = storage(idA);
  const flow = createChatWorkflow({ storage: memory, fetcher: async url => {
    calls.push(url);
    if (url.endsWith('/status')) return reply({ name: '小灵', style: '活泼俏皮', context_turns: 6 });
    if (url.endsWith('/' + idA)) return reply({ id: idA, image_id: imageA, messages: [pair().user_message, pair().assistant_message] });
    return reply([{ id: idA, title: '你好' }]);
  } });
  await flow.initialize();
  assert.equal(flow.getState().conversationId, idA);
  assert.equal(flow.getState().messages.length, 2);
  assert.equal(calls.some(url => url.endsWith('/messages')), false);
  flow.newConversation();
  assert.equal(flow.getState().messages.length, 0);
  assert.equal(memory.getItem('visionsoul.current-conversation'), undefined);
});

test('missing saved topic starts fresh, and malformed replies do not appear as success', async () => {
  const memory = storage(idA);
  const flow = createChatWorkflow({ storage: memory, fetcher: async url => {
    if (url.endsWith('/status')) return reply({ name: '小灵' });
    if (url.endsWith('/' + idA)) return reply({ detail: '不存在' }, 404);
    return reply([]);
  } });
  await flow.initialize();
  assert.equal(flow.getState().error, '');
  assert.equal(memory.getItem('visionsoul.current-conversation'), undefined);
  const bad = createChatWorkflow({ storage: storage(), fetcher: async () => reply({}) });
  await bad.send('你好');
  assert.match(bad.getState().error, /不完整/);
  assert.equal(bad.getState().messages.length, 0);
});
