"""FastAPI 应用：面板数据接口 + 流水线/回测触发 + 内置调度 + 静态托管前端。"""
from __future__ import annotations

import base64
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import load_config, PROJECT_ROOT
from ..config_runtime import effective_config, ensure_settings_seeded, validate_section
from ..db import Database
from ..engine.backtest import run_backtest
from ..engine.pipeline import Pipeline
from ..engine.scheduler import DailyScheduler
from ..logging_setup import setup_logging

import logging

logger = logging.getLogger(__name__)

setup_logging()
_base_cfg = load_config()          # 仅读取引导配置（数据库路径）
db = Database(_base_cfg.db_path)
ensure_settings_seeded(db)         # 旧版 YAML 配置一次性迁入 DB（已有覆盖则跳过）
_scheduler = DailyScheduler(lambda: effective_config(db), db)


def _seed_funds_once() -> None:
    """旧版升级兼容：config.yaml 若还带 funds 列表，首次启动播种进 fund_config。

    新版 YAML 不再承载基金，基金列表完全由设置页管理（存 fund_config 表）。
    """
    if not db.get_fund_overrides():
        for f in _base_cfg.funds:
            db.save_fund(f.code, f.name, f.benchmark_index, f.sector, f.valuation_index)
        logger.info("已从 config.yaml 播种 %d 只基金到数据库", len(_base_cfg.funds))


_seed_funds_once()


