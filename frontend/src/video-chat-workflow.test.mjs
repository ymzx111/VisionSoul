import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatWorkflow } from './chat-workflow.mjs';

const id = 'a'.repeat(32);
const video = 'b'.repeat(64);
const response = data => ({ ok: true, json: async () => data });
const result = (body, index=1) => ({ conversation_id: id, video_id: body.video_id, image_id: null,
  user_message: { id: index, role: 'user', content: body.message, video_id: body.video_id },
  assistant_message: { id: index+1, role: 'assistant', content: '我记得你说去了西湖。' },
  video_context: { summary: '可见水面。' }, usage: { total_tokens: 100 } });

test('video plus caption and follow-up use one persistent topic and no client history', async () => {
  const sent = [];
  const workflow = createChatWorkflow({ mode: 'video', storage: null, makeId: () => '1'.repeat(32), fetcher: async (url, options) => {
    if (url.endsWith('/messages')) { const body = JSON.parse(options.body); sent.push(body); return response(result(body, sent.length*2)); }
    return response(url.endsWith('/status') ? { configured: true } : []);
  } });
  await workflow.initialize();
  assert.equal(await workflow.send('我今天去了西湖', null, { videoId: video }), true);
  assert.equal(await workflow.send('我刚才说去哪了？'), true);
  assert.equal(sent[0].conversation_id, null);
  assert.equal(sent[0].video_id, video);
  assert.equal(sent[1].conversation_id, id);
  assert.equal(sent[1].video_id, video);
  assert.ok(!Object.hasOwn(sent[1], 'history'));
  assert.equal(workflow.getState().messages.length, 4);
});

test('new video starts independent topic and not-ready selection blocks paid request', async () => {
  const sent = [];
  const workflow = createChatWorkflow({ mode: 'video', storage: null, makeId: () => '2'.repeat(32), fetcher: async (url, options) => {
    if (url.endsWith('/messages')) { const body = JSON.parse(options.body); sent.push(body); return response(result(body)); }
    return response(url.endsWith('/status') ? { configured: true } : []);
  } });
  await workflow.initialize();
  assert.equal(await workflow.send('没选视频'), false);
  await workflow.send('第一段', null, { videoId: video });
  assert.equal(await workflow.send('还没上传好', null, { uploadReady: false }), false);
  await workflow.send('换一段', null, { videoId: 'c'.repeat(64) });
  assert.equal(sent.length, 2);
  assert.equal(sent[1].conversation_id, null);
  assert.equal(workflow.getState().messages.length, 2);
});

test('video restore uses separate storage and recovers context without model call', async () => {
  const calls = [];
  const workflow = createChatWorkflow({ mode: 'video', storage: { getItem: key => key.includes('video') ? id : null, setItem() {} }, fetcher: async url => {
    calls.push(url);
    return response(url.endsWith('/status') ? { configured: true } : url.endsWith(id) ? { id, video_id: video, messages: [], video_context: { opening_message: '去了西湖' } } : []);
  } });
  await workflow.initialize();
  assert.equal(workflow.getState().videoId, video);
  assert.equal(workflow.getState().videoContext.opening_message, '去了西湖');
  assert.ok(calls.some(url => url.includes('kind=video')));
  assert.ok(!calls.some(url => url.endsWith('/messages')));
});

test('explicit retry keeps request id and a double send does not duplicate paid call', async () => {
  let count = 0;
  let fail = true;
  const ids = [];
  const workflow = createChatWorkflow({ mode: 'video', storage: null, makeId: () => String(++count).repeat(32), fetcher: async (url, options) => {
    if (url.endsWith('/messages')) {
      const body = JSON.parse(options.body); ids.push(body.request_id);
      if (fail) throw new TypeError('lost connection');
      return response(result(body));
    }
    return response(url.endsWith('/status') ? { configured: true } : []);
  } });
  await workflow.initialize();
  assert.equal(await workflow.send('看看视频', null, { videoId: video }), false);
  fail = false;
  const pending = workflow.send('看看视频', null, { videoId: video });
  assert.equal(await workflow.send('看看视频', null, { videoId: video }), false);
  assert.equal(await pending, true);
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1]);
});

test('only explicit video inspection requests new visual comparison and keeps it on retry',async()=>{
  const sent=[];let fail=false;
  const workflow=createChatWorkflow({mode:'video',storage:null,makeId:()=>String(sent.length+1).repeat(32),fetcher:async(url,options)=>{
    if(url.endsWith('/messages')){const body=JSON.parse(options.body);sent.push(body);if(fail)throw new TypeError('offline');return response(result(body,sent.length*2));}
    return response(url.endsWith('/status')?{configured:true}:[]);
  }});
  await workflow.initialize();await workflow.send('分享',null,{videoId:video});
  assert.equal(Object.hasOwn(sent[0],'inspect_video'),false);
  fail=true;await workflow.send('比对',null,{inspectVideo:true});fail=false;
  await workflow.send('比对',null,{inspectVideo:true});
  assert.equal(sent[1].inspect_video,true);assert.equal(sent[1].request_id,sent[2].request_id);
});
