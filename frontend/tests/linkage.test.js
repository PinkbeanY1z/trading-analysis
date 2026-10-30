import test from 'node:test';
import assert from 'node:assert/strict';
import {chartPlan,evidenceMatches} from '../src/api/linkage.js';
const e={symbol:'QQQ',asset_class:'stock',interval:'1day',source:'mock',last_updated:'date',snapshot_id:'a'};
const response={status:'success',symbol:'QQQ',asset_class:'stock',timeframes:['1day','1h'],report:{evidence:[e,{...e,interval:'1h'}]}};
test('uses only explicit evidence and supported timeframes',()=>{
  assert.deepEqual(chartPlan(response).frames,['1day','1h']);
  assert.equal(chartPlan({...response,asset_class:null}),null);
  assert.equal(chartPlan({...response,status:'failed'}),null);
  assert.equal(chartPlan({...response,timeframes:['2h']}),null);
  assert.equal(chartPlan({...response,report:{evidence:[{...e,symbol:'XAU/USD'}]}}),null);
});
test('same timestamp with revised candle snapshot must not match',()=>{
  const d={symbol:'QQQ',assetClass:'stock',interval:'1day',source:'mock',last_updated:'date',snapshot_id:'a'};
  assert.equal(evidenceMatches(d,e),true);
  assert.equal(evidenceMatches({...d,snapshot_id:'revised'},e),false);
});
