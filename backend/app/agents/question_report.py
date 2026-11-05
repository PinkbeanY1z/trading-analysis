"""Validate question coverage and resolve numbers from actual execution evidence."""
import json
import math
import re
from pydantic import ValidationError
from .question_schema import QuestionAnswerDraft, AnswerBlock, AnswerFact
from .schemas import ReportAttempt
from .report import REF, free_prose, reference_values


def values_for(evidence):
    values = {} if evidence.get('kind') == 'calculation' else reference_values(evidence)
    def visit(data, prefix):
        for key, value in data.items():
            path = prefix + key
            if isinstance(value, dict): visit(value, path + '.')
            elif isinstance(value, (int,float,str)) and not isinstance(value,bool): values[path] = value
    visit(evidence.get('calculation',{}), 'calculation.')
    visit(evidence.get('analysis',{}), 'analysis.')
    return values


def evidence_catalog(evidence):
    result = []
    for index, data in enumerate(evidence):
        values = values_for(data)
        # Expose measured values once, never send hundreds of raw candles to the model.
        result.append({'index':index, 'evidence_id':data['call_id'],
                       'symbol':data.get('symbol'), 'interval':data.get('interval'),
                       'source':data['source'], 'snapshot_id':data.get('snapshot_id'),
                       'calculation_id':data.get('calculation_id'),
                       'references':{key:{'token':'{{'+str(index)+'.'+key+'}}','value':value}
                                     for key,value in values.items()}})
    return result


def display_value(value, key=''):
    if value is None: return '不可用'
    if isinstance(value,(int,float)):
        if not math.isfinite(value): return '不可用'
        if key.split('.')[-1] == 'volume':
            for unit, scale in [('亿',1e8),('百万',1e6),('万',1e4)]:
                if abs(value)>=scale: return f'{value/scale:,.2f}'+unit
        return f'{value:,.2f}'
    labels={'bullish':'多头排列','bearish':'空头排列','mixed':'混合排列','unknown':'未确认',
            'above':'上方','below':'下方','at':'附近','broken':'已跌破','crossed':'已越过',
            'potential':'潜在参考，尚未确认','unconfirmed':'未确认','unverified':'未验证',
            'not_supported':'当前结构不支持','oversold':'超卖区','overbought':'超买区','neutral':'中性区',
            'up':'上行','down':'下行','flat':'走平','user_assumption':'用户假设',
            'theoretical_required_rate':'理论盈亏平衡门槛','evaluated':'已测算',
            'structural_hypothesis':'结构假设','unavailable':'不可用','long':'多头','short':'空头'}
    return labels.get(value, value if not re.search(r'[a-z]+_[a-z]+',value) else '详见技术详情')


def render_text(text, evidence):
    return re.sub(REF, lambda match: display_value(values_for(evidence[int(match[1])])[match[2]],match[2]), text)


def required_kinds(message):
    """Completeness guard only; this never selects tools or generates a research plan."""
    required=set()
    if re.search(r'胜率|成功概率|win\s*rate',message,re.I): required.add('win_rate')
    if re.search(r'盈亏比|风险收益比|reward.{0,4}risk',message,re.I): required.add('risk_reward')
    if re.search(r'支撑|阻力',message): required.add('levels')
    if re.search(r'现在|目前|当前',message) and re.search(r'买入|购入|入场',message) and re.search(r'时机|合适|可以|值得|好',message):
        required.add('entry')
    return required


