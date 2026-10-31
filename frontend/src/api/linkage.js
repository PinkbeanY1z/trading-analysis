export function chartPlan(result) {
  const evidence = result?.report?.evidence || [];
  if (!['success', 'partial', 'insufficient_data'].includes(result?.status) || !evidence.length) return null;
  if (!result.symbol || !['stock', 'gold'].includes(result.asset_class)) return null;
  const frames = [...new Set(result.timeframes || [])];
  if (!frames.length || frames.some(i => !['1h', '4h', '1day'].includes(i))) return null;
  if ('primary_timeframe' in result) {
    if (!frames.includes(result.primary_timeframe)) return null;
    frames.sort((a, b) => Number(b === result.primary_timeframe) - Number(a === result.primary_timeframe));
  }
  if (evidence.some(e => e.symbol !== result.symbol || e.asset_class !== result.asset_class)) return null;
  if (frames.some(i => !evidence.some(e => e.interval === i && e.snapshot_id))) return null;
  return { symbol: result.symbol, asset: result.asset_class, frames, evidence };
}
export function evidenceMatches(data, evidence) {
  return data.symbol === evidence.symbol && data.assetClass === evidence.asset_class &&
    data.interval === evidence.interval && data.source === evidence.source &&
    data.last_updated === evidence.last_updated && Boolean(data.snapshot_id) && data.snapshot_id === evidence.snapshot_id;
}
