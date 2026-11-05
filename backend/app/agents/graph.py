import asyncio,json
from uuid import uuid4
from typing import TypedDict, Annotated
from langchain_core.messages import AIMessage,HumanMessage,SystemMessage,ToolMessage
from langgraph.graph import StateGraph,START,END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.checkpoint.memory import InMemorySaver
from .schemas import AgentSettings,ChatResponse,Report,Interpretation,ReportValidation
from .tools import RUN,build_tools,collect_evidence
from .planning import planning_tool
from .question_schema import QuestionAnswerDraft
from .question_report import evidence_catalog,validate_answer,render_answer,fallback_blocks
from .audit import evidence_digest
from .llm import model
from .report import sections,evidence_from,catalog,validate_interpretation

PROMPT='''你是行情研究工具调用智能体。根据用户任务自主选择工具，可连续调用。沿用会话标的，但每轮重新查询证据。黄金使用XAU/USD和gold。只做研究，不下单。
工具完成后只返回一个符合下方Schema的json对象，不要Markdown或额外字段。conclusion只能选择enum中的一个值，不是把所有选项连接起来。
evidence_ids必须逐字复制【本轮证据目录】的evidence_id；函数名、品种、序号、上一轮的ID都不是本轮证据ID。目录由服务器在实际工具执行后提供，不能自行命名。
primary_timeframe必须明确本轮用户主要询问的周期；追问某周期时，可以查询其他周期作背景，但不能让背景替代主要周期。该主要周期必须有被引用的本轮工具证据。
conclusion_basis必须声明判断依据：ma_alignment仅用于均线多头/空头排列；price_structure用于主要周期收盘价确实突破工具支撑/阻力，或价格同在MA20/MA50的一侧且两条斜率同向；multi_timeframe用于至少两个实际周期的结构分歧；insufficient_evidence用于需要更多证据。长期均线排列与短期价格可能方向不同，应明确所选依据，不强行将价格结构当成均线排列。任何依据仍须通过工具数据校验。
explanations建议最多两句，每句不超过八十个中文字符（含引用）。只概括趋势与风险，不重复完整数值表格，不写序号和数量词。完整指标和价格表由服务器生成。
不得编造价格或读数。所有值只能逐字复制本轮目录reference_tokens中的完整占位符，且对应证据必须被引用；不要自行编造字段名。目录支持均线、RSI、ATR、斜率、偏离率、支撑阻力等真实字段。标签MA20、MA50、MA200、RSI14、ATR14可以直接写，其他读数、阈值和百分比不允许直接写成数字或中文数词。非数值解释用普通中文。不提供没有证据支持的结论。
数据不足必须承认；模拟行情必须说明为模拟历史，不能声称当前真实市场。数值报告由服务器从工具证据构造。
JSON Schema：'''+json.dumps(Interpretation.model_json_schema(),ensure_ascii=False)
LEGACY_PROMPT=PROMPT
PROMPT='''你是问题优先的金融研究助手，不下单。先理解本轮问题及子问题，再自主选择必要工具，不固定遍历全部工具。沿用会话品种，追问的周期为主周期；默认研究日线，黄金用XAU/USD与gold。
每轮必须原生调用plan_research，列出用户实际问题、主周期与必要工具。可与首个专项工具同轮调用。计划不是行情证据。每个问题都要回答，不要增加无关任务。无需在每个工具前调用get_market_data；它只用于原始K线。calculate_technical_indicators只用于指定读数；结构问题通常仅analyze_market_structure即可；compare_timeframes已完成多周期研究，不再逐周期重复查询。禁止为展示调用轨迹而重复同样工作。
买入时机、胜率、盈亏比分别作为entry/win_rate/risk_reward子问题。先用结构证据判断支持和反对；需要候选方案时自主调用evaluate_trade_plan(mode=candidate)，接受“无法形成合理方案”的结果，明确缺少的确认，不凑价位。滚动区间极值不是多次测试确认的强支撑；已跌破的低点不再是当前有效支撑。模型不能自行给出任何入场/止损/目标价，candidate只能由Python取实际结构锚点。
用户明确给定价格时使用evaluate_trade_plan(mode=evaluate)，只复制本轮用户给定的入场/止损/目标数字及成本；纯算术无需行情，primary_timeframe为null。未提供的成本不传零、不编造。所有盈亏比与理论盈亏平衡门槛由Python计算，实际历史胜率一律未验证，禁止估计概率、宣称胜率较高或把理论门槛当实际胜率。ATR只用于检查正常波动风险，不机械选择止损。
完成研究后只输出下方Schema的JSON，不加Markdown。默认可见回答合计150至250个中文字，三个以内子问题硬上限300字。每个限制只说一次；直接结论只选最关键的支持与反对线索，不堆MA/RSI/ATR数值。补充指标必须detail=true折叠。方案不可用只解释工具明确返回的原因，未检查到的锚点不得说成已确认缺失。不要输出完整指标列表、固定章节或重复周期。answer_blocks逐个回答plan中的question_ids；第一块直接回答，随后仅关键依据、方案/限制和最多三个简短观察条件。只问观察信号时，只给最多三个watch块。facts通常留空，避免与行文重复。
evidence_ids逐字复制本轮目录的evidence_id，不引用上一轮。主周期必须有本轮工具证据。所有数值（包括价格、阈值、盈亏比、百分比）只能使用本轮目录reference的完整token，如{{0.ma20}}；不要手写数字或中文数词，MA20/50/200、RSI14、前20根、4小时等规则标签除外。服务器会解析引用并统一格式化，模型不做计算。每个token的index指向目录中的一个证据，该证据的evidence_id必须同时列在当前块和顶层evidence_ids，不能因为快照相同而改引另一个ID。方案不可用也是有效评估结果：risk_reward块必须引用evaluate_trade_plan对应的证据ID并解释目录trade_plan.reasons中的实际原因；修复时不能漏掉它。trade_plan_evidence_id只有实际存在calculation时才填写，否则null。
entry_stance用not_supported/await_confirmation/conditional_hypothesis，不提供“已确认立即买入”；数据时段和入场触发未确认必须如实说明。针对胜率的问题只说明缺少匹配策略的历史回测，明确“实际历史胜率未验证”，这一段不引用RSI或其他指标数字，针对无方案的盈亏比说明缺少的锚点、触发、成本条件。模型可解释和提出条件，但不得将未来观察条件表述为已发生事实。Mock明确为模拟历史，不能称当前真实市场。
JSON Schema：'''+json.dumps(QuestionAnswerDraft.model_json_schema(),ensure_ascii=False)