def validate_answer(content, evidence, plan, message, finish_reason='unknown'):
    attempt=ReportAttempt(content_type='text' if isinstance(content,str) else 'unsupported')
    if finish_reason in ('stop','length','tool_calls','content_filter'): attempt.finish_reason=finish_reason
    def issue(code, path=()): attempt.issues.append({'code':code,'path':list(path)})
    if not isinstance(content,str) or not content.strip():
        issue('missing_report'); return None,attempt
    try: raw=json.loads(content)
    except ValueError:
        attempt.json_shape='invalid'; issue('invalid_json'); return None,attempt
    attempt.json_shape='object' if isinstance(raw,dict) else 'non_object'
    fields=set(QuestionAnswerDraft.model_fields)
    if isinstance(raw,dict):
        attempt.field_types={key:type(value).__name__ for key,value in raw.items() if key in fields}
        attempt.extra_field_count=len(set(raw)-fields)
    try: answer=QuestionAnswerDraft.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors(include_input=False,include_context=False,include_url=False):
            issue('schema_validation',[p if isinstance(p,int) or p in fields else '<field>' for p in error['loc']])
        return None,attempt
    if plan is None:
        issue('missing_research_plan'); return None,attempt
    known={d['call_id'] for d in evidence}
    attempt.evidence_ids=[value for value in answer.evidence_ids if value in known]
    attempt.unknown_evidence_id_count=sum(value not in known for value in answer.evidence_ids)
    if attempt.unknown_evidence_id_count: issue('unknown_evidence_ids')
    if len(set(answer.evidence_ids))!=len(answer.evidence_ids): issue('duplicate_evidence_ids')
    cited=[d for d in evidence if d['call_id'] in answer.evidence_ids]
    market=[d for d in cited if d.get('kind')!='calculation']
    if market:
        if not any(d['interval']==answer.primary_timeframe for d in market): issue('primary_timeframe_not_cited')
        if plan.primary_timeframe != answer.primary_timeframe: issue('plan_primary_timeframe_mismatch')
        if len({(d['symbol'],d['asset_class'],d['source']) for d in market})!=1: issue('inconsistent_evidence_identity')
    elif answer.primary_timeframe is not None: issue('calculation_has_no_market_timeframe')
    question_ids={q.id for q in plan.questions}; kinds={q.kind for q in plan.questions}
    if not required_kinds(message)<=kinds: issue('missing_user_subquestion')
    covered=set(); visible=[]; watch_count=0
    for index,block in enumerate(answer.answer_blocks):
        if not block.detail: covered.update(block.question_ids)
        if not set(block.question_ids)<=question_ids: issue('unknown_question_id',('answer_blocks',index))
        if not set(block.evidence_ids)<=set(answer.evidence_ids): issue('uncited_block_evidence',('answer_blocks',index))
        if block.kind not in ('limitation',) and not block.evidence_ids: issue('missing_block_evidence',('answer_blocks',index))
        if block.kind=='watch': watch_count+=1
        if not block.detail: visible.append(block.title+block.text)
        for text in (block.title,block.text):
            remainder=free_prose(text)
            # Reading counts are not prices. The observations-only request may ask for three.
            remainder=re.sub(r'三个|三项|三点|两项|两个|第一|第二|第三','',remainder)
            # Single Chinese numerals in ordinary language (e.g. 单一/一侧) are not
            # readings. Numeric phrases and financial amounts still require tokens.
            chinese_amount=(r'[零一二三四五六七八九十百千万亿]{2,}|'
                            r'[零一二三四五六七八九十百千万亿]+(?:元|美元|点|成|倍|%)|'
                            r'(?:价格|收盘|读数|盈亏比|目标价|止损价)(?:为|是|在|约|：|:)?[零一二三四五六七八九十百千万亿]+')
            if re.search(r'[\d{}]',remainder) or re.search(chinese_amount,remainder): issue('unverified_numeric_prose',('answer_blocks',index))
            if re.search(r"\{['\"]|[a-z]+_[a-z]+|\d{4}-\d{2}-\d{2}T",re.sub(REF,'',text)):
                issue('unformatted_protocol_content',('answer_blocks',index))
            for number,key in re.findall(REF,text):
                position=int(number)
                if position>=len(evidence) or key not in values_for(evidence[position]): issue('invalid_numeric_reference',('answer_blocks',index))
                elif evidence[position]['call_id'] not in block.evidence_ids: issue('uncited_numeric_reference',('answer_blocks',index))
                elif 'breakeven_win_rate' in key and '盈亏平衡' not in block.text+block.title:
                    issue('theoretical_rate_mislabeled',('answer_blocks',index))
            if re.search(r'胜率.{0,5}(很高|较高|可达|预计|估计)|保证获利|稳赚|已经确认企稳|多次测试确认的强',text):
                issue('unsupported_trading_claim',('answer_blocks',index))
            # A real RSI/price token is still not a measured historical win rate.
            # Check semantic roles as well as the existence of numerical evidence.
            for clause in re.split(r'[。；;\n]', text):
                if re.search(r'胜率|命中率|成功概率', clause):
                    for _, key in re.findall(REF, clause):
                        if key != 'calculation.breakeven_win_rate_percent' or '盈亏平衡' not in clause:
                            issue('unverified_historical_win_rate',('answer_blocks',index))
        for fact in block.facts:
            matches=[d for d in cited if d['call_id']==fact.evidence_id and (fact.interval is None or d.get('interval')==fact.interval)]
            if fact.evidence_id not in block.evidence_ids or len(matches)!=1:
                issue('invalid_fact_reference',('answer_blocks',index)); continue
            value=values_for(matches[0]).get(fact.metric)
            if not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value):
                issue('invalid_fact_reference',('answer_blocks',index))
            if 'historical_win_rate' in fact.metric: issue('unverified_historical_win_rate',('answer_blocks',index))
            if re.search(r'胜率|命中率|成功概率', fact.label) and (
                fact.metric != 'calculation.breakeven_win_rate_percent' or '盈亏平衡' not in fact.label
            ):
                issue('unverified_historical_win_rate',('answer_blocks',index))
    if covered != question_ids: issue('unanswered_subquestion')
    if watch_count>3: issue('too_many_observation_conditions')
    if len(re.findall(r'[\u4e00-\u9fff]',''.join(visible)))>450: issue('answer_too_verbose')
    if kinds=={'watch'} and (watch_count<1 or any(b.kind not in ('watch','direct_answer') and not b.detail for b in answer.answer_blocks)):
        issue('irrelevant_answer_blocks')
    if 'win_rate' in kinds:
        ids={q.id for q in plan.questions if q.kind=='win_rate'}
        if not any(ids & set(b.question_ids) and '未验证' in b.text for b in answer.answer_blocks): issue('missing_unverified_win_rate')
        if any('historical_win_rate' in key for b in answer.answer_blocks for _,key in re.findall(REF,b.text)):
            issue('unverified_historical_win_rate')
    if 'entry' in kinds:
        primary=[d for d in market if d['interval']==answer.primary_timeframe]
        if answer.entry_stance=='not_applicable': issue('missing_entry_assessment')
        if not primary or not all(d.get('analysis',{}).get('entry_assessment') for d in primary): issue('missing_entry_evidence')
        if answer.entry_stance=='not_supported' and not any(d['analysis']['entry_assessment']['long']=='not_supported' for d in primary):
            issue('unsupported_entry_stance')
    if 'levels' in kinds:
        primary=[d for d in market if d['interval']==answer.primary_timeframe]
        if not primary or not any(d.get('analysis',{}).get('rolling_levels') for d in primary): issue('missing_level_evidence')
    if 'risk_reward' in kinds:
        if not any(d.get('calculation') or d.get('trade_plan') for d in cited): issue('missing_trade_plan_evaluation')
    if answer.trade_plan_evidence_id:
        plans=[d for d in cited if d['call_id']==answer.trade_plan_evidence_id and d.get('calculation')]
        if len(plans)!=1: issue('invalid_trade_plan_reference')
    if answer.entry_stance=='conditional_hypothesis':
        if not any(d.get('trade_plan',{}).get('available') for d in cited): issue('missing_conditional_hypothesis')
    attempt.valid=not attempt.issues
    return (answer if attempt.valid else None),attempt


