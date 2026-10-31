from app.agents.report import sections
from app.agents.schemas import Interpretation

def test_report_numbers():
    e={'symbol':'QQQ','interval':'1day','source':'mock','last_updated':'today','technical_indicators':{'maAlignment':'bullish','rsi':55},'support_resistance':{},'warnings':[]}
    i=Interpretation(evidence_ids=['a'],primary_timeframe='1day',conclusion='需要更多证据',explanations=['动量读数为{{0.rsi}}。','价格999。','价格９９９。','读数{{0.fake}}'])
    result=sections([e,e],i)
    prose=[r['text'] for r in result if r['title']=='模型解释']
    assert prose==['动量读数为55。']
    assert len([r for r in result if r['title']=='市场趋势'])==1

def test_empty_report_fallback():
    assert sections([],None)==[]
