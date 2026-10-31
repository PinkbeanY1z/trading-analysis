"""Report failures and repairs through the real LangGraph, with an offline model."""
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage, SystemMessage
from app.agents.graph import Agent
from app.agents.llm import ScriptedLLM
from app.agents.schemas import AgentSettings, ChatRequest
from app.market import MarketService
from app.providers import MockProvider


def call(id='a', interval='1day'):
    return AIMessage(content='',tool_calls=[dict(id=id,name='analyze_market_structure',args={'symbol':'QQQ','interval':interval})])


def final(ids=('a',), primary='1day', **changes):
    data={'evidence_ids':list(ids),'primary_timeframe':primary,'conclusion':'需要更多证据',**changes}
    return AIMessage(content=json.dumps(data,ensure_ascii=False))


def execute(plan, provider=None, **config):
    async def run():
        llm=ScriptedLLM(plan)
        agent=Agent(MarketService(provider or MockProvider()),AgentSettings(_env_file=None,agent_mode='mock',**config),llm)
        response=await agent.chat(ChatRequest(message='请分析指定品种的走势'))
        return response,llm
    return asyncio.run(run())


def test_unknown_citation_is_repaired_once_without_repeating_tools():
    response,llm=execute([call(),final(('invented',)),final(explanations=['RSI14读数为{{0.rsi}}。'])])
    assert response.status=='success' and response.report.validation.valid
    attempts=response.report.validation.attempts
    assert len(attempts)==2 and not attempts[0].valid and attempts[1].valid
    assert attempts[0].unknown_evidence_id_count==1 and attempts[0].evidence_ids==[]
    assert response.report.validation.referenced_evidence_ids==['a']
    assert len(response.tool_executions)==1 and len(llm.seen)==3
    assert 'report_repaired' in response.warnings and response.status_reasons==[]
    ledger=[m.content for m in llm.seen[1] if isinstance(m,SystemMessage)][-1]
    assert '"evidence_id": "a"' in ledger
    text=next(s['text'] for s in response.report.sections if s['title']=='模型解释')
    assert str(response.report.evidence[0]['technical_indicators']['rsi']) in text


def test_exhausted_repair_remains_partial_and_drops_invalid_interpretation():
    response,llm=execute([call(),final(('invented',),explanations=['不应展示的内容']),final(('invented-again',))])
    assert response.status=='partial' and 'unknown_evidence_ids' in response.status_reasons
    assert response.report.interpretation is None and not response.report.validation.valid
    assert len(response.report.validation.attempts)==2 and len(llm.seen)==3
    assert not any(s['title']=='模型解释' for s in response.report.sections)
    assert 'invented' not in response.report.validation.model_dump_json()


@pytest.mark.parametrize('content,reason',[
    ('','missing_report'),
    ('```json\n{}\n```','invalid_json'),
    (json.dumps({'evidence_ids':['a'],'primary_timeframe':'1day'}),'missing_conclusion'),
    (json.dumps({'evidence_ids':['a'],'primary_timeframe':'1day','conclusion':'看涨'}),'invalid_conclusion'),
    (json.dumps({'evidence_ids':['a'],'conclusion':'需要更多证据'}),'missing_primary_timeframe'),
    (json.dumps({'evidence_ids':['a'],'primary_timeframe':'1day','conclusion':'需要更多证据','private-field':'do-not-output'}),'schema_validation'),
])
def test_report_failure_reasons_are_distinct_and_sanitized(content,reason):
    response,_=execute([call(),AIMessage(content=content)],agent_report_retries=0)
    assert response.status=='partial' and reason in response.status_reasons
    assert response.report.interpretation is None and response.report.evidence
    assert 'do-not-output' not in response.model_dump_json()
    assert 'private-field' not in response.model_dump_json()


@pytest.mark.parametrize('explanation,reason',[
    ('RSI14读数为99。','unverified_numeric_prose'),
    ('读数{{9.rsi}}。','invalid_numeric_reference'),
    ('读数{{0.not_a_field}}。','invalid_numeric_reference'),
])
def test_unverified_numbers_prevent_success(explanation,reason):
    response,_=execute([call(),final(explanations=[explanation])],agent_report_retries=0)
    assert response.status=='partial' and reason in response.status_reasons
    assert not response.report.validation.valid


def test_numeric_reference_must_be_from_a_cited_result():
    response,_=execute([call(),call('b'),final(explanations=['读数{{1.rsi}}。'])],agent_report_retries=0)
    assert response.status=='partial' and 'uncited_numeric_reference' in response.status_reasons


def test_primary_period_needs_cited_evidence_even_when_other_tools_fetched_it():
    response,_=execute([call(),call('b','4h'),final(primary='4h')],agent_report_retries=0)
    assert response.status=='partial' and 'primary_timeframe_not_cited' in response.status_reasons
    assert response.primary_timeframe is None


