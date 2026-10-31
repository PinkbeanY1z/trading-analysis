# 第八阶段真实闭环验收通过

使用现有项目backend/.env，Twelve Data与DeepSeek均为真实服务。FastAPI本机8150、Vite5180，正常应用入口构造并注入真实模型的只读观测包装；没有脚本模型、Mock行情或生产控制路由。临时进程缓存900秒，显示原始数据及获取时间；重启修复时仅复用未过期、已实际获取的Twelve Data缓存。日志在被忽略的work/stage8目录，未提交。

## 独立修复：结论依据

首次真实QQQ请求因unsupported_conclusion返回partial，页面保留原图并显示部分完成。旧诊断没有保留枚举结论，不能断言当时的具体结论；代码只按均线排列支持方向，无法表达真实价格与长期均线结构不一致的状态。

现在Interpretation增加conclusion_basis，按所选依据分别验证：ma_alignment对应主周期的均线排列；price_structure对应真实收盘价突破工具支撑/阻力，或价格位于MA20/MA50同侧且这两条斜率同向；multi_timeframe要求至少两个实际周期及结构差异。数据都来自本轮引用工具，仍拒绝无证据方向和伪造读数。

实际重测中A初稿把单日线的长短结构矛盾称作周期分歧，被严格拒绝，修复后明确需要更多证据；D以价格结构支持偏空，即使其均线排列为mixed。这不是强制success，也不把完成研究等同确定买卖方向。两轮各一次受限修复，最终报告验证通过。

## 当前真实结果

| 场景 | 结果 | 来源 | 主要周期 | 报告修复 |
| --- | --- | --- | --- | --- |
| QQQ日线A | PASSED，success | twelve_data | 1day | 1次 |
| 同会话4h追问D | PASSED，success | twelve_data | 4h | 1次 |
| 黄金日线C | PASSED，success | twelve_data | 1day | 0次 |
| QQQ日线+1h B | PASSED，success | twelve_data | 1day，1h为比较图 | 1次 |

A/D的thread_id均为a6a979d3493846acab21645f32d77202；D请求携带原ID，首次模型请求中user_message_count=2。每轮两个原生工具调用、两个真实成功执行；结果进入下一次模型请求。通过现有check_task及额外报告/行情快照逐项对照，checks_failed/model_errors均为空。

黄金C使用3个真实工具调用，成交量及相对成交量为不可用，未替换为0；报告和图表均明确提示。B使用compare_timeframes及两次calculate_technical_indicators，日线均线多头而1h空头，报告以multi_timeframe为依据。四个场景共10个实际调用，无模型HTTP错误；均通过品种、主周期、来源、证据ID、数值和快照校验。

| 数据 | QQQ日线 | QQQ 4h |
| --- | --- | --- |
| 根数 | 350 | 350 |
| last_updated | 2026-10-30T04:00:00+00:00 | 2026-10-30T17:30:00+00:00 |
| close | 690.49 | 690.56 |
| MA20 | 728.1874998999999 | 713.6181819999999 |
| MA50 | 725.00079926 | 730.70009626 |
| MA200 | 679.51930018 | 719.2088906600001 |
| RSI | 34.88359951875802 | 31.767691737626535 |

每个周期与自身图表比较，不要求不同原生周期最后收盘价相等；时间口径及来源显式展示。数据包含上游最新K线，仍标记可能未完成。本轮未运行九项行情矩阵。

黄金日线last_updated=2026-10-31T00:00:00+00:00，close=3875.39395，MA20/50/200=4071.4126945/4199.53344636/4461.370810503，RSI=28.20609929104468。QQQ 1h的last_updated=2026-10-30T19:30:00+00:00，close=690.56，MA20/50/200=708.74249885/710.81169846/732.468690275，RSI=29.56313029930581。图表保留两位显示，原始数值与报告一致。

## 实际原生调用轨迹

下表是四个最终验收请求的真实DeepSeek tool_calls，不是脚本计划。每个ID均有成功Python工具执行，结果进入后续真实模型消息；repair阶段不调用工具。四场景model_errors/checks_failed均为空。A/D共用同一thread_id，C/B各自新会话。

| 请求 | call_id | 工具与周期 | 状态 |
| --- | --- | --- | --- |
| A | call_00_ZyUPthxbRpN41dKNKNaN0587 | calculate_technical_indicators QQQ/1day | success |
| A | call_01_NaqeXPLZtyMHHzpWmoSe7227 | analyze_market_structure QQQ/1day | success |
| D | call_00_PGA5NXaDZSd55ZUxtRnz5230 | calculate_technical_indicators QQQ/4h | success |
| D | call_01_iu31Nd4567pqgubtMdHP0744 | analyze_market_structure QQQ/4h | success |
| C | call_00_8ea85UhG4obmyQbhvOdI3767 | calculate_technical_indicators XAU/USD/1day | success |
| C | call_01_qsE5TMj1Qjew7h8ZPJ6o5408 | analyze_market_structure XAU/USD/1day | success |
| C | call_02_RUKJagx89t0KR9uX8qM11999 | get_market_data XAU/USD/1day | success |
| B | call_00_EqCc4RRGwQgvK63pIPUO1783 | compare_timeframes QQQ/[1day,1h] | success |
| B | call_01_koNemnoICgQAZ9dv9l2h1038 | calculate_technical_indicators QQQ/1day | success |
| B | call_02_EjZZaoiFp4TIworOR3aC2044 | calculate_technical_indicators QQQ/1h | success |

