# 第六阶段浏览器验收记录（2026-10-30）

## 环境与边界

实际运行 FastAPI/Uvicorn 与 Vite，通过 Codex 内置浏览器操作页面。标准生产入口使用 Mock Provider + 固定脚本模型；复杂场景通过独立 `scripts/browser_fixture.py` 注入显式脚本。后者仍执行真实 StateGraph、ToolNode、Checkpointer、MarketService 和指标代码，不按用户文本选择工具。

没有配置 Twelve Data 或 DeepSeek Key。以下结果证明浏览器协议和数据展示行为，不证明真实模型自然语言理解。Mock 各周期使用历史合成序列，日期可能不同，页面明确显示时间和过期提示。

## 已实际覆盖

| 场景 | 浏览器观察结果 |
| --- | --- |
| QQQ：1day、4h、1h | K线、均线和副图加载；MA20/50/200、RSI14、成交量和四条价格线可见，标题与周期一致。 |
| XAU/USD：1day、4h、1h | 各周期切换正确；成交量显示不可用，空量能不绘成0。 |
| 旧均线开关 | MA7/25/60与MA20/50/200标签、数值切换成功。 |
| 多周期研究 | 测试桩显式选择multi；真实compare_timeframes工具产生日线/1h证据，页面显示主图和比较图。 |
| 数值对应 | 日线MA20/50/200=435.95/434.45/426.95，1h=436.35/434.85/427.35，与报告一致；RSI均100；数据来源和时间一并展示。 |
| 连续追问 | 同一thread_id `bd0c796b03dd4d30a57ebc01aa0119a6` 从multi切到followup；服务观察到第二次POST携带原ID；工具实际查询QQQ/4h。此ID为合成测试会话。 |
| 快速切换 | QQQ日线请求延迟0.7秒时切换黄金、4h、QQQ、1h，旧请求结束后仍保持QQQ/1h。 |
| 行情超时 | 注入504，显示“行情服务超时”，原4h图保留。 |
| 非法参数 | 注入422，显示“请求参数无效”，原图保留；真实参数Schema另有自动测试。 |
| 空行情 | Provider返回空列表后API为404，页面提示“没有可用行情”，原图保留。 |
| 历史不足 | 25根K线，MA50/200显示不可用；Agent状态为insufficient_data而非成功。 |
| Agent失败 | 模型桩抛错经过实际Agent错误路径；页面显示失败，保留原图。 |
| 连接中断 | 停止本轮启动的测试Vite进程后点击刷新及发送；真实fetch拒绝，页面显示网络连接失败，已有图表保留；恢复服务后可重新加载。 |
| 刷新会话 | 页面reload后thread_id显示消失，不恢复已丢失的前端会话。 |
| 控制台 | 新页面及正常交互、恢复服务后的error/warn记录为空；未发现未处理JS/Vue异常。断网过程的网络/HMR连接失败为预期故障。 |

## 移动端

375、390、430px均实际设置浏览器viewport，并检查截图与DOM尺寸；没有横向溢出。375px：scrollWidth=360、输入框宽336；390px：scrollWidth=375、输入框宽351.2；430px：scrollWidth=415、输入框宽391.2。差值来自垂直滚动条。输入框高度均100px，430px实际填写并提交Agent任务成功。

发现并修复：旧全局CSS覆盖移动布局、图表网格最小内容宽度撑出容器、顶部OHLC和均线信息遮挡；RSI预热/黄金缺量时逻辑索引同步令副图错位。现在按真实时间范围同步，缺量副图不驱动主图范围。

## 复现测试桩

仓库根目录终端一：

```powershell
& ./.venv/Scripts/python.exe -m uvicorn browser_fixture:app --app-dir scripts --host 127.0.0.1 --port 8139
```

终端二：

```powershell
cd frontend
$env:VITE_API_TARGET='http://127.0.0.1:8139'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174 --strictPort
```

打开 http://127.0.0.1:5174 。在另一终端显式设定下一次调用的脚本：

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8139/__test/scenario -ContentType application/json -Body '{"scenario":"multi"}'
# 页面发送比较任务，再切换followup并在页面追问
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8139/__test/scenario -ContentType application/json -Body '{"scenario":"followup"}'
Invoke-RestMethod http://127.0.0.1:8139/__test/observations
```

其他场景：normal、market_timeout、invalid_parameters、insufficient_history、empty_data、agent_failed、delayed。测试入口只接受loopback连接，生产 `app.main:app` 不注册控制路由，自动测试会验证这一点。不要将测试桩作为生产服务部署。

## 自动化与真实接口

106项Python测试通过；前端2项Node协议测试通过；Vite生产构建成功。新增测试覆盖验证器、错误分类、快照修订、测试桩会话及失败状态。CI会执行Python、锁文件前端构建和前端协议测试。

`scripts/verify_live.py` 实际输出：Twelve Data=SKIPPED、DeepSeek Function Calling=SKIPPED、多轮自然语言理解=SKIPPED，原因分别为缺少本项目对应密钥。安全配置和真实验证命令见 [live-api.md](live-api.md)。真实接口成功、实时市场时段及真实代词理解不能由本验收记录推断。
