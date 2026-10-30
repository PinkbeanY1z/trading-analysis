import asyncio,json
from uuid import uuid4
from typing import TypedDict, Annotated
from langchain_core.messages import AIMessage,HumanMessage,SystemMessage,ToolMessage
from langgraph.graph import StateGraph,START,END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode,tools_condition
from langgraph.checkpoint.memory import InMemorySaver
from .schemas import AgentSettings,ChatResponse,Report,Interpretation
from .tools import RUN,build_tools
from .llm import model
from .report import sections

PROMPT='''你是行情研究工具调用智能体。根据用户任务自主选择工具，可连续调用。沿用会话标的但每轮重新查询证据。黄金使用XAU/USD和gold。不要计算或编造数值。数据不足必须承认。完成后仅返回JSON：{"evidence_ids":[本轮实际工具call id],"conclusion":"趋势偏多|趋势偏空|周期分歧|需要更多证据"}。可增加explanations中文解释数组；具体数字只能使用{{0.rsi}}这类证据索引和technical_indicators字段引用，禁止直接写数字。不输出价格，数值报告由服务器从工具证据构造。只做研究，不下单。'''
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
        async def call(state):
            if state['rounds']>=self.settings.agent_max_rounds:
                return {'error':'max_rounds'}
            try:
                answer=await asyncio.wait_for(bound.ainvoke([SystemMessage(content=PROMPT)]+state['messages']),self.settings.deepseek_timeout)
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
        graph=StateGraph(State)
        graph.add_node('llm',call); graph.add_node('tools',execute); graph.add_node('validate',validate)
        graph.add_edge(START,'llm'); graph.add_conditional_edges('llm',route,{'tools':'tools','validate':'validate'})
        graph.add_edge('tools','llm'); graph.add_edge('validate',END)
        self.graph=graph.compile(checkpointer=self.saver)
    def report(self,state):
        run=RUN.get(); records=run['records']; good=[r for r in records if r['status']=='success']
        evidence=[]
        for r in good:
            for data in r['result'].get('timeframes',[r['result']]):
                evidence.append({'call_id':r['call_id'],**data})
        warnings=list(dict.fromkeys(w for d in evidence for w in d.get('warnings',[])))
        interpretation=None; error=state.get('error','')
        if not error:
            try:
                interpretation=Interpretation.model_validate_json(state['messages'][-1].content)
                if not set(interpretation.evidence_ids)<=set(r['call_id'] for r in good): raise ValueError()
            except Exception: error='invalid_report'
        status='failed' if not evidence else 'partial' if error or len(good)!=len(records) else 'insufficient_data' if any(d.get('insufficient_history') for d in evidence) else 'success'
        if error: warnings.append(error)
        if self.settings.agent_mode=='mock': warnings.append('Mock LLM：预设工具调用脚本，不代表模型自主理解已验证。')
        symbols=list(dict.fromkeys(d['symbol'] for d in evidence))
        assets=list(dict.fromkeys(d['asset_class'] for d in evidence))
        return ChatResponse(thread_id=run['thread'],symbol=symbols[0] if len(symbols)==1 else None,
            asset_class=assets[0] if len(assets)==1 else None,
            timeframes=list(dict.fromkeys(d['interval'] for d in evidence)),
            report=Report(sections=sections(evidence,interpretation),conclusion=interpretation.conclusion if status=='success' and interpretation else '证据不足或执行未完整完成，请查看状态和警告。',evidence=evidence,interpretation=interpretation),
            tool_executions=records,data_sources=list(dict.fromkeys(d['source'] for d in evidence)),warnings=warnings,status=status,mode=self.settings.agent_mode)
    async def chat(self,request):
        thread=request.thread_id or uuid4().hex
        if len(self.locks)>=100 and thread not in self.locks:
            from ..models import MarketError
            raise MarketError('session_limit','开发环境最多100个会话，请重启服务释放',503)
        lock=self.locks.setdefault(thread,asyncio.Lock())
        async with lock:
            run={'thread':thread,'records':[],'cache':{},'lock':asyncio.Lock(),'market':self.market}
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