def get_cfg():
    """每次请求动态合成生效配置（内置默认 + DB 覆盖），页面改配置立即生效。"""
    return effective_config(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _scheduler.start()
    yield
    _scheduler.shutdown()


app = FastAPI(title="nav-sextant", version="0.2.0", lifespan=lifespan)


# ---------- 元信息 ----------
@app.get("/api/meta")
def meta():
    cfg = get_cfg()
    return {
        "version": app.version,
        "llm": {
            "model": cfg.llm.model,
            "model_name": cfg.llm.model_name or cfg.llm.model,
            "configured": bool(cfg.llm.api_key_resolved),
            "debate_enabled": cfg.llm.debate_enabled,
            "debate_rounds": cfg.debate.rounds,
        },
        "scheduler": {"enabled": cfg.scheduler.enabled, "run_at": cfg.scheduler.run_at},
        "notifications_enabled": cfg.notifications.enabled,
    }


@app.get("/api/funds")
def list_funds():
    return [f.model_dump() for f in get_cfg().funds]


class FundCreate(BaseModel):
    code: str
    name: str
    benchmark_index: str = "000300"
    sector: str = ""
    valuation_index: str = ""


@app.post("/api/funds")
def add_fund(body: FundCreate):
    from pydantic import ValidationError
    try:
        fund = FundCreate.model_validate({
            "code": body.code.strip(), "name": body.name.strip(),
            "benchmark_index": body.benchmark_index.strip() or "000300",
            "sector": body.sector.strip(),
            "valuation_index": body.valuation_index.strip(),
        })
    except ValidationError:
        raise HTTPException(status_code=400, detail="格式错误：基金代码须为 6 位数字")
    if not re.fullmatch(r"\d{6}", body.code.strip()):
        raise HTTPException(status_code=400, detail="基金代码必须为 6 位数字")
    if body.valuation_index.strip() and not re.fullmatch(r"\d{6}", body.valuation_index.strip()):
        raise HTTPException(status_code=400, detail="估值指数代码必须为 6 位数字或留空")
    db.save_fund(body.code.strip(), body.name.strip(),
                 body.benchmark_index.strip() or "000300", body.sector.strip(),
                 body.valuation_index.strip())
    return {"ok": True, "funds": get_cfg().funds and [f.model_dump() for f in get_cfg().funds]}


@app.delete("/api/funds/{fund_code}")
def remove_fund(fund_code: str):
    db.delete_fund(fund_code)
    return {"ok": True}


# ---------- 信号 ----------
@app.get("/api/signals/latest")
def latest_signals():
    rows = db.get_latest_signals()
    return [dict(r) | {"reasons": json.loads(r["reasons_json"] or "[]"),
                       "risk_checks": json.loads(r["risk_checks_json"] or "[]")} for r in rows]


@app.get("/api/signals/history/{fund_code}")
def signal_history(fund_code: str, limit: int = 60):
    rows = db.query(
        "SELECT * FROM signals WHERE fund_code=? ORDER BY trade_date DESC LIMIT ?", (fund_code, limit))
    return [dict(r) for r in rows]


@app.get("/api/agent-logs")
def agent_logs(trade_date: str | None = None):
    return [dict(r) for r in db.get_agent_logs(trade_date)]


# ---------- 行情与持仓 ----------
@app.get("/api/nav/{fund_code}")
def nav_history(fund_code: str, limit: int = 250):
    rows = db.query(
        "SELECT trade_date, nav, accumulated_nav FROM fund_nav WHERE fund_code=? ORDER BY trade_date DESC LIMIT ?",
        (fund_code, limit))
    return [dict(r) for r in reversed(rows)]


@app.get("/api/holdings/{fund_code}")
def fund_holdings(fund_code: str):
    rows = db.query(
        "SELECT report_date, stock_code, stock_name, weight FROM fund_holdings "
        "WHERE fund_code=? ORDER BY report_date DESC, weight DESC LIMIT 10", (fund_code,))
    return [dict(r) for r in rows]


@app.get("/api/positions")
def get_positions():
    return [dict(r) for r in db.query("SELECT fund_code, position, shares, cost FROM fund_position")]


class PositionUpdate(BaseModel):
    fund_code: str
    position: float = 0.0
    shares: float = 0.0
    cost: float = 0.0


@app.post("/api/position")
def update_position(body: PositionUpdate):
    db.execute(
        "INSERT OR REPLACE INTO fund_position (fund_code, position, shares, cost) VALUES (?,?,?,?)",
        (body.fund_code, body.position, body.shares, body.cost))
    return {"ok": True}


@app.delete("/api/position/{fund_code}")
def delete_position(fund_code: str):
    db.execute("DELETE FROM fund_position WHERE fund_code=?", (fund_code,))
    return {"ok": True}


@app.get("/api/positions/ledger")
def positions_ledger():
    """持有列表：最新净值 / 当日涨幅 / 当日收益 / 持有收益 / 关联板块 / 市值，附汇总。

    当日数据口径：
    - 官方净值已更新到当日 → 用最新两日净值计算（当日涨幅、当日收益为官方口径）
    - 官方净值尚未更新 → 用盘中估算（基准指数 × 重仓穿透），并打 estimated 标记
    """
    cfg = get_cfg()
    fund_map = {f.code: f for f in cfg.funds}
    today = date.today().isoformat()
    out = []
    for r in db.query("SELECT fund_code, position, shares, cost FROM fund_position"):
        code = r["fund_code"]
        shares, cost = float(r["shares"] or 0), float(r["cost"] or 0)
        navs = db.query(
            "SELECT trade_date, nav FROM fund_nav WHERE fund_code=? ORDER BY trade_date DESC LIMIT 2",
            (code,))
        fund = fund_map.get(code)
        fund_name = fund.name if fund else code

        latest_nav = float(navs[0]["nav"]) if navs and navs[0]["nav"] else None
        latest_date = navs[0]["trade_date"] if navs else None
        prev_nav = float(navs[1]["nav"]) if len(navs) > 1 and navs[1]["nav"] else None

        # ---- 当日涨幅 / 当日收益 ----
        daily_change_pct: float | None = None
        daily_pnl: float | None = None
        nav_for_value = latest_nav
        daily_estimated = False
        if latest_nav is not None and prev_nav is not None:
            daily_change_pct = round((latest_nav - prev_nav) / prev_nav * 100, 2)
        if latest_date != today and latest_nav is not None:
            # 官方净值落后于当日 → 尝试盘中估算补当日涨幅
            try:
                from ..collectors.intraday import estimate_intraday
                est = estimate_intraday(db, code, fund_name,
                                        fund.benchmark_index if fund else "000300",
                                        latest_nav, latest_date)
                if est.estimated_change_pct is not None:
                    daily_change_pct = est.estimated_change_pct
                    nav_for_value = est.estimated_nav
                    daily_estimated = True
            except Exception as e:  # noqa: BLE001
                logger.warning("持有列表盘中估算失败 %s: %s", code, e)
        if nav_for_value is not None and shares > 0:
            # 官方净值未更新（盘中估算）→ 当日收益相对最新官方净值；
            # 官方净值已到当日 → 相对前一日净值
            baseline = latest_nav if daily_estimated else prev_nav
            if baseline is not None:
                daily_pnl = round(shares * (nav_for_value - baseline), 2)

        # ---- 市值 / 持有收益 ----
        market_value = round(shares * nav_for_value, 2) if nav_for_value else None
        cost_total = round(shares * cost, 2) if (shares > 0 and cost > 0) else None
        pnl = round(market_value - cost_total, 2) if (market_value is not None and cost_total) else None
        pnl_pct = round(pnl / cost_total * 100, 2) if (pnl is not None and cost_total) else None

        # ---- 关联板块：优先页面配置，其次按基金名称推断 ----
        sector = (fund.sector if fund else "") or _guess_sector(fund_name)

        out.append({
            "fund_code": code, "fund_name": fund_name, "sector": sector,
            "position": r["position"], "shares": shares, "cost": cost,
            "latest_nav": latest_nav,
            "latest_nav_date": latest_date,
            "prev_nav": prev_nav,
            "daily_change_pct": daily_change_pct, "daily_pnl": daily_pnl,
            "daily_estimated": daily_estimated,
            "market_value": market_value, "cost_total": cost_total,
            "pnl": pnl, "pnl_pct": pnl_pct,
        })
    # 汇总（有份额的行才计入）
    held = [x for x in out if x["market_value"] is not None]
    summary = {
        "market_value": round(sum(x["market_value"] for x in held), 2) if held else 0.0,
        "daily_pnl": round(sum(x["daily_pnl"] for x in held if x["daily_pnl"] is not None), 2) if held else 0.0,
        "pnl": round(sum(x["pnl"] for x in held if x["pnl"] is not None), 2) if held else 0.0,
        "cost_total": round(sum(x["cost_total"] for x in held if x["cost_total"] is not None), 2) if held else 0.0,
    }
    summary["pnl_pct"] = round(summary["pnl"] / summary["cost_total"] * 100, 2) if summary["cost_total"] else None
    return {"rows": out, "summary": summary}


_SECTOR_KEYWORDS = (
    ("白酒", "白酒"), ("消费", "消费"), ("医药", "医药"), ("医疗", "医疗"),
    ("科技", "科技"), ("信息", "科技"), ("互联网", "互联网"),
    ("新能源", "新能源"), ("半导体", "半导体"), ("芯片", "半导体"),
    ("军工", "军工"), ("银行", "银行"), ("证券", "券商"), ("金融", "金融"),
    ("地产", "地产"), ("有色", "有色金属"), ("煤炭", "煤炭"), ("黄金", "黄金"),
    ("食品", "消费"), ("农业", "农业"), ("新能源车", "新能源车"), ("光伏", "光伏"),
)


def _guess_sector(name: str) -> str:
    for kw, sector in _SECTOR_KEYWORDS:
        if kw in name:
            return sector
    return ""


# ---------- 图表数据：K线 / 业绩对比 / 我的收益 / 板块走势 ----------
def _index_name(code: str) -> str:
    from ..collectors.market_data import index_name
    return index_name(code)


@app.get("/api/index/kline/{index_code}")
def index_kline(index_code: str, days: int = 250):
    """指数 K 线（OHLCV）。DB 无 K 线数据时实时拉取并入库。"""
    rows = db.query(
        "SELECT trade_date, open, high, low, close, volume FROM index_daily "
        "WHERE index_code=? AND open IS NOT NULL AND close IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT ?", (index_code, days))
    if not rows:  # 旧数据只有 close → 实时补 OHLCV
        try:
            from ..collectors.market_data import fetch_index_ohlcv
            df = fetch_index_ohlcv(index_code, max(days, 400))
            if not df.empty:
                db.upsert_index_ohlcv([
                    (index_code, r.trade_date,
                     float(r.open) if r.open is not None else None,
                     float(r.high) if r.high is not None else None,
                     float(r.low) if r.low is not None else None,
                     float(r.close) if r.close is not None else None,
                     float(r.volume) if r.volume is not None else None)
                    for r in df.itertuples()
                ])
        except Exception as e:  # noqa: BLE001
            logger.warning("K线数据实时拉取失败 %s: %s", index_code, e)
        rows = db.query(
            "SELECT trade_date, open, high, low, close, volume FROM index_daily "
            "WHERE index_code=? AND open IS NOT NULL AND close IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT ?", (index_code, days))
    rows = list(reversed(rows))
    return {
        "code": index_code, "name": _index_name(index_code),
        "rows": [dict(r) for r in rows],
    }


@app.get("/api/index/history/{index_code}")
def index_history(index_code: str, days: int = 250):
    """指数收盘价历史（关联板块走势）。"""
    rows = db.query(
        "SELECT trade_date, close FROM index_daily WHERE index_code=? AND close IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT ?", (index_code, days))
    return {"code": index_code, "name": _index_name(index_code),
            "rows": [dict(r) for r in reversed(rows)]}


@app.get("/api/fund/performance/{fund_code}")
def fund_performance(fund_code: str, days: int = 250):
    """业绩走势：基金净值与基准指数按起点归一（rebase=100）对比。"""
    cfg = get_cfg()
    fund = next((f for f in cfg.funds if f.code == fund_code), None)
    bench = fund.benchmark_index if fund else "000300"
    navs = db.query(
        "SELECT trade_date, nav FROM fund_nav WHERE fund_code=? AND nav IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT ?", (fund_code, days))
    idx = db.query(
        "SELECT trade_date, close FROM index_daily WHERE index_code=? AND close IS NOT NULL "
        "ORDER BY trade_date DESC LIMIT ?", (bench, days))
    nav_map = {r["trade_date"]: float(r["nav"]) for r in navs}
    idx_map = {r["trade_date"]: float(r["close"]) for r in idx}
    common = sorted(set(nav_map) & set(idx_map))
    rows = []
    if common:
        nav0, idx0 = nav_map[common[0]], idx_map[common[0]]
        if nav0 and idx0:
            rows = [{"trade_date": d,
                     "fund": round(nav_map[d] / nav0 * 100, 2),
                     "benchmark": round(idx_map[d] / idx0 * 100, 2)} for d in common]
    return {
        "fund_code": fund_code, "fund_name": fund.name if fund else fund_code,
        "benchmark": {"code": bench, "name": _index_name(bench)},
        "rows": rows,
    }


@app.get("/api/portfolio/profit")
def portfolio_profit(days: int = 250):
    """我的收益曲线：Σ(份额×当日净值) - 总成本，按日累计（份额视为持仓期内不变）。"""
    positions = db.query("SELECT fund_code, shares, cost FROM fund_position WHERE shares > 0")
    if not positions:
        return {"rows": [], "cost_total": 0.0}
    cost_total = sum(float(p["shares"]) * float(p["cost"]) for p in positions if p["cost"])
    nav_series: dict[str, list[tuple[str, float]]] = {}
    for p in positions:
        navs = db.query(
            "SELECT trade_date, nav FROM fund_nav WHERE fund_code=? AND nav IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT ?", (p["fund_code"], days))
        nav_series[p["fund_code"]] = [(r["trade_date"], float(r["nav"])) for r in reversed(navs)]
    all_dates = sorted({d for series in nav_series.values() for d, _ in series})
    rows = []
    for d in all_dates:
        mv = 0.0
        for code, series in nav_series.items():
            nav_d = None
            for td, nv in series:  # 前向填充：用该日之前最近的净值
                if td <= d:
                    nav_d = nv
            if nav_d is not None:
                shares = next(float(p["shares"]) for p in positions if p["fund_code"] == code)
                mv += shares * nav_d
        profit = mv - cost_total if cost_total else None
        rows.append({
            "trade_date": d,
            "market_value": round(mv, 2),
            "profit": round(profit, 2) if profit is not None else None,
            "profit_rate": round(profit / cost_total * 100, 2) if (profit is not None and cost_total) else None,
        })
    return {"rows": rows, "cost_total": round(cost_total, 2)}


# ---------- 盘中估算 ----------
@app.get("/api/estimate/{fund_code}")
def intraday_estimate(fund_code: str):
    from ..collectors.intraday import estimate_intraday
    cfg = get_cfg()
    fund = next((f for f in cfg.funds if f.code == fund_code), None)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_code} 不在配置中")
    nav_row = db.query(
        "SELECT nav, trade_date FROM fund_nav WHERE fund_code=? ORDER BY trade_date DESC LIMIT 1", (fund_code,))
    latest_nav = float(nav_row[0]["nav"]) if nav_row and nav_row[0]["nav"] else None
    latest_date = nav_row[0]["trade_date"] if nav_row else None
    est = estimate_intraday(db, fund_code, fund.name, fund.benchmark_index, latest_nav, latest_date)
    return est.__dict__


