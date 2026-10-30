# Trading-Analysis｜行情结构分析智能体

现有实现使用 Vue 3 / Vite / Lightweight Charts、Python FastAPI、LangGraph 和兼容 DeepSeek 的原生工具调用接口。仅用于行情结构研究，不下单。

## 启动
在仓库根目录执行（Python 3.11+、Node.js）：
```powershell
py -3 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install -r backend/requirements-tested.txt
./start-backend.ps1 -Mock
```
另一终端：
```powershell
cd frontend
pnpm install
pnpm run dev
```
打开 http://localhost:5173 。Vite 将 /api 代理到 FastAPI 3000端口；API文档为 http://localhost:3000/docs 。

真实模式：在 backend/.env 不存在时复制 backend/.env.example，配置 Twelve Data 和 DeepSeek 密钥，再运行 `./start-backend.ps1`。不要提交 .env。前端可用 VITE_API_BASE 覆盖 API 地址。

## 当前功能与限制
- 行情、指标、规则和Agent API；缓存与参数校验。
- LangGraph模型/工具循环、内存会话、四种工具、结构化证据和报告模板。
- QQQ/黄金切换、周期选择、均线、RSI、成交量及Agent对话联动。
- Mock模型是固定QQQ日线脚本，不代表自然语言理解已验证；模拟行情不是实时数据。
- 无真实密钥时不能验证DeepSeek或Twelve Data。多周期浏览器场景、错误注入与移动端细节尚未完整验收。
- 内存会话重启丢失，无用户认证，仅适合本地开发。旧 backend/src 仅作迁移参考，新页面不依赖旧Node AI接口。

## 验证
```powershell
& ./.venv/Scripts/python.exe -m pytest -c backend/pyproject.toml -q -p no:cacheprovider
node frontend/node_modules/vite/bin/vite.js build frontend
& ./.venv/Scripts/python.exe scripts/smoke_agent.py
```
实际阶段记录、架构和限制见 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)。生成的日志、轨迹、缓存与虚拟环境不纳入Git。

## Git工作流程
每个阶段完成后先运行相应测试和构建，通过后创建说明清晰的提交并立即推送到 `upgrade/python-agent`。每次汇报提供提交Hash和实际推送状态。不得自动修改或合并main，不得强制推送。远程同名分支存在时先fetch检查差异；推送失败保留本地提交并如实报告。

## 第五阶段验证入口
CI定义见 `.github/workflows/ci.yml`，仅执行离线测试和锁文件构建。当前本地72项测试通过；远程CI状态须以Actions实际运行结果为准。
真实API矩阵：`.venv/Scripts/python.exe scripts/verify_live.py`。缺少项目密钥时输出SKIPPED，默认CI不运行此脚本。
当前市场结构包含严格局部极值、RSI背离候选和量价规则；未完成K线仍标为暂定信号。完整浏览器异常/移动端验收与真实API验证尚未完成，详见实施记录。
