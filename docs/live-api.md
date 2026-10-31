# 真实 API 配置与验收

## 安全配置

程序只从当前进程环境和本项目 `backend/.env` 读取配置。旧 Express 后端使用的 `TWELVEDATA_API_KEY`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL` 名称继续兼容；旧 `AI_PROVIDER` 不控制新 Agent，请使用 `AGENT_MODE`。

在仓库根目录运行。只在文件不存在时复制示例，不覆盖已有密钥：

```powershell
if (-not (Test-Path -LiteralPath backend/.env)) {
    Copy-Item -LiteralPath backend/.env.example -Destination backend/.env
}
notepad backend/.env
```

在本地编辑器填写这两个值；不要在聊天、命令行参数、截图或 Git 提交中填写密钥：

```dotenv
MARKET_PROVIDER=twelve_data
AGENT_MODE=deepseek
TWELVEDATA_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

示例中的空值需要自行填写。模型可使用账户实际支持、具有原生工具调用能力的名称。其他超时、缓存和调用轮次配置见 `backend/.env.example`。进程环境变量优先于 `.env`；若已有旧变量，请在新的终端启动，或只移除你需要更改的同名进程变量，避免旧配置覆盖新文件。前端不需要任何密钥。

检查忽略规则（不显示密钥内容）：

```powershell
git check-ignore backend/.env
```

预期返回 `backend/.env`。不要执行 `Get-Content backend/.env` 后粘贴输出，不要开启含请求 URL 的 HTTP 调试日志；Twelve Data 密钥位于请求查询参数。

## 启动

已经安装依赖时，仓库根目录选择一个模式：

```powershell
# 离线模拟行情 + 固定脚本 LLM；不证明自然语言理解
./start-backend.ps1 -Mock

# 真实 Twelve Data + DeepSeek；缺密钥会显式失败，不回退 Mock
./start-backend.ps1
```

另开终端启动现有 Vue 前端：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run dev --host 127.0.0.1
```

打开 `http://127.0.0.1:5173`，FastAPI API 文档为 `http://127.0.0.1:3000/docs`。本地没有 `pnpm` 命令但已有依赖时，可使用 `node node_modules/vite/bin/vite.js --host 127.0.0.1`。完整首次安装方法见 README。

## 真实验收脚本

当前真实Twelve Data＋真实DeepSeek的QQQ A→D、黄金日线和QQQ日线+1h已在浏览器通过，详情见 [第八阶段验收](acceptance-phase8.md)。第七阶段Mock行情验收及HTTP401诊断保留为历史记录。需要重测时按以下分级验证，避免反复消耗行情额度：

```powershell
# 一次最小真实DeepSeek请求，最多生成1 token，无工具和行情调用，无重试：
& ./.venv/Scripts/python.exe scripts/verify_live.py --deepseek-probe
# 仅在上一步PASSED后执行：真实模型+显式Mock行情、A→D连续追问
& ./.venv/Scripts/python.exe scripts/verify_live.py --market-source mock --tasks A D
# 再验证真实模型+真实行情，只获取Agent工具需要的数据，不跑九项矩阵：
& ./.venv/Scripts/python.exe scripts/verify_live.py --skip-market-checks --tasks A D
```

最小请求只验证基础聊天鉴权，不代表工具调用或多轮理解已通过。`--tasks`明确结果覆盖范围；D必须在A之后，没有选择A→D的运行不计为多轮PASSED。`--market-source mock`自动省略真实行情矩阵，即使已配置行情密钥也不会请求Twelve Data。`--skip-market-checks`只省略矩阵，真实行情工具仍消耗按需额度。

有意未执行的矩阵在本次输出中为`NOT_RUN`，不覆盖已有真实验收记录。HTTP状态码单独输出，错误仅使用固定分类，不回显上游正文。401/402/403/429等错误会停止后续模型任务；不要用反复重试代替处理密钥、账户或限流问题。

无需先启动 Web 服务；脚本直接调用同一 MarketService 和 LangGraph 执行图：

```powershell
& ./.venv/Scripts/python.exe scripts/verify_live.py
```

它分别报告：

- `twelve_data`：QQQ、XAU/USD、AAPL 的 1h / 4h / 1day，共九项真实查询；校验来源、品种周期、合法 OHLC、带时区且有序的时间戳、最后数据时间、MA200 历史与 RSI/ATR。黄金成交量必须不可用。
- `deepseek_function_calling`：观察真实模型返回的原生 `tool_calls`，核对实际执行记录，并确认工具结果进入下一轮模型消息。
- `multi_turn_natural_language`：核对任务 A 的 QQQ 日线、同一会话 D 的 QQQ 4h、任务 B 的 QQQ 日线+1h、任务 C 的黄金日线，以及任务 E 的 QQQ RSI。品种或周期错误、工具失败、证据数值不一致、未复用会话或报告校验失败均不得计为通过。