# ---------- 工作台概览 ----------
_index_cache: dict = {"ts": 0.0, "data": []}


def _fetch_index_spot() -> list[dict]:
    """三大指数实时行情（东财为主，新浪兜底合并），5 分钟内存缓存。"""
    want = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指"}
    out: dict[str, dict] = {}
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_em()
        for _, r in df.iterrows():
            code = str(r.get("代码", ""))
            if code in want:
                out[code] = {"code": code, "name": want[code],
                             "close": float(r["最新价"]), "change_pct": float(r["涨跌幅"])}
    except Exception:  # noqa: BLE001
        logger.warning("东财指数行情获取失败，尝试新浪兜底")
    missing = set(want) - set(out)
    if missing:
        try:
            import akshare as ak
            df = ak.stock_zh_index_spot_sina()
            for _, r in df.iterrows():
                code = str(r.get("代码", "")).lstrip("shzb")
                if code in missing:
                    out[code] = {"code": code, "name": want[code],
                                 "close": float(r["最新价"]), "change_pct": float(r["涨跌幅"])}
        except Exception:  # noqa: BLE001
            logger.warning("新浪指数行情兜底也失败")
    return [out[c] for c in ("000001", "399001", "399006") if c in out]


@app.get("/api/overview")
def overview():
    now = time.time()
    if not _index_cache["data"] or now - _index_cache["ts"] > 300:
        _index_cache["ts"] = now
        _index_cache["data"] = _fetch_index_spot()
    today = date.today().isoformat()
    cfg = get_cfg()
    fund_codes = [f.code for f in cfg.funds]
    nav_row = db.query(
        "SELECT MAX(trade_date) AS d FROM fund_nav WHERE fund_code IN (%s)" % ",".join("?" * len(fund_codes)),
        tuple(fund_codes)) if fund_codes else []
    news_row = db.query("SELECT MAX(published) AS d FROM news_items")
    positions = db.query("SELECT COUNT(*) AS n FROM fund_position WHERE position > 0")
    today_signals = db.query(
        "SELECT COUNT(*) AS n FROM signals WHERE trade_date=? AND action != 'HOLD'", (today,))
    today_logs = db.query(
        "SELECT COUNT(*) AS n FROM agent_logs WHERE trade_date=?", (today,))
    return {
        "indices": _index_cache["data"],
        "stats": {
            "funds": len(cfg.funds),
            "holding_funds": positions[0]["n"] if positions else 0,
            "today_signals": today_signals[0]["n"] if today_signals else 0,
            "today_logs": today_logs[0]["n"] if today_logs else 0,
            "nav_latest": nav_row[0]["d"] if nav_row else None,
            "news_latest": news_row[0]["d"] if news_row else None,
            "notifications_enabled": cfg.notifications.enabled,
            "scheduler_enabled": cfg.scheduler.enabled,
        },
    }


