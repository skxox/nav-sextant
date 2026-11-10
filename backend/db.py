"""SQLite 数据层：建表与基础读写。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import date
from pathlib import Path

from .config import AppConfig

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fund_nav (
    fund_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    nav REAL,
    accumulated_nav REAL,
    PRIMARY KEY (fund_code, trade_date)
);

CREATE TABLE IF NOT EXISTS fund_holdings (
    fund_code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    weight REAL,
    PRIMARY KEY (fund_code, report_date, stock_code)
);

CREATE TABLE IF NOT EXISTS index_daily (
    index_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close REAL,
    pe REAL,
    pb REAL,
    open REAL,
    high REAL,
    low REAL,
    volume REAL,
    pe_percentile REAL,
    pb_percentile REAL,
    PRIMARY KEY (index_code, trade_date)
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    published TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    source TEXT,
    UNIQUE(title, published)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    action TEXT NOT NULL,
    target_position REAL,
    composite_score REAL,
    score_technical REAL,
    score_valuation REAL,
    score_sentiment REAL,
    score_risk REAL,
    reasons_json TEXT,
    risk_checks_json TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(trade_date, fund_code)
);

CREATE TABLE IF NOT EXISTS fund_position (
    fund_code TEXT PRIMARY KEY,
    position REAL DEFAULT 0,
    shares REAL DEFAULT 0,
    cost REAL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 信号执行记录（决策日志）：人对每条可执行信号的处置（执行/跳过）与成交要素。
-- signal_date 为 NULL = 非信号驱动的人工操作；UNIQUE 中 NULL 互不冲突，可多条。
CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_code TEXT NOT NULL,
    signal_date TEXT,
    exec_date TEXT NOT NULL,
    action TEXT NOT NULL,             -- BUY / SELL / SKIP（决定不执行）
    amount REAL,                      -- 金额（元）
    nav REAL,                         -- 成交净值（人工确认时填；缺省按 exec_date 回查 fund_nav）
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(fund_code, signal_date)    -- 每条信号最多一条处置记录（可改写）；人工操作不受限
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    agent TEXT NOT NULL,
    score REAL,
    direction TEXT,
    confidence REAL,
    summary TEXT,
    fallback_used INTEGER DEFAULT 0,
    detail_json TEXT,
    data_quality REAL,
    UNIQUE(trade_date, fund_code, agent)
);

-- 运行时配置覆盖（页面编辑写这里，删除即回落内置默认）：key = llm/settings/scheduler/notifications/debate/backtest/funds
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- 基金列表（页面维护；空表时使用 YAML 的 funds）
CREATE TABLE IF NOT EXISTS fund_config (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    benchmark_index TEXT NOT NULL DEFAULT '000300',
    valuation_index TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);
"""


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        # 个人单机使用：不使用回滚 journal / 磁盘临时文件（部分环境限制文件删除操作）
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """轻量迁移：为旧库补列（journal_mode=OFF 环境下不做全量 migration 框架）。"""
        pos_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fund_position)").fetchall()}
        for col, ddl in (("shares", "REAL DEFAULT 0"), ("cost", "REAL DEFAULT 0")):
            if col not in pos_cols:
                self.conn.execute(f"ALTER TABLE fund_position ADD COLUMN {col} {ddl}")
        fund_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fund_config)").fetchall()}
        for col in ("sector", "valuation_index"):
            if col not in fund_cols:
                self.conn.execute(f"ALTER TABLE fund_config ADD COLUMN {col} TEXT DEFAULT ''")
        idx_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(index_daily)").fetchall()}
        for col in ("open", "high", "low", "volume", "pe_percentile", "pb_percentile"):
            if col not in idx_cols:
                self.conn.execute(f"ALTER TABLE index_daily ADD COLUMN {col} REAL")
        log_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(agent_logs)").fetchall()}
        if "data_quality" not in log_cols:
            self.conn.execute("ALTER TABLE agent_logs ADD COLUMN data_quality REAL")
        self.conn.commit()

    # ---- 写入 ----
    def executemany_safe(self, sql: str, rows: list[tuple]):
        with self._lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def upsert_nav(self, rows: list[tuple[str, str, float | None, float | None]]):
        self.executemany_safe(
            "INSERT OR REPLACE INTO fund_nav (fund_code, trade_date, nav, accumulated_nav) VALUES (?,?,?,?)",
            rows,
        )

    def upsert_holdings(self, rows: list[tuple[str, str, str, str | None, float]]):
        self.executemany_safe(
            "INSERT OR REPLACE INTO fund_holdings (fund_code, report_date, stock_code, stock_name, weight) VALUES (?,?,?,?,?)",
            rows,
        )

    def upsert_index(self, rows: list[tuple[str, str, float | None, float | None, float | None,
                                             float | None, float | None]]):
        """估值入库（close 恒为 NULL，由 OHLCV 采集负责）。

        ON CONFLICT 只更新估值列且 COALESCE 保留已有值：
        - 不整行替换，避免踩掉 open/high/low/close/volume；
        - pe/pb 为 NULL 的行（如乐咕 outer join 缺口）不覆盖已有估值。
        """
        self.executemany_safe(
            """INSERT INTO index_daily (index_code, trade_date, close, pe, pb, pe_percentile, pb_percentile)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(index_code, trade_date) DO UPDATE SET
                 pe=COALESCE(excluded.pe, index_daily.pe),
                 pb=COALESCE(excluded.pb, index_daily.pb),
                 pe_percentile=COALESCE(excluded.pe_percentile, index_daily.pe_percentile),
                 pb_percentile=COALESCE(excluded.pb_percentile, index_daily.pb_percentile)""",
            rows,
        )

    def upsert_index_ohlcv(self, rows: list[tuple[str, str, float | None, float | None,
                                                   float | None, float | None, float | None, float | None]]):
        """指数日线 K 线数据（open/high/low/close/volume），保留已有 pe/pb 值。"""
        self.executemany_safe(
            """INSERT INTO index_daily (index_code, trade_date, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(index_code, trade_date) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume""",
            rows,
        )

    def insert_news(self, rows: list[tuple[str, str, str | None, str | None]]):
        self.executemany_safe(
            "INSERT OR IGNORE INTO news_items (published, title, content, source) VALUES (?,?,?,?)",
            rows,
        )

    def save_signal(self, record: dict):
        self.execute(
            """INSERT OR REPLACE INTO signals
               (trade_date, fund_code, action, target_position, composite_score,
                score_technical, score_valuation, score_sentiment, score_risk,
                reasons_json, risk_checks_json)
               VALUES (:trade_date,:fund_code,:action,:target_position,:composite_score,
                :score_technical,:score_valuation,:score_sentiment,:score_risk,
                :reasons_json,:risk_checks_json)""",
            record,
        )

    def save_agent_log(self, record: dict):
        self.execute(
            """INSERT OR REPLACE INTO agent_logs
               (trade_date, fund_code, agent, score, direction, confidence, summary,
                fallback_used, detail_json, data_quality)
               VALUES (:trade_date,:fund_code,:agent,:score,:direction,:confidence,:summary,
                       :fallback_used,:detail_json,:data_quality)""",
            record,
        )

    # ---- 查询 ----
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """线程安全的写操作（自动提交）。"""
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # ---- 运行时配置 ----
    def get_settings(self) -> dict[str, dict]:
        """读取全部配置覆盖，返回 {key: value_dict}。"""
        out: dict[str, dict] = {}
        for r in self.query("SELECT key, value_json FROM app_settings"):
            try:
                out[r["key"]] = json.loads(r["value_json"])
            except json.JSONDecodeError:
                logger.warning("app_settings 中 key=%s 的 JSON 损坏，已忽略", r["key"])
        return out

    def set_setting(self, key: str, value: dict) -> None:
        self.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json) VALUES (?,?)",
            (key, json.dumps(value, ensure_ascii=False)))

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM app_settings WHERE key=?", (key,))

    # ---- 基金配置 CRUD ----
    def get_fund_overrides(self) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT code, name, benchmark_index, COALESCE(valuation_index, '') AS valuation_index, "
            "COALESCE(sector, '') AS sector FROM fund_config ORDER BY code")]

    def save_fund(self, code: str, name: str, benchmark_index: str,
                  sector: str = "", valuation_index: str = "") -> None:
        self.execute(
            "INSERT OR REPLACE INTO fund_config (code, name, benchmark_index, valuation_index, sector) "
            "VALUES (?,?,?,?,?)",
            (code, name, benchmark_index, valuation_index, sector))

    def delete_fund(self, code: str) -> None:
        self.execute("DELETE FROM fund_config WHERE code=?", (code,))

    def get_nav_history(self, fund_code: str, limit: int = 500) -> list[sqlite3.Row]:
        return self.query(
            "SELECT trade_date, nav FROM fund_nav WHERE fund_code=? ORDER BY trade_date DESC LIMIT ?",
            (fund_code, limit),
        )

    def get_latest_signals(self) -> list[sqlite3.Row]:
        return self.query(
            """SELECT s.* FROM signals s
               JOIN (SELECT fund_code, MAX(trade_date) AS d FROM signals GROUP BY fund_code) t
               ON s.fund_code = t.fund_code AND s.trade_date = t.d
               ORDER BY s.composite_score DESC""",
        )

    def get_agent_logs(self, trade_date: str | None = None) -> list[sqlite3.Row]:
        if trade_date:
            return self.query("SELECT * FROM agent_logs WHERE trade_date=? ORDER BY fund_code, agent", (trade_date,))
        return self.query(
            """SELECT a.* FROM agent_logs a
               JOIN (SELECT MAX(trade_date) AS d FROM agent_logs) t ON a.trade_date = t.d
               ORDER BY a.fund_code, a.agent""",
        )


def get_db(config: AppConfig) -> Database:
    return Database(config.db_path)
