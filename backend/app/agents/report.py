"""Render only verified numeric references, never accept free numeric model prose."""
import re

def sections(evidence, interpretation):
    out=[]
    seen=set()
    for e in evidence:
        identity=(e["symbol"],e["interval"],e["last_updated"])
        if identity in seen: continue
        seen.add(identity)
        values=e['technical_indicators']; prefix=f"{e['symbol']} · {e['interval']} · {e['source']} · {e['last_updated']}"
        out.append({'title':'市场趋势','text':prefix+'：均线结构为 '+{'bullish':'多头排列','bearish':'空头排列','mixed':'混合排列','unknown':'数据不足'}.get(values['maAlignment'],'未知')+'。仅为当前结构，不保证后续走势。'})
        for title,keys in [('均线与技术指标',['ma20','ma50','ma200','atr']),('RSI 动量',['rsi']),('量价结构',['relativeVolume'])]:
            out.append({'title':title,'text':'；'.join(k+'：'+('不可用' if values.get(k) is None else str(values[k])) for k in keys)})
        out.append({'title':'关键支撑阻力','text':str(e['support_resistance'])})
        out.append({'title':'风险因素','text':'；'.join(e.get('warnings',[]))})
    cycles = {(e['symbol'], e['interval']): e['technical_indicators']['maAlignment'] for e in evidence}
    if len(cycles) > 1:
        labels={'bullish':'多头排列','bearish':'空头排列','mixed':'混合排列','unknown':'数据不足'}
        out.append({'title':'多周期比较','text':'；'.join(
            f'{symbol} {interval}：{labels.get(trend,"未知")}'
            for (symbol,interval),trend in cycles.items())+'。不同周期信号需分别核对，数据不足时不确认一致性。'})
    if interpretation:
        for text in interpretation.explanations:
            # No numerical or reference claims accepted in free prose; use evidence token references.
            refs=re.findall(r'\{\{(\d+)\.([a-zA-Z0-9_]+)\}\}',text)
            remainder=re.sub(r'\{\{\d+\.[a-zA-Z0-9_]+\}\}','',text)
            if re.search(r'[\d零一二三四五六七八九十百千万亿]',remainder): continue
            valid=True
            for idx,key in refs:
                if int(idx)>=len(evidence) or key not in evidence[int(idx)]['technical_indicators']: valid=False;break
                text=text.replace('{{'+idx+'.'+key+'}}',str(evidence[int(idx)]['technical_indicators'][key]))
            if valid: out.append({'title':'模型解释','text':text})
    return out