# ---------- 数据导入 ----------
class ImportTextBody(BaseModel):
    text: str


def _parse_position_lines(text: str) -> tuple[list[dict], list[str]]:
    """解析每行持仓：`基金代码 仓位[ 份额 成本]`，分隔符支持空格/逗号/Tab；仓位支持 0.35 或 35%。"""
    imported, skipped = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"\b(\d{6})\b", line)
        if not m:
            skipped.append(line)
            continue
        code = m.group(1)
        rest = line[:m.start()] + line[m.end():]
        nums = re.findall(r"\d+\.?\d*%?", rest)
        if not nums:
            skipped.append(line)
            continue
        pos_raw = nums[0].rstrip("%")
        pos = float(pos_raw)
        if "%" in nums[0] or pos > 1:
            pos = pos / 100
        shares = float(nums[1]) if len(nums) > 1 and "%" not in nums[1] else 0.0
        cost = float(nums[2]) if len(nums) > 2 and "%" not in nums[2] else 0.0
        imported.append({"fund_code": code, "position": min(max(pos, 0.0), 1.0),
                         "shares": shares, "cost": cost})
    return imported, skipped


def _upsert_imported(rows: list[dict]) -> None:
    for r in rows:
        db.execute(
            "INSERT OR REPLACE INTO fund_position (fund_code, position, shares, cost) VALUES (?,?,?,?)",
            (r["fund_code"], r["position"], r["shares"], r["cost"]))