class State(TypedDict):
    messages: Annotated[list,add_messages]
    rounds: int
    error: str
    response: dict
    research_plan: dict

class Agent:
    def __init__(self,market,settings=None,llm=None):
        self.market=market; self.settings=settings or AgentSettings()
        self.llm=llm or model(self.settings)
        self.saver=InMemorySaver(); self.locks={}
        tools=[planning_tool(),*build_tools()]; node=ToolNode(tools,handle_tool_errors=lambda e:'{"error":"工具参数或执行错误"}')
        bound=self.llm.bind_tools(tools) if self.llm else None
        report_model=(self.llm.bind_report() if hasattr(self.llm,'bind_report') else
                      self.llm.bind(response_format={'type':'json_object'},max_tokens=2000) if hasattr(self.llm,'bind') else self.llm)
        async def call(state):
            if state['rounds']>=self.settings.agent_max_rounds:
                return {'error':'max_rounds'}
            try:
                messages=[SystemMessage(content=PROMPT)]+state['messages']
                evidence=collect_evidence(RUN.get()['records'])
                if evidence:
                    entries=evidence_catalog(evidence) if RUN.get().get('plan') else catalog(evidence)
                    messages.append(SystemMessage(content='本轮证据目录（仅引用这些ID与index）：'+json.dumps(entries,ensure_ascii=False)))
                answer=await asyncio.wait_for(bound.ainvoke(messages),self.settings.deepseek_timeout)
                if len(answer.tool_calls)>8: return {'error':'too_many_tools'}
                return {'messages':[answer],'rounds':state['rounds']+1}
            except Exception: return {'error':'model_error'}
        def route(state):
            if state.get('error'): return 'validate'
            return 'tools' if tools_condition(state)=='tools' else 'validate'
        async def execute(state):
            # Invoke official ToolNode per call; every requested call gets one sanitized record.
            output=[]
            for tc in state['messages'][-1].tool_calls:
                try:
                    single=AIMessage(content='',tool_calls=[tc])
                    result=await asyncio.wait_for(node.ainvoke({'messages':[single]}),self.settings.agent_tool_timeout)
                    msg=result['messages'][0]
                    payload=json.loads(msg.content)
                    failed=msg.status=='error' or 'error' in payload
                except Exception:
                    payload={'error':'工具执行失败或超时'}; failed=True
                record=dict(call_id=tc['id'],name=tc['name'],arguments=tc['args'],status='failed' if failed else 'success',result=payload)
                if not failed:
                    record['evidence_digests']=[dict(interval=d.get('interval'),snapshot_id=d.get('snapshot_id'),
                        calculation_id=d.get('calculation_id'),digest=evidence_digest(d)) for d in collect_evidence([record])]
                RUN.get()['records'].append(record)
                output.append(ToolMessage(content=json.dumps(payload,ensure_ascii=False),tool_call_id=tc['id'],status='error' if failed else 'success'))
            return {'messages':output,'research_plan':RUN.get()['plan'].model_dump() if RUN.get().get('plan') else {}}
        def validate(state):
            return {'response':self.report(state).model_dump()}
        def after_validation(state):
            run=RUN.get()
            can_repair=(not state.get('error') and state['response']['status']=='partial'
                        and not state['response']['report']['validation']['valid']
                        and run['records'] and all(r['status']=='success' for r in run['records'])
                        and run['report_retries']<self.settings.agent_report_retries
                        and state['rounds']<self.settings.agent_max_rounds)
            return 'repair' if can_repair else END
        async def repair(state):
            run=RUN.get(); run['report_retries']+=1
            evidence=collect_evidence(run['records'])
            payload={'user_tasks':[m.content for m in state['messages'] if isinstance(m,HumanMessage)][-2:],
                     'research_plan':run['plan'].model_dump() if run.get('plan') else None,
                     'evidence_catalog':evidence_catalog(evidence) if run.get('plan') else catalog(evidence),
                     'tool_results':[r['result'] for r in run['records']],
                     'validation_issues':run['report_attempts'][-1].issues,
                     'explanation_checks':run['report_attempts'][-1].explanation_checks}
            try:
                answer=await asyncio.wait_for(report_model.ainvoke([
                    SystemMessage(content=(PROMPT if state['response']['report']['protocol_version']==2 else LEGACY_PROMPT)+'\n这是受限报告修复：只能使用给定证据，不能调用工具。依据校验错误重新生成JSON。'),
                    HumanMessage(content=json.dumps(payload,ensure_ascii=False))]),self.settings.deepseek_timeout)
                if answer.tool_calls: return {'error':'report_repair_requested_tools'}
                return {'messages':[answer],'rounds':state['rounds']+1}
            except Exception: return {'error':'report_repair_model_error'}
        graph=StateGraph(State)
        graph.add_node('llm',call); graph.add_node('tools',execute); graph.add_node('validate',validate); graph.add_node('repair',repair)
        graph.add_edge(START,'llm'); graph.add_conditional_edges('llm',route,{'tools':'tools','validate':'validate'})
        graph.add_edge('tools','llm'); graph.add_conditional_edges('validate',after_validation,{'repair':'repair',END:END})
        graph.add_edge('repair','validate')
        self.graph=graph.compile(checkpointer=self.saver)
    def report(self,state):
        run=RUN.get(); records=run['records']; good=[r for r in records if r['status']=='success']
        try: evidence=collect_evidence(records)
        except (ValueError,KeyError): evidence=[]; state={**state,'error':'evidence_snapshot_mismatch'}
        warnings=list(dict.fromkeys(w for d in evidence for w in d.get('warnings',[])))
        interpretation=None; answer=None; error=state.get('error',''); reasons=[]
        content=state['messages'][-1].content if state.get('messages') else ''
        try: is_v2=self.settings.agent_report_protocol=='question_first' or bool(run.get('plan')) or isinstance(content,str) and json.loads(content).get('protocol_version')==2
        except (ValueError,AttributeError): is_v2=self.settings.agent_report_protocol=='question_first' or bool(run.get('plan'))
        if not error:
            finish_reason=state['messages'][-1].response_metadata.get('finish_reason','unknown') if state.get('messages') else 'unknown'
            if is_v2: answer,attempt=validate_answer(content,evidence,run.get('plan'),run['user_message'],finish_reason)
            else: interpretation,attempt=validate_interpretation(content,evidence,finish_reason)
            run['report_attempts'].append(attempt)
            if not attempt.valid:
                error='invalid_report'
                reasons.extend(i['code'] for i in attempt.issues)
        else: reasons.append(error)
        if not evidence: reasons.append('no_evidence')
        if len(good)!=len(records): reasons.append('tool_execution_failed')
        market=[d for d in evidence if d.get('kind')!='calculation']
        if is_v2:
            kinds={q.kind for q in run['plan'].questions} if run.get('plan') else set()
            insufficient=any((kinds & {'entry','trend','stabilization','timeframes','watch'} and
                              any(d['technical_indicators'].get(key) is None for key in ('ma20','ma50','rsi'))) or
                             ('levels' in kinds and any(d['support_resistance'].get(key) is None for key in ('support','resistance')))
                             for d in market)
            for record in good:
                if record['name']=='calculate_technical_indicators':
                    insufficient=insufficient or any(value is None for value in record['result']['technical_indicators'].values())
        else:
            insufficient=any(d.get('insufficient_history') or any(d['technical_indicators'].get(k) is None for k in ('ma20','ma50','ma200','rsi','atr')) for d in market)
        if insufficient: reasons.append('insufficient_history')
        if len({(d['symbol'],d['asset_class'],d['source']) for d in market})>1:
            reasons.append('inconsistent_evidence_identity'); interpretation=None; answer=None
        status=('failed' if not evidence else 'insufficient_data' if insufficient else
                'partial' if reasons else 'success')
        if error: warnings.append(error)
        if (interpretation or answer) and run['report_retries']: warnings.append('report_repaired')
        if self.settings.agent_mode=='mock': warnings.append('Mock LLM：预设工具调用脚本，不代表模型自主理解已验证。')
        symbols=list(dict.fromkeys(d['symbol'] for d in market))
        assets=list(dict.fromkeys(d['asset_class'] for d in market))
        primary=answer.primary_timeframe if answer else interpretation.primary_timeframe if interpretation else None
        timeframes=list(dict.fromkeys(d['interval'] for d in market))
        if primary: timeframes.sort(key=lambda interval:interval!=primary)
        valid=answer is not None or interpretation is not None
        blocks,trade_plan=render_answer(answer,evidence) if answer and status=='success' else (fallback_blocks(run.get('plan')) if is_v2 else [],None)
        conclusion=(next((b.text for b in blocks if b.kind=='direct_answer'),blocks[0].text) if answer and status=='success' else
                    interpretation.conclusion if interpretation and status=='success' else '证据不足或执行未完整完成，请查看状态和警告。')
        return ChatResponse(thread_id=run['thread'],symbol=symbols[0] if len(symbols)==1 else None,
            asset_class=assets[0] if len(assets)==1 else None,
            timeframes=timeframes,primary_timeframe=primary,status_reasons=list(dict.fromkeys(reasons)),
            report=Report(protocol_version=2 if is_v2 else 1,answer_origin='model_validated' if answer and status=='success' else 'deterministic_fallback' if is_v2 else 'legacy',
                          intent=run.get('plan'),answer_blocks=blocks,entry_stance=answer.entry_stance if answer and status=='success' else 'not_applicable',trade_plan=trade_plan,
                          sections=[] if is_v2 else sections(evidence,interpretation),conclusion=conclusion,evidence=evidence,interpretation=interpretation,
                          validation=ReportValidation(valid=valid,attempts=run['report_attempts'],referenced_evidence_ids=answer.evidence_ids if answer else interpretation.evidence_ids if interpretation else [])),
            tool_executions=records,data_sources=list(dict.fromkeys(d['source'] for d in evidence)),warnings=warnings,status=status,mode=self.settings.agent_mode)
    async def chat(self,request):
        thread=request.thread_id or uuid4().hex
        if len(self.locks)>=100 and thread not in self.locks:
            from ..models import MarketError
            raise MarketError('session_limit','开发环境最多100个会话，请重启服务释放',503)
        lock=self.locks.setdefault(thread,asyncio.Lock())
        async with lock:
            run={'thread':thread,'records':[],'cache':{},'lock':asyncio.Lock(),'market':self.market,'report_attempts':[],'report_retries':0,'plan':None,'user_message':request.message}
            token=RUN.set(run)
            try:
                if not self.llm: return self.report({'error':'missing_deepseek_key'})
                config={'configurable':{'thread_id':thread},'recursion_limit':self.settings.agent_max_rounds*3+5}
                old=await self.graph.aget_state(config)
                if len(old.values.get('messages',[]))>100:
                    return self.report({'error':'session_context_limit'})
                try:
                    result=await asyncio.wait_for(self.graph.ainvoke({'messages':[HumanMessage(content=request.message)],'rounds':0,'error':'','response':{}},config),self.settings.agent_timeout)
                    return ChatResponse.model_validate(result['response'])
                except Exception:
                    # Remove interrupted checkpoint to prevent unmatched tool calls next turn.
                    await self.saver.adelete_thread(thread)
                    return self.report({'error':'agent_timeout_or_failure'})
            finally: RUN.reset(token)
