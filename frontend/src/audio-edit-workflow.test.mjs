import test from 'node:test';
import assert from 'node:assert/strict';
import { createAudioEditWorkflow } from './audio-edit-workflow.mjs';

const original = { status: 'ready', message: '转写', segments: [{id:0,start:1,end:2,text:'打磨院'}] };
const record = { original, audio: original, revision: 0 };
const response = (body, ok=true) => ({ ok, json: async () => structuredClone(body) });

test('open edit and save only use local endpoint and preserve original', async () => {
  const calls = []; let updated = false;
  const flow = createAudioEditWorkflow({videoId:'a'.repeat(64),duration:4,onSaved: async()=>{updated=true;},fetcher:async(url,options)=>{
    calls.push([url,options]);
    return response(options ? {...record, revision:1, audio:{...original,source:'corrected',segments:JSON.parse(options.body).segments}} : record);
  }});
  await flow.open(); flow.edit(0,{text:'达摩院',start:1.2});
  assert.equal(await flow.save(),true);
  assert.equal(updated,true);
  assert.equal(calls.length,2);
  assert.equal(calls[1][0],`/api/videos/${'a'.repeat(64)}/audio`);
  assert.equal(JSON.parse(calls[1][1].body).expected_revision,0);
  assert.equal(flow.getState().record.original.segments[0].text,'打磨院');
});
test('invalid time and blank text never send a save request', async () => {
  let calls=0;
  const flow=createAudioEditWorkflow({videoId:'a'.repeat(64),duration:4,fetcher:async()=>{calls++;return response(record);}});
  await flow.open();
  for(const patch of [{start:NaN},{start:3,end:2},{start:1,end:5},{start:1,end:2,text:'   '}]) {
    flow.edit(0,patch); assert.equal(await flow.save(),false);
  }
  assert.equal(calls,1);
});
test('conflict keeps draft and never automatically retries', async () => {
  let calls=0;
  const flow=createAudioEditWorkflow({videoId:'a'.repeat(64),duration:4,fetcher:async(url,options)=>{
    calls++;return response(options?{detail:'版本冲突，请重新打开'}:record,!options);
  }});
  await flow.open();flow.edit(0,{text:'保留草稿'});
  assert.equal(await flow.save(),false);
  assert.equal(flow.getState().segments[0].text,'保留草稿');
  assert.match(flow.getState().error,/版本冲突/);
  assert.equal(calls,2);
});
test('double save cannot produce two writes and saving cannot be edited or closed', async () => {
  let finish;let writes=0;
  const gate=new Promise(resolve=>{finish=resolve;});
  const flow=createAudioEditWorkflow({videoId:'a'.repeat(64),duration:4,fetcher:async(url,options)=>{
    if(!options)return response(record);writes++;await gate;return response({...record,revision:1});
  }});
  await flow.open();flow.edit(0,{text:'修改'});
  const pending=flow.save();assert.equal(await flow.save(),false);
  flow.edit(0,{text:'第二次修改'});flow.close();
  assert.equal(flow.getState().segments[0].text,'修改');assert.equal(flow.getState().open,true);
  finish();await pending;assert.equal(writes,1);
});