@app.post("/api/import/text")
def import_text(body: ImportTextBody):
    imported, skipped = _parse_position_lines(body.text)
    if not imported:
        raise HTTPException(status_code=400, detail="未能解析出任何持仓行；格式示例：`110022 0.35 1200 2.85` 或 `110022 35%`")
    _upsert_imported(imported)
    return {"imported": imported, "skipped": skipped}


_OCR_PROMPT = (
    "你是持仓截图解析器。从图片中提取场外基金持仓信息，只输出 JSON 数组，不要输出其他文字。"
    "每个元素形如 {\"fund_code\": \"6位基金代码\", \"position\": 仓位比例0-1, "
    "\"shares\": 持有份额(若无则为0), \"cost\": 成本单价(若无则为0)}。"
    "若截图中基金代码或仓位不完整，跳过该基金。"
)


@app.post("/api/import/image")
async def import_image(file: UploadFile = File(...)):
    """上传持仓截图，由视觉多模态模型（llm.vision_model）OCR 提取持仓并写入。"""
    cfg = get_cfg()
    if not cfg.llm.vision_model:
        raise HTTPException(
            status_code=400,
            detail="未配置视觉模型：请在「系统设置 → LLM」填入视觉多模态模型 ID，或改用文本导入")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片超过 8MB 限制")
    mime = file.content_type or "image/png"
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件（png/jpg/webp）")
    b64 = base64.b64encode(raw).decode()
    from ..llm.client import LLMClient  # 复用超时/容错配置
    client = LLMClient(cfg.llm)
    try:
        content = client.chat_vision(_OCR_PROMPT, mime, b64)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"视觉模型调用失败：{e}")
    try:
        match = re.search(r"\[.*\]", content, re.S)
        rows = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail=f"视觉模型输出无法解析为 JSON：{content[:200]}")
    valid, skipped = [], []
    for r in rows:
        if isinstance(r, dict) and re.fullmatch(r"\d{6}", str(r.get("fund_code", ""))):
            valid.append({
                "fund_code": str(r["fund_code"]),
                "position": min(max(float(r.get("position") or 0), 0.0), 1.0),
                "shares": float(r.get("shares") or 0),
                "cost": float(r.get("cost") or 0),
            })
        else:
            skipped.append(json.dumps(r, ensure_ascii=False))
    if not valid:
        raise HTTPException(status_code=422, detail="截图中未识别出有效持仓（需包含 6 位基金代码与仓位）")
    _upsert_imported(valid)
    return {"imported": valid, "skipped": skipped}