数值、market_structure、支撑阻力、last_updated和snapshot_id逐项比对工具执行结果与实际MarketResponse。四个快照为：

| 数据 | snapshot_id |
| --- | --- |
| QQQ/1day | 3d9f838d1e85bc6da8627c74618225baf5594c43cf67dd25e377aa97773bbc69 |
| QQQ/4h | bf7034c4b1480e45485f2a886fe56b39c757c58ed8c3a446b2305aba40a538a2 |
| XAU/USD/1day | c59406363a1c8928a4ee519b34b61c81e9d2d79b1541ed0dae0778547a8198b1 |
| QQQ/1h | 5ad8dbf1b479d2310cca1ef252bccad744acb2392be2fe669be15c446d90a429 |

主流程实际观察到4个唯一行情组合；验收及故障页真实初始加载共5次Provider查询（日线TTL到期后重新获取一次）。没有故意耗尽配额，没有静默Mock回退。本地详细响应、计数和核对结果在work/stage8/r1、r2，日志未提交。

## 工程验证

方向修复新增4项通过真实StateGraph的回归测试，总148项Python通过。图表修复新增4项，总7项前端测试及Vite生产构建通过。原144项保持通过。

## 独立修复：图表时间及同根读数

实际4h页面原先把17:30 UTC显示为次日01时，未标注上海时区且丢失分钟；悬停后OHLC更新但均线图例仍为最新值。现在统一显示UTC分钟，区分光标/最新K线，所有读数仅索引后端数组，不另算指标；缺失MA保持不可用。

真实浏览器点击QQQ 1h图表后显示2026-10-27 14:30 UTC，收720.11，MA20/50/200=709.27/714.90/734.23；移出后恢复2026-10-30 19:30 UTC，收690.56，MA20/50/200=708.74/710.81/732.47。主图与比较图均明确标注真实行情及原始source=twelve_data。主流程控制台error/warn为空。

## 异常提示与范围

单独本机故障页先从真实8150后端载入QQQ日线，再注入错误；主流程5180页面和真实后端不注入故障。未把确定性桩计入真实模型行为。

| 验证 | 结果 | 边界 |
| --- | --- | --- |
| 受控HTTP504 | PASSED | 页面明确显示本地注入超时，保留真实图表。 |
| 受控HTTP429 | PASSED | 页面明确显示本地注入额度受限，保留真实图表，不自动刷请求。 |
| 受控模型异常 | PASSED | 真实StateGraph走模型抛错路径，返回failed，页面未显示成功报告，图表保留、输入恢复可用；此项未调用DeepSeek。 |
| 故障解除 | PASSED | 刷新重新正常显示，错误提示清除。 |
| 供应商真实超时/限流/模型HTTP错误 | SKIPPED | 本轮未触发，不故意刷接口；上述注入不证明供应商实际故障行为。 |

故障页、代理及其Vite实例已停止；真实8150/5180演示仍保留。本轮没有重做第六阶段，也没有将Mock行情或脚本模型计作真实验收。

## Git交付及MVP判断

- aca10113acbf8c5480dbe785b7336909e1a5ccd8：结论依据修复，Push成功，[CI成功](https://github.com/PinkbeanY1z/trading-analysis/actions/runs/41192098053)。
- 3b498e7dd8ca3d8647f92989e4f338d4ea2ead8f：UTC/光标读数修复，Push成功，[CI成功](https://github.com/PinkbeanY1z/trading-analysis/actions/runs/41192619959)。
- 文档收尾提交及其CI以本轮最终汇报为准；main未修改，无强推，.env/API Key/临时验收代码及日志未提交。

已达到所测四条真实路径的本地面试演示MVP：真实数据、原生工具调用、会话追问、严格数值溯源、受限修复、图表联动和失败展示均有实际证据。A的“需要更多证据”来自长短信号冲突，不是伪造方向；success表示可信研究流程完成。

生产部署仍需要用户鉴权与会话隔离、持久化和会话回收、HTTPS与安全部署、配额调度/并发与分布式缓存、可观测性及恢复、供应商故障演练、精确交易日历/报价延迟和复权口径核验，以及更广任务/长期稳定性评估。自由文字校验不等于完整语义事实证明，四条成功路径不保证所有自然语言任务都成功。
