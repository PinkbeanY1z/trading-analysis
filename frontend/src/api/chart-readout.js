// Display backend values only. No indicators or candles are recalculated here.
export function formatChartTime(value) {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : '-';
}

export function chartReadout(candles, series, time) {
  let index = Number.isFinite(time) ? candles.findIndex(c => Date.parse(c.t) / 1000 === time) : -1;
  const cursor = index >= 0;
  if (!cursor) index = candles.length - 1;
  const candle = candles[index];
  const format = value => Number.isFinite(value) ? value.toFixed(2) : '-';
  return {
    info: { mode: cursor ? 'cursor' : 'latest', time: candle ? formatChartTime(candle.t) : '-',
      open: format(candle?.o), high: format(candle?.h), low: format(candle?.l), close: format(candle?.c) },
    maText: Object.fromEntries(['ma20', 'ma50', 'ma200'].map(key => [key, format(series[key]?.[index])]))
  };
}