# ---------- 触发 ----------
@app.post("/api/pipeline/run")
def run_pipeline(force: bool = False):
    try:
        results = Pipeline(get_cfg(), db).run(force=force)
        return {"trade_date": results[0].trade_date if results else None,
                "results": [r.model_dump() for r in results]}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backtest/{fund_code}")
def backtest(fund_code: str):
    cfg = get_cfg()
    fund = next((f for f in cfg.funds if f.code == fund_code), None)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_code} 不在配置中")
    try:
        return run_backtest(db, fund, cfg).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/factors/eval/{fund_code}")
def factors_eval(fund_code: str, forward_days: int = 5):
    """因子有效性检验：RankIC / t 检验 / ICIR / 五分位多空价差（生产同款因子函数）。"""
    from ..engine.factor_eval import evaluate_all_factors
    import pandas as pd
    cfg = get_cfg()
    fund = next((f for f in cfg.funds if f.code == fund_code), None)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_code} 不在配置中")
    nav_rows = db.get_nav_history(fund.code, limit=1000)
    nav_history = pd.DataFrame([{"trade_date": r["trade_date"], "nav": r["nav"]} for r in nav_rows])
    index_val = pd.DataFrame([
        {"trade_date": r["trade_date"], "pe": r["pe"], "pb": r["pb"], "close": r["close"]}
        for r in db.query(
            "SELECT trade_date, pe, pb, close FROM index_daily WHERE index_code=? ORDER BY trade_date",
            (fund.effective_valuation_index(),))
    ])
    try:
        reports = evaluate_all_factors(nav_history, index_val,
                                       tuple(cfg.settings.momentum_windows),
                                       cfg.settings.momentum_windows_weights,
                                       forward_days=forward_days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"因子检验失败: {e}")
    return {"fund_code": fund.code, "fund_name": fund.name,
            "valuation_index": fund.effective_valuation_index(),
            "forward_days": forward_days, "reports": [r.to_dict() for r in reports]}


