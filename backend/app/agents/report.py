"""Render only verified numeric references, never accept free numeric model prose."""
import json
import re
from copy import deepcopy
from pydantic import ValidationError
from .schemas import Interpretation, ReportAttempt

REF=r'\{\{(\d+)\.([a-zA-Z][a-zA-Z0-9_.]*)\}\}'


def reference_values(evidence):
    """Closed map of actual scalar fields, including nested deterministic metrics."""
    out={}
    def visit(data,prefix=''):
        for key,value in data.items():
            path=prefix+key
            if isinstance(value,dict): visit(value,path+'.')
            elif isinstance(value,(int,float,str)) and not isinstance(value,bool): out[path]=value
    visit(evidence['technical_indicators'])
    visit(evidence['support_resistance'],'support_resistance.')
    return out


def free_prose(text):
    remainder=re.sub(REF,'',text)
    # Fixed labels/counts describe the protocol/rules; they are not market readings.
    periods='(?:7|20|25|50|60|200)'
    remainder=re.sub(r'(?<![A-Za-z0-9_])(?:MA\s*'+periods+r'(?:[/、,]\s*'+periods+r')*|RSI\s*\(?14\)?|ATR\s*\(?14\)?|1day|1h|4h)(?![A-Za-z0-9_])','',remainder,flags=re.I)
    remainder=re.sub(periods+r'(?:日|周期)(?:均线|移动平均线)','',remainder)
    remainder=re.sub(r'(?<![\d.])(?:1|4)\s*小时|四小时|一小时|三条均线|前20根|前二十根|第[一二三四五六七八九十]+|一致|统一|进一步|一般|一方面|另一方面|一旦|万一','',remainder)
    return remainder

def evidence_from(records):
    return [{'call_id':r['call_id'], **deepcopy(data)}
            for r in records if r['status']=='success'
            for data in r['result'].get('timeframes',[r['result']])]


def catalog(evidence):
    """Explicit model-visible IDs: transport tool_call_id alone is not a report citation."""
    return [{'index':i, 'evidence_id':d['call_id'], 'symbol':d['symbol'],
             'interval':d['interval'], 'source':d['source'],
             'reference_tokens':['{{'+str(i)+'.'+key+'}}' for key in reference_values(d)]}
            for i,d in enumerate(evidence)]


def prose_issue(text, evidence):
    refs=re.findall(REF,text)
    remainder=free_prose(text)
    if re.search(r'[\d零一二三四五六七八九十百千万亿{}]',remainder):
        return 'unverified_numeric_prose'
    for idx,key in refs:
        if int(idx)>=len(evidence): return 'invalid_numeric_reference'
        if key not in reference_values(evidence[int(idx)]): return 'invalid_numeric_reference'
    return None


def validate_interpretation(content, evidence, finish_reason='unknown'):
    """No raw input, prose, arbitrary keys or exception messages enter diagnostics."""
    attempt=ReportAttempt(content_type='text' if isinstance(content,str) else 'unsupported')
    if finish_reason in ('stop','length','tool_calls','content_filter'): attempt.finish_reason=finish_reason
    known={d['call_id'] for d in evidence}
    def issue(code, path=()): attempt.issues.append({'code':code,'path':list(path)})
    if not isinstance(content,str) or not content.strip():
        issue('missing_report'); return None,attempt
    try:
        data=json.loads(content)
    except ValueError:
        attempt.json_shape='invalid'; issue('invalid_json'); return None,attempt
    attempt.json_shape='object' if isinstance(data,dict) else 'non_object'
    fields=set(Interpretation.model_fields)
    if isinstance(data,dict):
        attempt.field_types={k:type(v).__name__ for k,v in data.items() if k in fields}
        attempt.extra_field_count=len(set(data)-fields)
        ids=data.get('evidence_ids',[])
        if isinstance(ids,list):
            attempt.evidence_ids=[v for v in ids if isinstance(v,str) and v in known]
            attempt.unknown_evidence_id_count=sum(not isinstance(v,str) or v not in known for v in ids)
    try:
        parsed=Interpretation.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors(include_input=False,include_context=False,include_url=False):
            path=[p if isinstance(p,int) or p in fields else '<extra_field>' for p in error['loc']]
            field=path[0] if path else None
            code=('missing_conclusion' if field=='conclusion' and error['type']=='missing' else
                  'invalid_conclusion' if field=='conclusion' else
                  'missing_primary_timeframe' if field=='primary_timeframe' and error['type']=='missing' else
                  'schema_validation')
            issue(code,path)
        return None,attempt
    if attempt.unknown_evidence_id_count: issue('unknown_evidence_ids',('evidence_ids',))
    if len(set(parsed.evidence_ids))!=len(parsed.evidence_ids): issue('duplicate_evidence_ids',('evidence_ids',))
    cited=[d for d in evidence if d['call_id'] in parsed.evidence_ids]
    primary=[d for d in cited if d['interval']==parsed.primary_timeframe]
    if not primary: issue('primary_timeframe_not_cited',('primary_timeframe',))
    if len({(d['symbol'],d['asset_class'],d['source']) for d in cited})!=1:
        issue('inconsistent_evidence_identity',('evidence_ids',))
    for index,text in enumerate(parsed.explanations):
        refs=re.findall(REF,text)
        remaining=free_prose(text)
        attempt.explanation_checks.append({'index':index,
            'reference_count':len(refs),
            'invalid_reference_count':sum(int(idx)>=len(evidence) or key not in reference_values(evidence[int(idx)]) for idx,key in refs),
            'free_digit_count':len(re.findall(r'\d',remaining)),
            'free_chinese_numeral_count':len(re.findall(r'[零一二三四五六七八九十百千万亿]',remaining)),
            'malformed_reference':bool(re.search(r'[{}]',remaining))})
        code=prose_issue(text,evidence)
        if code: issue(code,('explanations',index))
        # Numeric tokens must reference a cited tool result, not merely another available result.
        for idx,_ in refs:
            if int(idx)<len(evidence) and evidence[int(idx)]['call_id'] not in parsed.evidence_ids:
                issue('uncited_numeric_reference',('explanations',index))
    alignments={d['technical_indicators']['maAlignment'] for d in primary}
    if parsed.conclusion=='趋势偏多' and alignments!={'bullish'}: issue('unsupported_conclusion',('conclusion',))
    if parsed.conclusion=='趋势偏空' and alignments!={'bearish'}: issue('unsupported_conclusion',('conclusion',))
    if parsed.conclusion=='周期分歧' and len({d['technical_indicators']['maAlignment'] for d in cited})<2:
        issue('unsupported_conclusion',('conclusion',))
    attempt.valid=not attempt.issues
    return (parsed if attempt.valid else None),attempt

def sections(evidence, interpretation):
    out=[]
    seen=set()
    ordered=sorted(evidence,key=lambda e:e['interval']!=interpretation.primary_timeframe) if interpretation else evidence
    for e in ordered:
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
            refs=re.findall(REF,text)
            if prose_issue(text,evidence): continue
            valid=True
            for idx,key in refs:
                values=reference_values(evidence[int(idx)]) if int(idx)<len(evidence) else {}
                if key not in values: valid=False;break
                text=text.replace('{{'+idx+'.'+key+'}}',str(values[key]))
            if valid: out.append({'title':'模型解释','text':text})
    return out