缺少密钥时对应项为 `SKIPPED`，不发起对应真实请求。失败返回退出码 1，全部通过或缺密钥跳过时返回 0；因此一定要读取各项状态，不能把退出码 0 当成真实验收通过。脚本不输出密钥、供应商原始错误文本或完整模型提示词。

仅有 DeepSeek Key 时，可显式用模拟行情验证真实模型协议和品种/周期理解：

```powershell
& ./.venv/Scripts/python.exe scripts/verify_live.py --market-source mock
```

输出 `market_source_for_agent=mock`，并保留模拟数据说明。该模式仍是真实模型调用，会消耗模型额度，但不证明真实市场数据端到端通过；它与完全离线 `AGENT_MODE=mock` 不同。脚本从不因为真实请求失败而静默切换到模拟行情。

真实矩阵包含九个行情查询和五个模型任务，可能受套餐额度、每日限额或账户余额影响。缓存减少重复请求，但不会绕过供应商限制。

## 错误定位

| 分类 | 含义与下一步 |
| --- | --- |
| `missing_*_key` | 未配置对应项目密钥，填写后再执行。 |
| `upstream_authentication` / `authentication` | 密钥无效、过期或属于错误服务，核对本地配置。 |
| `upstream_permission` / `permission` | 套餐、账户或品种周期权限不足；不得合成假数据绕过。 |
| `insufficient_balance` | DeepSeek 账户余额不足。 |
| `rate_limit` | 十二数据额度或模型速率受限；等待额度恢复，避免重复执行完整矩阵。 |
| `upstream_timeout` / `timeout` / `upstream_network` / `network` | 出站网络、代理、DNS 或供应商超时；先检查网络。 |
| `model_or_endpoint_unavailable` | 模型名、API Base URL 或当前账户可用模型不匹配。 |
| `model_parameters_or_tool_compatibility` | 模型/接口不接受本工具参数或模式，核对 Function Calling 支持与请求配置。 |
| `invalid_report`、`wrong_symbol`、`wrong_timeframes` 等校验失败 | 已收到响应但不满足验收；不能算通过，请保存脱敏结果定位。 |
| `validation_or_code_error` | 本地数据校验或代码异常；不代表密钥错误。 |

上游 400/401/403/404/429 分类依据 [Twelve Data 文档](https://twelvedata.com/docs)；DeepSeek 分类依据 [官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/) 与 [工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/)。脚本保留固定错误分类，不把供应商原始文本回显给用户。

## 验收范围

校验 K 线时间和指标合法性不等于精确交易所日历、实时开闭市或半日市时段已经验证。当前系统保留可能未完成的最新 K 线并明确警告，结构是暂定判断。数据来源和时间应始终随报告一起查看。

离线自动化测试通过不能代替上述真实请求；真实请求失败也不否定已通过的离线测试。只有确实未执行的真实项才标记`SKIPPED`，实际发起请求后失败必须记录`FAILED`及原因。当前真实联合四场景已通过，不适用历史“未配置密钥/401”的结论；供应商真实错误本轮未触发，受控故障结果单独记录。

## 报告与主要周期校验

`primary_timeframe`是模型明确声明且有本轮已引用证据的主要周期；`timeframes`仍列出实际证据周期，主要周期在前。任务D允许日线背景，同时严格要求4h为主要周期，不允许无关周期或错误标的。品种、来源、价格、指标、快照与支撑阻力继续逐项对应实际工具记录。

`conclusion_basis`声明方向依据。均线排列、价格突破/短均线位置与斜率、多周期结构分歧分别核对工具事实；单周期长短信号不一致不能冒充多个周期。`需要更多证据`允许表达研究不确定性，status=success仅表示任务完成且报告证据校验通过，不表示已形成确定的交易方向；它与历史不足的insufficient_data不同。

`report.validation`仅记录模型输出的JSON结构、可信证据ID、未知ID数量、固定校验错误、数值引用检查和finish_reason，不记录模型原文、系统提示词或请求头。`status_reasons`区分缺少/无效结论、JSON/Schema错误、引用错误、历史不足、工具失败等；兼容原有success/partial/failed/insufficient_data状态。

默认`AGENT_REPORT_RETRIES=1`，设置范围0到2；修复调用仍计入最大模型轮数与总超时。修复只使用已有工具证据与当前任务，不重新拉行情或调用工具，使用[官方JSON Output](https://api-docs.deepseek.com/guides/json_mode/)模式。无有效报告仍返回partial；无证据为failed，历史不足为insufficient_data。模板继续展示可追溯数据，不将模板回退当成模型报告通过。