# ---------- 配置管理（页面编辑，存 DB；YAML 只留引导项） ----------
_SETTING_SECTIONS = ("llm", "settings", "scheduler", "notifications", "debate", "backtest")


@app.get("/api/settings")
def get_settings():
    """返回当前生效配置（内置默认 + DB 覆盖合成结果），api_key 打码。"""
    cfg = get_cfg()
    overrides = db.get_settings()
    data = {
        "llm": cfg.llm.model_dump() | {"api_key": ("****" + cfg.llm.api_key_resolved[-4:]) if cfg.llm.api_key_resolved else ""},
        "settings": cfg.settings.model_dump(),
        "scheduler": cfg.scheduler.model_dump(),
        "notifications": cfg.notifications.model_dump(),
        "debate": cfg.debate.model_dump(),
        "backtest": cfg.backtest.model_dump(),
        "funds": [f.model_dump() for f in cfg.funds],
        "overridden": [k for k in overrides],
    }
    return data


@app.put("/api/settings/{section}")
def update_settings(section: str, body: dict):
    """更新一段配置：与当前生效配置合并后走完整 pydantic 校验，非法即 400，合法则写 DB 立即生效。"""
    patch = body.get(section if section in _SETTING_SECTIONS else "payload", body)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="请求体需为 JSON 对象")
    # api_key 留空 = 保持现有密钥不变
    if section == "llm" and patch.get("api_key", None) == "":
        patch = {k: v for k, v in patch.items() if k != "api_key"}
    try:
        cfg = validate_section(db, section, patch)
    except Exception as e:  # noqa: BLE001 pydantic.ValidationError 等
        raise HTTPException(status_code=400, detail=f"配置校验失败：{e}")
    db.set_setting(section, patch)
    return {"ok": True, "effective": getattr(cfg, section).model_dump() if section != "funds" else None}


