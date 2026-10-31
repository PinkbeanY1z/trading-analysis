# 真实API诊断（2026-10-31）

## 当前结论与证据来源

用户本轮提供`scripts/verify_live.py`的真实运行结果：QQQ、XAU/USD的1h/4h/1day及AAPL的1h/1day，共8项PASSED，350根K线且source=twelve_data；AAPL/4h为FAILED(rate_limit)。本轮没有重复九项行情矩阵。该结果是部分成功，不是SKIPPED，也不是九项全通过。

本轮现场确认并执行了一次最小真实DeepSeek聊天请求（max_tokens=1、thinking禁用、无工具、无行情、无重试），收到**HTTP 401 / authentication**。未打印原始错误、请求头、密钥内容、片段、长度或哈希。没有继续执行已知鉴权失败的Agent请求。

用户提供的Agent任务A结果为agent_status=failed、model_errors=[authentication]，model_rounds与tool_execution均为空。结合本轮401，DeepSeek Function Calling与真实多轮自然语言理解保持FAILED（鉴权阻断）。这尚不能判断工具Schema或模型工具选择能力是否兼容。

## 已排除的配置问题

- 工作区：`E:\ChatGPTFiles\2026-10-29\files-pasted-by-the-user-python\outputs\trading-analysis`，分支`upgrade/python-agent`，诊断开始HEAD为`5ba96d20e1368b04c4ff4722cb9866426c803990`。
- 解释器：该工作区`.venv\Scripts\python.exe`，Python3.12.14。
- 行情和Agent均从该工作区`backend/.env`读取配置，路径根据源码位置解析，不依赖调用时所在目录。
- `TWELVEDATA_API_KEY`与`DEEPSEEK_API_KEY`：文件中已配置，有效配置已加载；本次诊断进程没有同名环境变量覆盖。
- Base URL=`https://api.deepseek.com`、Model=`deepseek-flash`；没有进程Base URL/Model/Timeout覆盖。
- 使用OpenAI兼容SDK显式传入DeepSeek Key，通过Bearer鉴权调用`/chat/completions`。基础地址、模型名与请求形式符合[DeepSeek官方说明](https://api-docs.deepseek.com/)。没有使用其他账号或切换远程地址。

这些检查只代表本次Python子进程。其他早已运行的后端可能保留旧配置；修改文件后需重启。无法读取用户另一个终端已设置但未被当前进程继承的变量。

## 需要用户手动处理

1. 打开[DeepSeek开放平台API Keys](https://platform.deepseek.com/api_keys)，核对当前账户中的Key；必要时创建新Key。
2. 在本地编辑器中替换`backend/.env`的`DEEPSEEK_API_KEY`，只填写原始Key，不加入`Bearer `前缀。不要发送到聊天、写入命令行参数或提交Git。
3. 重新启动后端；新开的验证脚本会重新加载文件。如果从其他终端运行，检查该终端有无旧同名环境变量覆盖文件。
4. 先运行`verify_live.py --deepseek-probe`。仅PASSED后再运行真实模型+Mock行情的A→D，然后才进行按需真实联合验证，完整命令见[live-api.md](live-api.md)。

401无法进一步证明Key是填错、撤销、失效还是来自其他服务。不能根据401要求用户充值：余额不足在官方错误码中是402。没有证据表明本次失败由LangGraph、指标或行情模块造成。

| 本次HTTP或错误类别 | 判断及操作 |
| --- | --- |
| 401 / authentication | 本轮实际结果；检查或替换DeepSeek平台Key。 |
| 402 / insufficient_balance | 只有收到此状态时再核对API账户余额。 |
| 403 / permission | 核对账户/端点访问权限，不能当成401。 |
| 400 / model_parameters_or_tool_compatibility | 检查请求参数与模型兼容性。 |
| 429 / rate_limit | 停止重复请求，等待限额恢复。 |
| 无HTTP状态 / network或timeout | 检查网络、代理、DNS/TLS与超时。 |

分类参考[DeepSeek官方错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。

## 离线与真实结果分别记录

原有106项离线Python测试仍通过；本轮新增12项验证器测试，总118项。它们覆盖固定HTTP分类、单次低成本请求、无密钥泄露、显式Mock行情不请求Twelve Data、按需联合测试和A→D范围校验。离线测试的Mock Transport不代表本轮真实模型通过。

当前没有修改本地Key、没有静默回退Mock。真实联合分析仍被401阻断，AAPL/4h仍保留rate_limit失败；旧浏览器验收记录中的SKIPPED只是当时状态。
