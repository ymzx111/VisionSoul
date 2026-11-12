import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatWorkflow } from './chat-workflow.mjs';

const workId = 'a'.repeat(32), companionId = 'b'.repeat(32), legacyId = 'c'.repeat(32);
const response = (data, status = 200) => ({ ok: status < 300, status, json: async () => data });
const memory = seed => {
  const map = new Map(Object.entries(seed || {}));
  return { getItem: key => map.get(key), setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
};
function harness() {
  const calls = [], rows = new Map();
  let sequence = 0;
  rows.set(legacyId, { id: legacyId, title: '旧话题', space: 'legacy', messages: [{ id: 7, role: 'user', content: '保留旧消息' }] });
  const fetcher = async (url, options = {}) => {
    calls.push({ url, options });
    const parsed = new URL(url, 'http://localhost');
    const space = parsed.searchParams.get('space');
    if (parsed.pathname === '/api/chat/status') return response({ configured: true, name: space === 'work' ? '解析助手' : '小灵' });
    if (parsed.pathname === '/api/chat/conversations') return response([...rows.values()].filter(row => row.space === space));
    if (parsed.pathname.endsWith('/classify')) {
      const id = parsed.pathname.split('/')[4], row = rows.get(id);
      if (row?.space !== 'legacy') return response({ detail: '已归类' }, 409);
      row.space = JSON.parse(options.body).space;
      return response({ id, space: row.space });
    }
    if (parsed.pathname.startsWith('/api/chat/conversations/')) {
      const row = rows.get(parsed.pathname.split('/')[4]);
      return row?.space === space ? response(row) : response({ detail: '空间不一致' }, 409);
    }
    if (parsed.pathname === '/api/chat/messages') {
      const body = JSON.parse(options.body), id = body.space === 'work' ? workId : companionId;
      const user = { id: ++sequence, role: 'user', content: body.message }, assistant = { id: ++sequence, role: 'assistant', content: `${body.space}回答` };
      const row = rows.get(id) || { id, space: body.space, title: body.message, messages: [] };
      row.messages.push(user, assistant); rows.set(id, row);
      return response({ space: body.space, conversation_id: id, user_message: user, assistant_message: assistant });
    }
    throw new Error(`Unexpected URL ${url}`);
  };
  return { fetcher, calls, rows };
}

test('work and companion maintain separate storage, histories, contexts and request spaces', async () => {
  const api = harness(), storage = memory({ 'visionsoul.current-media-conversation': legacyId });
  const work = createChatWorkflow({ ...api, storage, mode: 'media', space: 'work', makeId: () => '1'.repeat(32) });
  const companion = createChatWorkflow({ ...api, storage, mode: 'media', space: 'companion', makeId: () => '2'.repeat(32) });
  await work.initialize(); await companion.initialize();
  assert.equal(work.getState().conversationId, null); // 不自动把旧角色上下文带入工作。
  assert.equal(await work.send('工作问题'), true);
  assert.equal(await companion.send('角色分享'), true);
  await new Promise(setImmediate); // 列表在已保存回复后异步刷新。
  assert.equal(storage.getItem('visionsoul.work.current-conversation'), workId);
  assert.equal(storage.getItem('visionsoul.companion.current-conversation'), companionId);
  assert.equal(work.getState().messages[0].content, '工作问题');
  assert.equal(companion.getState().messages[0].content, '角色分享');
  assert.equal(work.getState().recent.length, 1);
  assert.equal(companion.getState().recent.length, 1);
  const posts = api.calls.filter(call => call.url.endsWith('/messages')).map(call => JSON.parse(call.options.body));
  assert.deepEqual(posts.map(body => body.space), ['work', 'companion']);
  assert.equal(posts.every(body => body.conversation_id === null && !body.history), true);
  work.newConversation();
  assert.equal(storage.getItem('visionsoul.companion.current-conversation'), companionId);
});

test('reload restores only selected space and rejects tampered saved cross-space ID', async () => {
  const api = harness(), storage = memory();
  const work = createChatWorkflow({ ...api, storage, mode: 'media', space: 'work' });
  await work.initialize(); await work.send('保留这个问题');
  work.dispose();
  const restored = createChatWorkflow({ ...api, storage, mode: 'media', space: 'work' });
  await restored.initialize();
  assert.equal(restored.getState().conversationId, workId);
  storage.setItem('visionsoul.companion.current-conversation', workId);
  const companion = createChatWorkflow({ ...api, storage, mode: 'media', space: 'companion' });
  await companion.initialize();
  assert.equal(companion.getState().conversationId, null);
  assert.equal(storage.getItem('visionsoul.companion.current-conversation'), undefined);
  assert.equal(await restored.loadConversation(companionId), false);
  assert.equal(restored.getState().conversationId, workId);
  assert.equal(api.calls.filter(call => call.url.endsWith('/messages')).length, 1);
});

test('old topic is explicitly classified and restored without model call', async () => {
  const api = harness(), flow = createChatWorkflow({ ...api, storage: memory(), mode: 'media', space: 'work' });
  await flow.initialize();
  assert.equal(flow.getState().legacy.length, 1);
  assert.equal(await flow.classifyLegacy(legacyId), true);
  assert.equal(flow.getState().conversationId, legacyId);
  assert.equal(flow.getState().messages[0].content, '保留旧消息');
  assert.equal(flow.getState().legacy.length, 0);
  assert.equal(flow.getState().recent[0].space, 'work');
  assert.equal(api.calls.some(call => call.url.endsWith('/messages')), false);
  assert.equal(await flow.classifyLegacy(legacyId), false);
});

test('malformed cross-space detail and reply cannot replace current state', async () => {
  const api = harness();
  let broken = false;
  const flow = createChatWorkflow({ storage: memory(), space: 'work', mode: 'media', fetcher: async (url, options) => {
    const result = await api.fetcher(url, options);
    const data = await result.json();
    if (broken && (url.endsWith('/messages') || url.includes(`/conversations/${workId}`))) return response({ ...data, space: 'companion' });
    return result;
  } });
  await flow.initialize(); await flow.send('第一条'); broken = true;
  assert.equal(await flow.loadConversation(workId), false);
  assert.equal(flow.getState().messages.length, 2);
  assert.equal(await flow.send('第二条'), false);
  assert.equal(flow.getState().messages.length, 2);
  assert.match(flow.getState().error, /空间/);
});

test('legacy classification is blocked while paid request is pending', async () => {
  const api = harness(); let finish;
  const flow = createChatWorkflow({ storage: memory(), space: 'work', mode: 'media', fetcher: (url, options) => {
    if (!url.endsWith('/messages')) return api.fetcher(url, options);
    return new Promise(resolve => { finish = async () => resolve(await api.fetcher(url, options)); });
  } });
  await flow.initialize();
  const sent = flow.send('处理中');
  assert.equal(await flow.classifyLegacy(legacyId), false);
  assert.equal(api.calls.some(call => call.url.endsWith('/classify')), false);
  await finish(); await sent;
});

test('work photos and videos request the IDE and pass sample data', async () => {
  const videoId = '9'.repeat(64);
  const imageId = '8'.repeat(32);
  const calls = [];
  const localSource = { status: 'opened', detail: '已发送到 PyCharm。', path: 'G:\\VisionSoul\\workspace\\main.py' };
  const fetcher = async (url, options = {}) => {
    if (url.endsWith('/messages')) {
      const body = JSON.parse(options.body);
      calls.push(body);
      return response({ space: body.space, image_id: body.image_id, video_id: body.video_id, conversation_id: workId,
        user_message: { id: 1, role: 'user', content: body.message },
        assistant_message: { id: 2, role: 'assistant', content: '完整程序' },
        local_source: body.open_in_ide ? localSource : null });
    }
    return response([]);
  };
  for (const space of ['work', 'companion']) {
    const flow = createChatWorkflow({ fetcher, storage: memory(), mode: 'media', space, makeId: () => workId });
    const pythonTest = { stdin: '21', expected: '42' };
    for (const [image, video] of [[null, videoId], [imageId, null], [null, null]]) {
      assert.equal(await flow.send('请编写程序', image, { videoId: video, pythonTest }), true);
      assert.deepEqual(flow.getState().localSource, space === 'work' && (image || video) ? localSource : null);
      flow.newConversation();
      assert.equal(flow.getState().localSource, null);
    }
  }
  assert.deepEqual(calls.map(call => call.open_in_ide), [true, true, undefined, undefined, undefined, undefined]);
  assert.deepEqual(calls[1].python_test, { stdin: '21', expected: '42' });
  assert.equal(calls[4].python_test, undefined);
});