def render_answer(answer, evidence):
    blocks=[]
    for index,draft in enumerate(answer.answer_blocks):
        facts=[]
        for selection in draft.facts:
            data=next(d for d in evidence if d['call_id']==selection.evidence_id and (selection.interval is None or d.get('interval')==selection.interval))
            facts.append(AnswerFact(label=selection.label,value=values_for(data)[selection.metric],format=selection.format,
                                    evidence_id=selection.evidence_id,metric=selection.metric))
        blocks.append(AnswerBlock(id=f'a{index+1}',kind=draft.kind,title=render_text(draft.title,evidence),
            text=render_text(draft.text,evidence),question_ids=draft.question_ids,evidence_ids=draft.evidence_ids,
            facts=facts,detail=draft.detail))
    trade_plan=next((d['calculation'] for d in evidence if d['call_id']==answer.trade_plan_evidence_id and d.get('calculation')),None)
    return blocks,trade_plan


def fallback_blocks(plan):
    return [AnswerBlock(id='fallback',kind='limitation',title='本次研究未完整完成',
        text='回答未通过证据或执行校验，暂不能给出可信的完整结论。已取得的工具记录可在技术详情核对；请修正条件或重试。',
        question_ids=[q.id for q in plan.questions] if plan else [],evidence_ids=[])]