@app.delete("/api/settings/{section}")
def reset_settings(section: str):
    """清空某段的 DB 覆盖，回落到代码内置默认。"""
    if section not in _SETTING_SECTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的配置段: {section}")
    db.delete_setting(section)
    return {"ok": True}


# ---------- 基金穿透 ----------
_INDEX_NAMES = {
    "000001": "上证指数", "000300": "沪深300", "000905": "中证500", "000015": "红利指数",
    "399001": "深证成指", "399006": "创业板指", "399997": "中证白酒",
}


@app.get("/api/penetrate/{fund_code}")
def fund_penetrate(fund_code: str):
    """基金穿透：前十大重仓股 + 映射指数估值分位 + 盘中估算，一条接口出全链路视图。"""
    cfg = get_cfg()
    fund = next((f for f in cfg.funds if f.code == fund_code), None)
    if fund is None:
        raise HTTPException(status_code=404, detail=f"基金 {fund_code} 不在配置中")

    holdings = [dict(r) for r in db.query(
        "SELECT report_date, stock_code, stock_name, weight FROM fund_holdings "
        "WHERE fund_code=? ORDER BY report_date DESC, weight DESC LIMIT 10", (fund_code,))]

    val_index = fund.effective_valuation_index()
    val_rows = db.query(
        "SELECT trade_date, pe, pe_percentile, close FROM index_daily WHERE index_code=? "
        "ORDER BY trade_date DESC LIMIT 800", (val_index,))
    pe_value = None
    pe_percentile = None
    pe_proxy = False
    percentile_source = "none"
    pe_series = [r for r in val_rows if r["pe"] is not None]
    if len(pe_series) >= 60:
        pe_value = float(pe_series[0]["pe"])
        below = sum(1 for r in pe_series if float(r["pe"]) <= pe_value)
        pe_percentile = round(below / len(pe_series) * 100, 1)
        percentile_source = "history"
    elif any(r["pe_percentile"] is not None for r in val_rows):
        # 外部权威分位快照（蛋卷，近 10 年窗口）
        ext = next(r for r in val_rows if r["pe_percentile"] is not None)
        pe_value = float(ext["pe"]) if ext["pe"] is not None else None
        pe_percentile = round(float(ext["pe_percentile"]), 1)
        percentile_source = "external"
    elif len(val_rows) >= 60 and val_rows[0]["close"] is not None:
        # 行业指数 PE 历史不足 → 指数价格分位代理
        pe_proxy = True
        percentile_source = "proxy"
        closes = [float(r["close"]) for r in val_rows if r["close"] is not None]
        pe_value = closes[0]
        below = sum(1 for c in closes if c <= closes[0])
        pe_percentile = round(below / len(closes) * 100, 1)

    nav_row = db.query(
        "SELECT nav, trade_date FROM fund_nav WHERE fund_code=? ORDER BY trade_date DESC LIMIT 1", (fund_code,))
    latest_nav = float(nav_row[0]["nav"]) if nav_row and nav_row[0]["nav"] else None
    latest_date = nav_row[0]["trade_date"] if nav_row else None
    from ..collectors.intraday import estimate_intraday
    est = estimate_intraday(db, fund_code, fund.name, fund.benchmark_index, latest_nav, latest_date)

    return {
        "fund": fund.model_dump(),
        "index": {"code": val_index, "name": _INDEX_NAMES.get(val_index, val_index),
                  "pe": pe_value, "pe_percentile": pe_percentile,
                  "price_proxy": pe_proxy, "percentile_source": percentile_source},
        "holdings": holdings,
        "estimate": est.__dict__,
    }


# ---------- 前端静态托管 ----------
FRONTEND_DIR = PROJECT_ROOT / "frontend"
_dist_assets = FRONTEND_DIR / "dist" / "assets"
if _dist_assets.exists():
    app.mount("/assets", StaticFiles(directory=_dist_assets), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    """SPA 路由回退：/history、/backtest 等返回 index.html。"""
    dist_index = FRONTEND_DIR / "dist" / "index.html"
    if dist_index.exists():
        return FileResponse(dist_index)
    return FileResponse(FRONTEND_DIR / "index.html")
