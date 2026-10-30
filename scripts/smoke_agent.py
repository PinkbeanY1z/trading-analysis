"""HTTP smoke test. --real uses configured DeepSeek and requires its key."""
import os,sys,json,time,subprocess,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'backend'))
from app.agents.schemas import AgentSettings
real='--real' in sys.argv
if real and not AgentSettings().deepseek_api_key:
    print('SKIPPED: DEEPSEEK_API_KEY is not configured'); sys.exit(2)
env=dict(os.environ)
env['AGENT_MODE']='deepseek' if real else 'mock'
if not real: env['MARKET_PROVIDER']='mock'
p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8138'],cwd=root/'backend',env=env)
try:
    for _ in range(100):
        try:
            urllib.request.urlopen('http://127.0.0.1:8138/health',timeout=1).close(); break
        except OSError: time.sleep(.1)
    req=urllib.request.Request('http://127.0.0.1:8138/api/agent/chat',data=json.dumps({'message':'分析 QQQ 的日线趋势和 RSI14。'}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=150) as r: result=json.load(r)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    (root/'agent-trace.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['tool_executions'], 'No actual tool execution'
    assert result['status'] in ('success','partial','insufficient_data'), result['status']
finally:
    p.terminate(); p.wait(timeout=10)
