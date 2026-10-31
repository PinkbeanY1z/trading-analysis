import asyncio,json
from uuid import uuid4
from typing import TypedDict, Annotated
from langchain_core.messages import AIMessage,HumanMessage,SystemMessage,ToolMessage
from langgraph.graph import StateGraph,START,END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.checkpoint.memory import InMemorySaver
from .schemas import AgentSettings,ChatResponse,Report,Interpretation,ReportValidation
from .tools import RUN,build_tools
from .llm import model
from .report import sections,evidence_from,catalog,validate_interpretation

PROMPT='''你是行情研究工具调用智能体。根据用户任务自主选择工具，可连续调用。沿用会话标的，但每轮重新查询证据。黄金使用XAU/USD和gold。只做研究，不下单。
工具完成后只返回一个符合下方Schema的json对象，不要Markdown或额外字段。conclusion只能选择enum中的一个值，不是把所有选项连接起来。
evidence_ids必须逐字复制【本轮证据目录】的evidence_id；函数名、品种、序号、上一轮的ID都不是本轮证据ID。目录由服务器在实际工具执行后提供，不能自行命名。
primary_timeframe必须明确本轮用户主要询问的周期；追问某周期时，可以查询其他周期作背景，但不能让背景替代主要周期。该主要周期必须有被引用的本轮工具证据。
explanations建议最多两句，每句不超过八十个中文字符（含引用）。只概括趋势与风险，不重复完整数值表格，不写序号和数量词。完整指标和价格表由服务器生成。
不得编造价格或读数。所有值只能逐字复制本轮目录reference_tokens中的完整占位符，且对应证据必须被引用；不要自行编造字段名。目录支持均线、RSI、ATR、斜率、偏离率、支撑阻力等真实字段。标签MA20、MA50、MA200、RSI14、ATR14可以直接写，其他读数、阈值和百分比不允许直接写成数字或中文数词。非数值解释用普通中文。不提供没有证据支持的结论。
数据不足必须承认；模拟行情必须说明为模拟历史，不能声称当前真实市场。数值报告由服务器从工具证据构造。
JSON Schema：'''+json.dumps(Interpretation.model_json_schema(),ensure_ascii=False)
class State(TypedDict):
    messages: Annotated[list,add_messages]
    rounds: int
    error: str
    response: dict

class Agent:
    def __init__(self,market,settings=None,llm=None):
        self.market=market; self.settings=settings or AgentSettings()
        self.llm=llm or model(self.settings)
        self.saver=InMemorySaver(); self.locks={}
        tools=build_tools(); node=ToolNode(tools,handle_tool_errors=lambda e:'{"error":"工具参数或执行错误"}')
        bound=self.llm.bind_tools(tools) if self.llm else None
        report_model=(self.llm.bind_report() if hasattr(self.llm,'bind_report') else
                      self.llm.bind(response_format={'type':'json_object'},max_tokens=2000) if hasattr(self.llm,'bind') else self.llm)
        async def call(state):
            if state['rounds']>=self.settings.agent_max_rounds:
                return {'error':'max_rounds'}
            try:
                messages=[SystemMessage(content=PROMPT)]+state['messages']
                evidence=evidence_from(RUN.get()['records'])
                if evidence:
                    messages.append(SystemMessage(content='本轮证据目录（仅引用这些ID与index）：'+json.dumps(catalog(evidence),ensure_ascii=False)))
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
                RUN.get()['records'].append(record)
                output.append(ToolMessage(content=json.dumps(payload,ensure_ascii=False),tool_call_id=tc['id'],status='error' if failed else 'success'))
            return {'messages':output}
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
            evidence=evidence_from(run['records'])
            payload={'user_tasks':[m.content for m in state['messages'] if isinstance(m,HumanMessage)][-2:],
                     'evidence_catalog':catalog(evidence),
                     'evidence':[{k:v for k,v in d.items() if k not in {'ohlcv'}} for d in evidence],
                     'validation_issues':run['report_attempts'][-1].issues,
                     'explanation_checks':run['report_attempts'][-1].explanation_checks}
            try:
                answer=await asyncio.wait_for(report_model.ainvoke([
                    SystemMessage(content=PROMPT+'\n这是受限报告修复：只能使用给定证据，不能调用工具。依据校验错误重新生成JSON。'),
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
        evidence=evidence_from(records)
        warnings=list(dict.fromkeys(w for d in evidence for w in d.get('warnings',[])))
        interpretation=None; error=state.get('error',''); reasons=[]
        if not error:
            content=state['messages'][-1].content if state.get('messages') else ''
            finish_reason=state['messages'][-1].response_metadata.get('finish_reason','unknown') if state.get('messages') else 'unknown'
            interpretation,attempt=validate_interpretation(content,evidence,finish_reason)
            run['report_attempts'].append(attempt)
            if not attempt.valid:
                error='invalid_report'
                reasons.extend(i['code'] for i in attempt.issues)
        else: reasons.append(error)
        if not evidence: reasons.append('no_evidence')
        if len(good)!=len(records): reasons.append('tool_execution_failed')
        insufficient=any(d.get('insufficient_history') or any(d['technical_indicators'].get(k) is None for k in ('ma20','ma50','ma200','rsi','atr')) for d in evidence)
        if insufficient: reasons.append('insufficient_history')
        if len({(d['symbol'],d['asset_class'],d['source']) for d in evidence})>1:
            reasons.append('inconsistent_evidence_identity'); interpretation=None
        status=('failed' if not evidence else 'insufficient_data' if insufficient else
                'partial' if reasons else 'success')
        if error: warnings.append(error)
        if interpretation and run['report_retries']: warnings.append('report_repaired')
        if self.settings.agent_mode=='mock': warnings.append('Mock LLM：预设工具调用脚本，不代表模型自主理解已验证。')
        symbols=list(dict.fromkeys(d['symbol'] for d in evidence))
        assets=list(dict.fromkeys(d['asset_class'] for d in evidence))
        primary=interpretation.primary_timeframe if interpretation else None
        timeframes=list(dict.fromkeys(d['interval'] for d in evidence))
        if primary: timeframes.sort(key=lambda interval:interval!=primary)
        return ChatResponse(thread_id=run['thread'],symbol=symbols[0] if len(symbols)==1 else None,
            asset_class=assets[0] if len(assets)==1 else None,
            timeframes=timeframes,primary_timeframe=primary,status_reasons=list(dict.fromkeys(reasons)),
            report=Report(sections=sections(evidence,interpretation),conclusion=interpretation.conclusion if status=='success' and interpretation else '证据不足或执行未完整完成，请查看状态和警告。',evidence=evidence,interpretation=interpretation,
                          validation=ReportValidation(valid=interpretation is not None,attempts=run['report_attempts'],referenced_evidence_ids=interpretation.evidence_ids if interpretation else [])),
            tool_executions=records,data_sources=list(dict.fromkeys(d['source'] for d in evidence)),warnings=warnings,status=status,mode=self.settings.agent_mode)
    async def chat(self,request):
        thread=request.thread_id or uuid4().hex
        if len(self.locks)>=100 and thread not in self.locks:
            from ..models import MarketError
            raise MarketError('session_limit','开发环境最多100个会话，请重启服务释放',503)
        lock=self.locks.setdefault(thread,asyncio.Lock())
        async with lock:
            run={'thread':thread,'records':[],'cache':{},'lock':asyncio.Lock(),'market':self.market,'report_attempts':[],'report_retries':0}
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
