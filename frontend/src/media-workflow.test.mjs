import test from 'node:test';
import assert from 'node:assert/strict';
import { createMediaWorkflow } from './media-workflow.mjs';

const imageId='1'.repeat(32),videoId='a'.repeat(64),conversationId='b'.repeat(32);
const image=()=>new File(['image'],'photo.png',{type:'image/png'});
const video=()=>new File(['video'],'clip.mp4',{type:'video/mp4'});
const response=body=>({ok:true,json:async()=>body});
const videoInfo=()=>({video_id:videoId,duration_seconds:5,sample_count:2,frames:[{index:0,time_seconds:0,seen_at:[0]},{index:1,time_seconds:4.96,seen_at:[4.96]}]});
const gate=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};};
function setup(options={}) {
  const calls=[];let paid=0;
  const fetcher=async(url,request={})=>{
    calls.push({url,...request});
    if(url==='/api/chat/conversations?kind=all')return response([]);
    if(url.endsWith('/status'))return response({configured:true,max_bytes:50*1024*1024,max_seconds:30,max_frames:31});
    if(url==='/api/images')return response({image_id:imageId,width:20,height:20});
    if(url==='/api/videos'||url===`/api/videos/${videoId}`)return response(videoInfo());
    if(url==='/api/chat/messages'){
      const body=JSON.parse(request.body);paid++;
      return response({conversation_id:body.conversation_id||String(paid).padStart(32,'0'),image_id:body.image_id||null,video_id:body.video_id||null,
        user_message:{id:paid*2,role:'user',content:body.message},assistant_message:{id:paid*2+1,role:'assistant',content:'分析结果'},video_context:null});
    }
    if(url===`/api/chat/conversations/${conversationId}`)return response({id:conversationId,image_id:options.savedImage?imageId:null,video_id:options.savedVideo?videoId:null,messages:[]});
    throw Error('Unexpected '+url);
  };
  let serial=0;
  const workflow=createMediaWorkflow({fetcher:options.fetcher||fetcher,storage:options.storage||null,durationReader:options.durationReader||(()=>Promise.resolve(30)),makeId:()=>String(++serial).padStart(32,'0')});
  return{workflow,calls,posts:()=>calls.filter(c=>c.method==='POST'),messages:()=>calls.filter(c=>c.url==='/api/chat/messages').map(c=>JSON.parse(c.body))};
}

test('one entry routes photo and video automatically; swapping starts a fresh topic without crossed IDs',async()=>{
  const {workflow:w,posts,messages}=setup();await w.initialize();
  assert.equal(await w.selectFiles([image()]),true);assert.equal(w.getState().kind,'image');assert.equal(messages().length,0);
  assert.equal(await w.send(''),true);assert.equal(messages()[0].image_id,imageId);assert.equal(messages()[0].message,'请说明这张图片的主要内容和可见细节。');assert.ok(!messages()[0].video_id);
  await w.send('继续解释');assert.equal(messages()[1].conversation_id,'1'.padStart(32,'0'));
  assert.equal(await w.selectFiles([video()]),true);assert.equal(w.getState().chat.messages.length,0);
  assert.equal(await w.send(''),true);assert.equal(messages()[2].conversation_id,null);assert.equal(messages()[2].image_id,null);assert.equal(messages()[2].video_id,videoId);assert.equal(messages()[2].message,'');
  await w.selectFiles([image()]);await w.send('再看图片');assert.equal(messages()[3].conversation_id,null);assert.ok(!messages()[3].video_id);
  assert.equal(posts().filter(c=>c.url==='/api/images').length,2);w.dispose();
});

test('video uses the same action for a typed request and keeps special actions explicit', async () => {
  const { workflow, messages } = setup();
  await workflow.initialize();
  await workflow.selectFiles([video()]);
  assert.equal(await workflow.send('  看一下第 4 题  '), true);
  assert.equal(messages()[0].message, '看一下第 4 题');
  assert.equal(await workflow.send('  '), false);
  await workflow.selectFiles([video()]);
  assert.equal(await workflow.send('', { debugCode: true }), true);
  assert.equal(messages()[1].debug_code, true);
  assert.match(messages()[1].message, /源码和终端错误/);
  workflow.dispose();
});

test('multiple photos and mixed uploads produce explicit errors with no upload or paid call',async()=>{
  const {workflow:w,posts}=setup();await w.initialize();
  assert.equal(await w.selectFiles([image(),image()]),false);assert.equal(w.getState().error,'目前只支持单张图片上传');
  assert.equal(await w.selectFiles([image(),new File(['gif'],'other.gif',{type:'image/gif'})]),false);assert.equal(w.getState().error,'目前只支持单张图片上传');
  assert.equal(await w.send('分析'),false);
  assert.equal(await w.selectFiles([image(),video()]),false);assert.match(w.getState().error,/一次只能/);
  assert.equal(posts().length,0);w.dispose();
});

test('over 30 seconds is rejected before uploading; exactly 30 is accepted',async()=>{
  let seconds=30.01;const {workflow:w,posts}=setup({durationReader:async()=>seconds});await w.initialize();
  assert.equal(await w.selectFiles([video()]),false);assert.equal(w.getState().error,'超过视频时长上限，上限为30秒');assert.equal(posts().length,0);
  seconds=30;assert.equal(await w.selectFiles([video()]),true);assert.equal(posts().length,1);w.dispose();
});

test('failed new selection retains previous preview but blocks send until dismissed or corrected',async()=>{
  const {workflow:w,messages}=setup();await w.initialize();await w.selectFiles([image()]);
  await w.selectFiles([image(),image()]);assert.equal(w.getState().image.upload.image_id,imageId);
  assert.equal(await w.send('分析'),false);assert.equal(messages().length,0);
  w.dismissError();assert.equal(await w.send('用当前图片'),true);w.dispose();
});

test('metadata checking and external audio edit block sends; disposing ignores late duration results',async()=>{
  const pending=gate();const {workflow:w,posts}=setup({durationReader:()=>pending.promise});await w.initialize();
  const selection=w.selectFiles([video()]);assert.equal(await w.send('不要提前分析'),false);assert.equal(await w.selectFiles([image()]),false);
  w.dispose();pending.resolve(20);assert.equal(await selection,false);assert.equal(posts().length,0);
  const second=setup();await second.workflow.initialize();second.workflow.setExternalBusy(true);
  assert.equal(await second.workflow.selectFiles([image()]),false);assert.equal(await second.workflow.send('文字'),false);second.workflow.dispose();
});

test('clear starts a text topic and never silently keeps the old media attachment',async()=>{
  const {workflow:w,messages}=setup();await w.initialize();await w.selectFiles([image()]);await w.send('看图片');
  assert.equal(w.clear(),true);assert.equal(w.getState().kind,null);await w.send('现在聊文字');
  assert.equal(messages()[1].conversation_id,null);assert.equal(messages()[1].image_id,null);assert.ok(!messages()[1].video_id);w.dispose();
});

for(const kind of ['Image','Video'])test(`old ${kind} topic restores from prior storage key without paid call`,async()=>{
  const storage={getItem:key=>key===(kind==='Video'?'visionsoul.current-video-conversation':'visionsoul.current-conversation')?conversationId:null,setItem(){},removeItem(){}};
  const setupResult=setup({storage,[`saved${kind}`]:true});const w=setupResult.workflow;
  await w.initialize();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(w.getState().kind,kind.toLowerCase());assert.equal(setupResult.messages().length,0);
  if(kind==='Image')assert.equal(w.getState().restoredImageId,imageId);else assert.equal(w.getState().video.video.video_id,videoId);
  w.dispose();
});
