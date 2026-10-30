"""Deterministic indicators. Missing observations stay missing; arrays align to bars."""
from math import isfinite

PERIODS = (7, 20, 25, 50, 60, 200)

def valid(x):
    return isinstance(x, (int, float)) and isfinite(x)

def sma(values, period):
    if period < 1:
        raise ValueError('period must be positive')
    return [sum(w) / period if len(w := values[max(0, i-period+1):i+1]) == period and all(valid(v) for v in w) else None for i in range(len(values))]

def wilder(values, period=14):
    out = [None] * len(values)
    seed = []
    previous = None
    for i, value in enumerate(values):
        if not valid(value):
            seed, previous = [], None
            continue
        if previous is None:
            seed.append(value)
            if len(seed) < period:
                continue
            previous = sum(seed) / period
        else:
            previous = (previous * (period-1) + value) / period
        out[i] = previous
    return out

def rsi(closes, period=14):
    changes = [None] + [b-a if valid(a) and valid(b) else None for a,b in zip(closes, closes[1:])] if closes else []
    gains = wilder([max(x, 0) if x is not None else None for x in changes], period)
    losses = wilder([max(-x, 0) if x is not None else None for x in changes], period)
    return [None if g is None or l is None else 50.0 if g == l == 0 else 100.0 if l == 0 else 100-100/(1+g/l) for g,l in zip(gains, losses)]

def atr(candles, period=14):
    # First TR requires previous close; first ATR is at index period.
    tr = [None] if candles else []
    for prev, row in zip(candles, candles[1:]):
        tr.append(max(row['h']-row['l'], abs(row['h']-prev['c']), abs(row['l']-prev['c'])))
    return wilder(tr, period)

def calculate(candles, asset_class='stock'):
    closes = [c['c'] for c in candles]
    volumes = [c.get('v') if asset_class != 'gold' else None for c in candles]
    result = {f'ma{p}': sma(closes, p) for p in PERIODS}
    result.update(rsi=rsi(closes), atr=atr(candles), volume=volumes, volMA24=sma(volumes, 24))
    last = lambda a: a[-1] if a else None
    latest = {k:last(v) for k,v in result.items()}
    close = last(closes)
    latest['close'] = close
    latest['high'] = candles[-1]['h'] if candles else None
    latest['priceDistancePct'] = {f'ma{p}': (close/latest[f'ma{p}']-1)*100 if latest[f'ma{p}'] not in (None,0) else None for p in PERIODS}
    latest['slopes'] = {f'ma{p}': (result[f'ma{p}'][-1]-result[f'ma{p}'][-6])/5 if len(candles)>=p+5 else None for p in PERIODS}
    a,b,c = (latest[f'ma{p}'] for p in (20,50,200))
    latest['maAlignment'] = 'unknown' if None in (a,b,c) else 'bullish' if a>b>c else 'bearish' if a<b<c else 'mixed'
    latest['trendDirection'] = {'bullish':'up','bearish':'down','mixed':'flat','unknown':'unknown'}[latest['maAlignment']]
    baseline = sma(volumes[:-1], 24)
    mean = last(baseline)
    latest['relativeVolume'] = last(volumes)/mean if mean not in (None,0) and valid(last(volumes)) else None
    latest['volumeAvailable'] = latest['relativeVolume'] is not None
    result['latest'] = latest
    result['insufficient_history'] = len(candles)<200
    return result
