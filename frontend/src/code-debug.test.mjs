import test from 'node:test';
import assert from 'node:assert/strict';
import { validateCodeDebug } from './code-debug.mjs';

const frames=[{index:0,time_seconds:0}];
const report={status:'likely',visible_error:'NameError',evidence:[{frame_index:0,kind:'error',excerpt:'NameError: price'}],
  causes:[{hypothesis:'变量名不一致',check:'核对声明',evidence_ids:[0]}],steps:['改变量名后重新运行'],missing:[],suggested_code:'print(prices)'};
test('legacy absence and bounded sourced debug report remain readable',()=>{
  validateCodeDebug(undefined,frames);validateCodeDebug(report,frames);
});
test('unknown frames and cause evidence cannot become clickable debug advice',()=>{
  assert.throws(()=>validateCodeDebug({...report,evidence:[{...report.evidence[0],frame_index:9}]},frames));
  assert.throws(()=>validateCodeDebug({...report,causes:[{...report.causes[0],evidence_ids:[-1]}]},frames));
  assert.throws(()=>validateCodeDebug({...report,suggested_code:{execute:true}},frames));
});
