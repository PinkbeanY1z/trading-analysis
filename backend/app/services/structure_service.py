"""Deterministic provisional structure; never claims last candle is finalized."""
def pivots(rows,field,high=True,width=2):
    result=[]
    for i in range(width,len(rows)-width):
        value=rows[i][field]
        neighbors=[rows[j][field] for j in range(i-width,i+width+1) if j!=i]
        if all(value>x if high else value<x for x in neighbors): result.append(i)
    return result

def structure(rows,indicators):
    latest=indicators['latest']; rsi=indicators['rsi']; highs=pivots(rows,'h'); lows=pivots(rows,'l',False)
    divergence=[]
    for indices,field,label in [(highs,'h','bearish'),(lows,'l','bullish')]:
        if len(indices)<2: continue
        a,b=indices[-2:]
        if rsi[a] is None or rsi[b] is None: continue
        if (rows[b][field]>rows[a][field] and rsi[b]<rsi[a]) if label=='bearish' else (rows[b][field]<rows[a][field] and rsi[b]>rsi[a]):
            divergence.append({'type':label,'indices':[a,b],'times':[rows[a]['t'],rows[b]['t']]})
    prior=rows[-21:-1]; close=latest['close']; rv=latest['relativeVolume']; value=latest['rsi']
    resistance=max(r['h'] for r in prior) if len(prior)==20 else None
    support=min(r['l'] for r in prior) if len(prior)==20 else None
    breakout=None if resistance is None else 'above' if close>resistance else 'below' if close<support else 'inside'
    rising=len(rows)>1 and close>rows[-2]['c']
    previous=rsi[-2] if len(rsi)>1 else None
    return {'confirmation':'provisional_last_bar_may_be_open','pivot_rule':'strict extrema with two bars on each side; ties excluded',
            'local_highs':[{'t':rows[i]['t'],'price':rows[i]['h']} for i in highs[-5:]],
            'local_lows':[{'t':rows[i]['t'],'price':rows[i]['l']} for i in lows[-5:]],
            'rsi_state':'unavailable' if value is None else 'overbought' if value>=70 else 'oversold' if value<=30 else 'neutral',
            'rsi_momentum':None if value is None or previous is None else value-previous,
            'rsi_divergence':divergence,'support':support,'resistance':resistance,'breakout':breakout,
            'volume_breakout':None if rv is None or breakout is None else breakout=='above' and rv>1.5,
            'low_volume_rebound':None if rv is None else rising and rv<.8 and latest['trendDirection']=='down',
            'price_volume_disagreement':None if rv is None else rising and rv<.8,
            'price_position':{f'ma{p}':None if latest[f'ma{p}'] is None else 'above' if close>latest[f'ma{p}'] else 'below' if close<latest[f'ma{p}'] else 'at' for p in (20,50,200)}}
