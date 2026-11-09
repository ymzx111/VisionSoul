import test from 'node:test';
import assert from 'node:assert/strict';
import { validateComparison } from './video-comparison.mjs';
const report=()=>({target:'树',category:'plant',verdict:'likely',conclusion:'暂定候选',basis:'visual_inference',features:[{frame_index:0,detail:'叶片轮廓'}],candidates:[{name:'候选A',supporting_features:[0],differences:'叶背未拍清'}],missing_features:['叶背'],next_shot:'请拍叶背。'});
test('comparison accepts legacy absence and uncertain candidates with valid evidence',()=>{
  assert.equal(validateComparison(null,[]),null);
  assert.equal(validateComparison(report(),[{index:0}]).target,'树');
  const value=report();value.verdict='insufficient';value.candidates=[];
  assert.equal(validateComparison(value,[{index:0}]).candidates.length,0);
});
test('unknown frames, feature references and falsely verified basis cannot render',()=>{
  for(const change of [value=>value.features[0].frame_index=29,value=>value.candidates[0].supporting_features=[8],value=>value.basis='web_verified']){
    const value=report();change(value);assert.throws(()=>validateComparison(value,[{index:0}]));
  }
});
