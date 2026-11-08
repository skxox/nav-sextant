# nav-sextant · 基金六分仪

自用基金决策系统：**多维 Agent 并行分析 → Bull/Bear 辩论 → 确定性风控审核**，输出每日操作信号，附盘中实时估算与策略回测。

## 架构（v0.2.0）

```
AKShare 数据采集（净值/持仓/指数估值/新闻，带重试退避 + 交易日历）
        ↓ SQLite
因子引擎（T-1 净值估算 · 多周期动量 · 估值偏离历史分位）
        ↓
Agent 扇出（ThreadPoolExecutor 并行，LangGraph 扇出/汇聚模式，统一 JSON 契约）
  ├─ 技术面 Agent（动量因子驱动，纯量化）
  ├─ 估值面 Agent（PE/PB 历史分位驱动，纯量化）
  ├─ 消息面 Agent（LLM 解读新闻，超时/非法输出回退中性，fail-closed）
  └─ 风险面 Agent（波动率/回撤/急跌预警，纯量化）
        ↓
编排层（置信度加权合成 → 初步信号与仓位）
        ↓
Bull/Bear 辩论层（LLM 多空对辩 → 保守仲裁，可否决/调整信号）
        ↓
风控审核层（确定性硬约束，Agent/辩论均无权越过）
  单基仓位上限 · 组合最大回撤 · 现金缓冲
        ↓
告警触达（邮件 SMTP + 飞书/企业微信 Webhook，BUY/SELL 或拦截时推送）
        ↓
Web 工作台（Vite + Vue3 + TS + Pinia + Element Plus + ECharts + UnoCSS/SCSS）
  信号卡片 · 评分雷达 · 净值曲线 · 盘中估算 · 持仓对账 · 信号历史 · 策略回测
```

核心原则：**预测层只输出数值，决策层消费契约，LLM 可以否决信号但永远无法越过风控红线**。

## 快速开始

依赖用 [uv](https://docs.astral.sh/uv/) 管理（`pyproject.toml` + `uv.lock`），前端为标准 Vite 工程：

```bash
cd nav-sextant
uv sync                          # 后端依赖
cd frontend && npm install && cd ..   # 前端依赖

# 构建/开发前端
cd frontend && npm run build     # 产物 dist/ 由 FastAPI 自动托管
cd frontend && npm run dev       # 开发模式（/api 代理到 8317）

# 启动面板
uv run uvicorn backend.api.app:app --host 127.0.0.1 --port 8317
# 打开 http://127.0.0.1:8317
```

测试：`uv run pytest`（22 个用例，无网络依赖）；前端类型检查：`npx vue-tsc -b --noEmit`。

## 配置（config.yaml）

- `funds`：基金列表及持仓映射的基准指数
- `settings.weights` / `thresholds` / `risk`：评分权重、BUY/SELL 阈值、风控红线
- `llm`：OpenAI 兼容接口（base_url / model / model_name / api_key，环境变量 `LLM_API_KEY` 优先）
- `debate`：Bull/Bear 辩论开关与轮次
- `notifications`：邮件 SMTP、飞书/企业微信 Webhook 告警
- `scheduler`：内置 APScheduler 每日定时（也可用 crontab，二选一）

## 每日自动运行

方式一：config.yaml `scheduler.enabled: true`（服务常驻时随 FastAPI lifespan 启动）；
方式二：crontab：

```bash
0 18 * * 1-5  cd /path/to/nav-sextant && ~/.local/bin/uv run python scripts/daily_run.py >> .tmp/cron.log 2>&1
```

## 接口一览

| 接口 | 说明 |
|---|---|
| GET /api/meta | 版本 / LLM 模型名（model_name）/ 调度与告警状态 |
| GET /api/funds · /api/signals/latest · /api/signals/history/{code} | 基金、最新信号、历史 |
| GET /api/nav/{code} · /api/holdings/{code} | 净值曲线、基金持仓 |
| GET /api/estimate/{code} | 盘中实时估算（持仓映射指数实时行情） |
| GET /api/agent-logs | 最近一天 Agent 分析日志（含辩论记录） |
| GET/POST /api/positions · /api/positions/ledger | 持仓维护、份额/成本对账 |
| POST /api/backtest/{code} | 阈值策略回测（年化/回撤/夏普/胜率，含申赎成本） |
| POST /api/pipeline/run | 手动触发完整分析流水线 |

## 已知限制

- T-1 净值估算基于前十大重仓股 + 指数涨跌近似，精度有限，仅作辅助输入
- 指数估值分位三级降级：自有 PE 历史（≥60 行，乐咕/中证官网日积）→ 蛋卷近 10 年外部分位快照 → 指数价格分位代理；行业指数初期依赖外部分位
- 个人单机使用，SQLite `journal_mode=OFF` 不支持崩溃回滚
