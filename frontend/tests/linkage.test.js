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

test('explicit primary timeframe selects the chart and must have evidence',()=>{
  assert.deepEqual(chartPlan({...response,primary_timeframe:'1h'}).frames,['1h','1day']);
  assert.equal(chartPlan({...response,primary_timeframe:'4h'}),null);
  assert.equal(chartPlan({...response,primary_timeframe:null}),null);
});

test('typed calculation evidence does not displace valid market linkage or pretend to be a quote',()=>{
  const calc={kind:'calculation',source:'user_assumption',symbol:null,asset_class:null,interval:null,calculation_id:'calc-a'};
  const mixed={...response,report:{evidence:[...response.report.evidence,calc]}};
  assert.deepEqual(chartPlan(mixed).evidence,response.report.evidence);
  assert.equal(chartPlan({...response,report:{evidence:[calc]}}),null);
  assert.equal(chartPlan({...mixed,report:{evidence:[...response.report.evidence,{...calc,source:'mock'}]}}),null);
  assert.equal(chartPlan({...mixed,report:{evidence:[...response.report.evidence,{...calc,symbol:'XAU/USD',interval:'1day'}]}}),null);
  const data={symbol:'QQQ',assetClass:'stock',interval:'1day',source:'mock',last_updated:'date',snapshot_id:'revised'};
  assert.equal(evidenceMatches(data,chartPlan(mixed).evidence[0]),false);
});