def test_round_budget_also_limits_report_repairs():
    response,llm=execute([call(),final(('bad',)),final()],agent_max_rounds=2,agent_report_retries=2)
    assert response.status=='partial' and len(llm.seen)==2
    assert len(response.report.validation.attempts)==1


def test_inadequate_history_has_data_status_without_report_retry():
    class Short(MockProvider):
        async def fetch(self,q): return (await super().fetch(q))[-25:]
    response,llm=execute([call(),final()],provider=Short())
    assert response.status=='insufficient_data' and 'insufficient_history' in response.status_reasons
    assert len(llm.seen)==2


def test_tool_failure_is_distinct_from_report_failure():
    bad=AIMessage(content='',tool_calls=[dict(id='bad',name='analyze_market_structure',args={'symbol':'???'})])
    response,_=execute([call(),bad,final()])
    assert response.status=='partial' and response.report.validation.valid
    assert response.status_reasons==['tool_execution_failed']


def test_previous_turn_citations_are_not_current_evidence():
    async def run():
        llm=ScriptedLLM([call(),final(),call('b','4h'),final(primary='4h')])
        agent=Agent(MarketService(MockProvider()),AgentSettings(_env_file=None,agent_mode='mock',agent_report_retries=0),llm)
        a=await agent.chat(ChatRequest(message='分析日线'))
        d=await agent.chat(ChatRequest(message='它的4小时呢？',thread_id=a.thread_id))
        assert a.status=='success' and d.status=='partial'
        assert 'unknown_evidence_ids' in d.status_reasons
        assert [e['call_id'] for e in d.report.evidence]==['b']
    asyncio.run(run())


def test_directional_conclusion_must_agree_with_primary_evidence():
    response,_=execute([call(),final(conclusion='趋势偏空')],agent_report_retries=0)
    assert response.report.evidence[0]['technical_indicators']['maAlignment']=='bullish'
    assert response.status=='partial' and 'unsupported_conclusion' in response.status_reasons


def test_nested_metric_and_price_references_render_only_actual_tool_values():
    response,_=execute([call(),final(explanations=[
        'MA20斜率为{{0.slopes.ma20}}，偏离率为{{0.priceDistancePct.ma20}}。',
        '支撑为{{0.support_resistance.support}}，均线结构{{0.maAlignment}}。',
    ])])
    assert response.status=='success' and response.report.validation.valid
    evidence=response.report.evidence[0]
    text=''.join(s['text'] for s in response.report.sections if s['title']=='模型解释')
    for value in (evidence['technical_indicators']['slopes']['ma20'],
                  evidence['technical_indicators']['priceDistancePct']['ma20'],
                  evidence['support_resistance']['support']):
        assert str(value) in text
    assert '{{' not in text


def test_fixed_indicator_and_cycle_labels_are_not_unverified_prices():
    response,_=execute([call(),final(explanations=['MA20/50/200三条均线方向一致，4小时与1小时仅作周期标签。一旦变化需重新查询。'])])
    assert response.status=='success' and response.report.validation.valid


def test_cycle_label_exception_does_not_accept_direct_prices():
    response,_=execute([call(),final(explanations=['4小时支撑为12345。'])],agent_report_retries=0)
    assert response.status=='partial' and 'unverified_numeric_prose' in response.status_reasons


def test_reference_paths_are_a_closed_map_and_never_attributes():
    response,_=execute([call(),final(explanations=['读数{{0.ma20.__class__}}。'])],agent_report_retries=0)
    assert response.status=='partial' and 'invalid_numeric_reference' in response.status_reasons


@pytest.mark.parametrize('close,conclusion',[(400,'趋势偏空'),(500,'趋势偏多')])
def test_price_structure_thesis_is_verified_independently_of_long_ma_alignment(close,conclusion):
    class PriceMove(MockProvider):
        async def fetch(self,q):
            rows=await super().fetch(q)
            rows[-1].update(c=close,h=max(rows[-1]['h'],close),l=min(rows[-1]['l'],close))
            return rows
    response,_=execute([call(),final(conclusion=conclusion,conclusion_basis='price_structure')],provider=PriceMove())
    assert response.status=='success' and response.report.validation.valid
    assert response.report.evidence[0]['technical_indicators']['maAlignment']=='bullish'
    assert response.report.interpretation.conclusion_basis=='price_structure'


def test_price_structure_label_cannot_justify_an_unsupported_direction():
    response,_=execute([call(),final(conclusion='趋势偏空',conclusion_basis='price_structure')],agent_report_retries=0)
    assert response.status=='partial' and 'unsupported_conclusion' in response.status_reasons
    assert response.report.validation.attempts[0].proposed_conclusion=='趋势偏空'
    assert response.report.validation.attempts[0].conclusion_basis=='price_structure'


def test_cross_timeframe_conclusion_requires_more_than_one_real_period():
    response,_=execute([call(),final(conclusion='周期分歧',conclusion_basis='multi_timeframe')],agent_report_retries=0)
    assert response.status=='partial' and 'unsupported_conclusion' in response.status_reasons
