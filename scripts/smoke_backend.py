"""Launch real Uvicorn, exercise HTTP, and always stop child process."""
import os, subprocess, time, urllib.request, json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
python=root/'.venv/Scripts/python.exe'
env=dict(os.environ, MARKET_PROVIDER='mock')
p=subprocess.Popen([str(python),'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8137'],cwd=root/'backend',env=env)
try:
    for _ in range(50):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8137/health',timeout=1) as r:
                print('health:',r.status,json.load(r)); break
        except OSError: time.sleep(.1)
    else: raise RuntimeError('server failed to start')
    for path in ['market/kline','market/indicators','analysis/rules']:
        with urllib.request.urlopen('http://127.0.0.1:8137/api/'+path+'?symbol=QQQ&interval=4h') as r:
            data=json.load(r); print(path,r.status,len(data['candles']),data['source'])
finally:
    p.terminate(); p.wait(timeout=10)
