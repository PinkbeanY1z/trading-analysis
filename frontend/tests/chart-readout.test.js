import test from 'node:test';
import assert from 'node:assert/strict';
import {chartReadout, formatChartTime} from '../src/api/chart-readout.js';
const candles=[
  {t:'2026-10-30T13:30:00Z',o:700,h:703,l:697,c:702},
  {t:'2026-10-30T17:30:00Z',o:701,h:704,l:698,c:703},
];
const series={ma20:[710,711],ma50:[720,721],ma200:[730,731]};

test('UTC labels preserve session minutes across equivalent timezone representations',()=>{
  assert.equal(formatChartTime('2026-10-31T01:30:00+08:00'),'2026-10-30 17:30 UTC');
  assert.equal(formatChartTime(Date.parse(candles[1].t)/1000),'2026-10-30 17:30 UTC');
  assert.equal(formatChartTime('invalid'),'-');
});
test('latest OHLC and MA readouts use the same backend candle',()=>{
  const r=chartReadout(candles,series);
  assert.equal(r.info.mode,'latest');
  assert.equal(r.info.close,'703.00');
  assert.equal(r.maText.ma20,'711.00');
});
test('cursor OHLC and MAs move together and leaving restores latest values',()=>{
  const r=chartReadout(candles,series,Date.parse(candles[0].t)/1000);
  assert.equal(r.info.mode,'cursor');
  assert.equal(r.info.close,'702.00');
  assert.equal(r.maText.ma50,'720.00');
  assert.equal(chartReadout(candles,series).maText.ma50,'721.00');
});
test('warmup and missing values are unavailable, never zero or an earlier MA',()=>{
  const r=chartReadout(candles,{ma20:[710,null],ma50:[null,null]});
  assert.equal(r.maText.ma20,'-');
  assert.equal(r.maText.ma50,'-');
  assert.equal(chartReadout([],{}).info.close,'-');
});
