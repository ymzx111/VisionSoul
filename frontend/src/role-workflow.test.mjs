import test from 'node:test';
import assert from 'node:assert/strict';
import { createRoleWorkflow } from './role-workflow.mjs';

const profile = { name:'小灵',identity:'AI伙伴',background:'好奇的观察者',traits:'俏皮',speaking_style:'自然短句',interests:'风景',relationship:'平等',avoidances:'不硬夸' };
const card = () => ({ profile:{...profile},draft:{...profile},revision:0,draft_revision:0,messages:[],versions:[{revision:0,profile:{...profile}}] });
const ok = data => ({ok:true,json:async()=>data});
function setup(handler) {
  const calls=[];
  const workflow=createRoleWorkflow({makeId:()=> 'a'.repeat(32),fetcher:async(url,options)=> {
    calls.push([url,options]);
    if (url.endsWith('/memories') && !options?.body) return ok([]);
    if (url.endsWith('/sources')) return ok([]);
    if (!options?.body) return ok({card:card(),configured:true});
    return handler(url,options,calls);
  }});
  return {workflow,calls};
}

test('entering settings and editing locally never calls model',async()=> {
  const {workflow,calls}=setup(()=>assert.fail('no mutation expected'));
  await workflow.initialize();
  workflow.update('traits','有主见');
  assert.equal(workflow.getState().dirty,true);
  assert.equal(calls.length,3);
  assert.equal(workflow.getState().card.profile.traits,'俏皮');
  await workflow.initialize();
  assert.equal(calls.length,4);
  assert.equal(calls.at(-1)[0].endsWith('/sources'),true);
  assert.equal(workflow.getState().form.traits,'有主见');
  assert.equal(workflow.getState().dirty,true);
});

test('manual dirty form is saved before discussion; response stays draft',async()=> {
  const {workflow,calls}=setup(async(url,options)=> {
    const body=JSON.parse(options.body);
    if(url.endsWith('/draft')) return ok({...card(),draft:body.profile,draft_revision:1});
    assert.equal(body.expected,1);
    return ok({card:{...card(),draft:{...profile,traits:'更有主见'},draft_revision:2},usage:{total_tokens:100}});
  });
  await workflow.initialize();workflow.update('traits','有主见');
  assert.equal(await workflow.discuss('进一步整理'),true);
  assert.equal(calls[3][0].endsWith('/draft'),true);
  assert.equal(calls[4][0].endsWith('/discuss'),true);
  assert.equal(workflow.getState().card.revision,0);
  assert.equal(workflow.getState().form.traits,'更有主见');
});

test('preview does not publish and explicit save uses current version',async()=> {
  const {workflow,calls}=setup(async(url,options)=> url.endsWith('/preview')?ok({answer:'测试回复',usage:{total_tokens:50}}):ok({...card(),revision:1,draft_revision:1}));
  await workflow.initialize();
  await workflow.preview('分享成果');
  assert.equal(workflow.getState().card.revision,0);
  assert.equal(workflow.getState().preview,'测试回复');
  await workflow.reload();
  assert.equal(workflow.getState().usage,null);
  assert.equal(workflow.getState().cached,false);
  await workflow.save(true);
  assert.equal(workflow.getState().card.revision,1);
  assert.equal(calls.filter(([url])=>url.endsWith('/publish')).length,1);
});

test('model network failure never auto retries and manual retry keeps request id',async()=> {
  let fail=true;const ids=[];
  const {workflow}=setup(async(url,options)=> {
    ids.push(JSON.parse(options.body).request_id);
    if(fail)throw new TypeError('offline');
    return ok({answer:'已恢复',cached:true});
  });
  await workflow.initialize();
  assert.equal(await workflow.preview('测试'),false);
  assert.equal(ids.length,1);fail=false;
  assert.equal(await workflow.preview('测试'),true);
  assert.equal(ids[0],ids[1]);
});

test('busy guard blocks double model submission',async()=> {
  let done;const gate=new Promise(resolve=>done=resolve);
  const {workflow,calls}=setup(()=>gate);
  await workflow.initialize();const pending=workflow.preview('测试');
  assert.equal(await workflow.preview('测试'),false);
  done(ok({answer:'一次回复'}));await pending;
  assert.equal(calls.filter(([url])=>url.endsWith('/preview')).length,1);
});
