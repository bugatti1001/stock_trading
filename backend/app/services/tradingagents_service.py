"""
TradingAgents 框架集成服务。
封装 TradingAgents 多智能体系统，为股票池中的每只股票运行分析并生成交易建议。

优化 v2:
  A. 复用 DB 财务数据 — 通过 inject_data_cache 将已有财报/现金流/资产负债表注入 TA
  B. 复用 DB 新闻分析 — 将已有的 AI 新闻摘要/情感注入 TA 新闻分析师
  C. 并行处理 + API 限流 — ThreadPoolExecutor 并发分析多只股票，内置 token 限流等待
"""
import logging
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date as date_type, datetime, timedelta
from typing import Dict, List, Optional

from app.config.database import db_session

logger = logging.getLogger(__name__)

# TradingAgents is now bundled at backend/tradingagents/
# Ensure it's importable (backend/ is the working dir, so 'import tradingagents' works directly)
TRADINGAGENTS_PATH = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # backend/
if TRADINGAGENTS_PATH not in sys.path:
    sys.path.insert(0, TRADINGAGENTS_PATH)
TRADER_NAME = 'tradingagents'
TA_LAST_RUN_SETTING_KEY = 'ta_last_run_date'
TA_LAST_RECOMMENDATIONS_SETTING_KEY = 'ta_last_recommendations'
MAX_POSITIONS = 5
TA_CASH_RESERVE_PCT = 0.05
TA_MAX_POSITION_WEIGHT = 0.25
TA_MIN_NEW_POSITION_WEIGHT = 0.08
TA_MIN_REBALANCE_WEIGHT = 0.02

TA_RATING_STRENGTH = {
    'BUY': 3,
    'OVERWEIGHT': 2,
    'HOLD': 0,
    'UNDERWEIGHT': -2,
    'SELL': -3,
}

# ── Concurrency & Rate-Limit Settings ─────────────────────────────────────
# How many stocks to analyze in parallel.  Keep low to avoid API rate limits.
MAX_WORKERS = 8
# Minimum seconds between launching successive TA analyses (token cool-down)
LAUNCH_INTERVAL_SEC = 5
# If an LLM call fails with rate-limit / timeout, retry after this many seconds
RETRY_DELAY_SEC = 30
MAX_RETRIES = 2


# ══════════════════════════════════════════════════════════════════════════════
#  A+B: Build pre-injected data cache from DB
# ══════════════════════════════════════════════════════════════════════════════

def _build_data_cache_for_symbol(symbol: str) -> dict:
    """Build a cache dict for one stock from the DB.

    Returns keys like:
        "get_fundamentals:AAPL"  -> formatted fundamentals string
        "get_balance_sheet:AAPL" -> formatted balance sheet string
        "get_cashflow:AAPL"      -> formatted cash flow string
        "get_income_statement:AAPL" -> formatted income statement string
        "get_news:AAPL"          -> formatted news string
    """
    cache: dict = {}
    sym_upper = symbol.upper()

    try:
        cache.update(_build_financials_cache(sym_upper))
    except Exception as e:
        logger.warning(f"[DataCache] Failed to build financials cache for {sym_upper}: {e}")

    try:
        cache.update(_build_news_cache(sym_upper))
    except Exception as e:
        logger.warning(f"[DataCache] Failed to build news cache for {sym_upper}: {e}")

    try:
        cache.update(_build_indicators_cache(sym_upper))
    except Exception as e:
        logger.warning(f"[DataCache] Failed to build indicators cache for {sym_upper}: {e}")

    return cache


def _build_financials_cache(symbol: str) -> dict:
    """Format DB FinancialData into the same string format TA expects."""
    from app.models.financial_data import FinancialData
    from app.models.stock import Stock

    stock = db_session.query(Stock).filter_by(symbol=symbol, is_active=True).first()
    if not stock:
        return {}

    # Get up to 12 quarters of financial data (3 years)
    records = (
        db_session.query(FinancialData)
        .filter_by(stock_id=stock.id)
        .order_by(FinancialData.fiscal_year.desc(), FinancialData.report_date.desc())
        .limit(12)
        .all()
    )
    if not records:
        logger.info(f"[DataCache] {symbol}: 本地无财务数据，将 fallback 到网络获取")
        return {}

    # Count how many distinct fiscal years we have
    distinct_years = len(set(r.fiscal_year for r in records))
    if distinct_years < 3:
        logger.info(f"[DataCache] {symbol}: 本地仅 {distinct_years} 年财务数据 (需要3年)，"
                     f"不缓存财务数据，允许 TA 从网络补充")
        return {}

    cache = {}

    # ── get_fundamentals ──
    latest = records[0]
    fundamentals_lines = [
        f"# Company Fundamentals for {symbol}",
        f"# Data from DB (fiscal {latest.fiscal_year} {latest.period.value if latest.period else ''})",
        "",
    ]
    info_map = {
        "Name": stock.name,
        "Sector": getattr(stock, 'sector', None),
        "Market Cap": (stock.current_price * latest.shares_outstanding)
                      if stock.current_price and latest.shares_outstanding else None,
        "Revenue (TTM)": latest.revenue,
        "Net Income": latest.net_income,
        "Operating Income": latest.operating_income,
        "R&D Expense": latest.rd_expense,
        "Cash & Equivalents": latest.cash_and_equivalents,
        "Total Assets": latest.total_assets,
        "Total Equity": latest.total_equity,
        "Shares Outstanding": latest.shares_outstanding,
        "Operating Cash Flow": latest.operating_cash_flow,
        "Capital Expenditure": latest.capital_expenditure,
        "Dividends per Share": latest.dividends_per_share,
    }
    # Compute derived ratios
    if latest.revenue and latest.revenue > 0:
        if latest.net_income is not None:
            info_map["Profit Margin"] = f"{latest.net_income / latest.revenue:.2%}"
        if latest.operating_income is not None:
            info_map["Operating Margin"] = f"{latest.operating_income / latest.revenue:.2%}"
    if latest.total_equity and latest.total_equity > 0 and latest.net_income:
        info_map["Return on Equity"] = f"{latest.net_income / latest.total_equity:.2%}"
    if latest.total_assets and latest.total_assets > 0 and latest.net_income:
        info_map["Return on Assets"] = f"{latest.net_income / latest.total_assets:.2%}"
    if latest.total_equity and latest.total_equity > 0:
        debt = (latest.short_term_borrowings or 0) + (latest.long_term_borrowings or 0)
        info_map["Debt to Equity"] = f"{debt / latest.total_equity:.2f}"
    if stock.current_price and latest.shares_outstanding and latest.net_income:
        eps = latest.net_income / latest.shares_outstanding
        if eps > 0:
            info_map["PE Ratio (TTM)"] = f"{stock.current_price / eps:.2f}"
            info_map["EPS (TTM)"] = f"{eps:.2f}"
    if latest.operating_cash_flow and latest.capital_expenditure:
        info_map["Free Cash Flow"] = latest.operating_cash_flow - abs(latest.capital_expenditure)

    for label, value in info_map.items():
        if value is not None:
            fundamentals_lines.append(f"{label}: {value}")

    cache[f"get_fundamentals:{symbol}"] = "\n".join(fundamentals_lines)

    # ── get_balance_sheet / get_cashflow / get_income_statement ──
    # Format as CSV-like tables similar to yfinance output
    def _fmt_financial_table(records, fields, title):
        header = f"# {title} for {symbol}\n# Periods: {len(records)} quarters\n\n"
        # CSV header row = field names as columns, dates as rows
        col_headers = ["Field"] + [
            f"{r.fiscal_year}-{r.period.value if r.period else 'N/A'}" for r in records
        ]
        lines = [",".join(col_headers)]
        for field_name, attr in fields:
            row = [field_name]
            for r in records:
                val = getattr(r, attr, None)
                row.append(str(val) if val is not None else "")
            lines.append(",".join(row))
        return header + "\n".join(lines)

    balance_fields = [
        ("Cash And Equivalents", "cash_and_equivalents"),
        ("Accounts Receivable", "accounts_receivable"),
        ("Inventory", "inventory"),
        ("Investments", "investments"),
        ("Accounts Payable", "accounts_payable"),
        ("Short Term Borrowings", "short_term_borrowings"),
        ("Long Term Borrowings", "long_term_borrowings"),
        ("Total Assets", "total_assets"),
        ("Total Equity", "total_equity"),
        ("Non Current Assets", "non_current_assets"),
        ("Current Liabilities", "current_liabilities"),
    ]
    cache[f"get_balance_sheet:{symbol}"] = _fmt_financial_table(
        records, balance_fields, "Balance Sheet"
    )

    cashflow_fields = [
        ("Operating Cash Flow", "operating_cash_flow"),
        ("Capital Expenditure", "capital_expenditure"),
    ]
    cache[f"get_cashflow:{symbol}"] = _fmt_financial_table(
        records, cashflow_fields, "Cash Flow Statement"
    )

    income_fields = [
        ("Revenue", "revenue"),
        ("Cost Of Revenue", "cost_of_revenue"),
        ("Operating Income", "operating_income"),
        ("Net Income", "net_income"),
        ("Net Income To Parent", "net_income_to_parent"),
        ("Selling Expense", "selling_expense"),
        ("Admin Expense", "admin_expense"),
        ("R&D Expense", "rd_expense"),
        ("Finance Cost", "finance_cost"),
    ]
    cache[f"get_income_statement:{symbol}"] = _fmt_financial_table(
        records, income_fields, "Income Statement"
    )

    return cache


def _build_news_cache(symbol: str) -> dict:
    """Format DB StockNewsAnalysis into the news string TA expects."""
    from app.models.stock_news_analysis import StockNewsAnalysis

    # Get the most recent analysis (within last 3 days)
    cutoff = datetime.utcnow() - timedelta(days=3)
    analysis = (
        db_session.query(StockNewsAnalysis)
        .filter(
            StockNewsAnalysis.symbol == symbol,
            StockNewsAnalysis.analyzed_at >= cutoff,
        )
        .order_by(StockNewsAnalysis.analyzed_at.desc())
        .first()
    )
    if not analysis:
        return {}

    cache = {}

    # Build news string from the stored analysis
    news_lines = [
        f"## {symbol} News Analysis (from DB, analyzed {analysis.analyzed_at.strftime('%Y-%m-%d')})",
        "",
        f"### Overall Sentiment: {analysis.sentiment}",
        "",
        f"### Summary",
        analysis.summary or "No summary available.",
        "",
    ]

    if analysis.key_events:
        news_lines.append("### Key Events")
        events = analysis.key_events if isinstance(analysis.key_events, list) else []
        for evt in events:
            if isinstance(evt, dict):
                news_lines.append(f"- **{evt.get('event', 'N/A')}**: {evt.get('impact', '')}")
            else:
                news_lines.append(f"- {evt}")
        news_lines.append("")

    if analysis.principle_impacts:
        news_lines.append("### Principle Impacts")
        impacts = analysis.principle_impacts if isinstance(analysis.principle_impacts, list) else []
        for imp in impacts:
            if isinstance(imp, dict):
                news_lines.append(f"- **{imp.get('principle', imp.get('area', 'N/A'))}**: {imp.get('impact', imp.get('description', ''))}")
            else:
                news_lines.append(f"- {imp}")
        news_lines.append("")

    if analysis.news_sources:
        news_lines.append("### Sources")
        sources = analysis.news_sources if isinstance(analysis.news_sources, list) else []
        for src in sources[:5]:
            if isinstance(src, dict):
                news_lines.append(f"- {src.get('title', 'N/A')} (source: {src.get('source', 'Unknown')})")
            else:
                news_lines.append(f"- {src}")

    news_text = "\n".join(news_lines)
    cache[f"get_news:{symbol}"] = news_text

    return cache


def _build_global_news_cache() -> dict:
    """Fetch global/macro news ONCE and return a cache dict.

    The cache key "get_global_news" matches the lookup in interface.py
    so all stocks share the same global news without re-fetching.
    """
    cache: dict = {}

    try:
        curr_date = datetime.now().strftime("%Y-%m-%d")

        from tradingagents.dataflows.yfinance_news import get_global_news_yfinance
        global_news_text = get_global_news_yfinance(curr_date, look_back_days=7, limit=10)

        if global_news_text and "Error" not in global_news_text:
            cache["get_global_news"] = global_news_text
            logger.info(f"[GlobalNews] 预获取全局新闻成功 ({len(global_news_text)} chars), 将共享给所有股票")
        else:
            logger.warning(f"[GlobalNews] 预获取全局新闻返回空或错误: {global_news_text[:100] if global_news_text else 'None'}")

    except Exception as e:
        logger.warning(f"[GlobalNews] 预获取全局新闻失败: {e}")

    return cache


def _build_indicators_cache(symbol: str) -> dict:
    """Pre-calculate all technical indicators locally using stockstats.

    Downloads stock data once (or uses existing CSV cache), then calculates
    all 13 indicators that TA's Market Analyst might request.
    Returns cache keys like "get_indicators:AAPL:rsi" -> formatted string.
    Also caches "get_stock_data:AAPL" for the Market Analyst's price queries.
    """
    import pandas as pd

    cache = {}
    today_str = date_type.today().strftime('%Y-%m-%d')

    # All indicators supported by TA
    INDICATORS = [
        'close_50_sma', 'close_200_sma', 'close_10_ema',
        'macd', 'macds', 'macdh',
        'rsi',
        'boll', 'boll_ub', 'boll_lb', 'atr',
        'vwma', 'mfi',
    ]

    # Indicator descriptions (same as in y_finance.py)
    IND_DESC = {
        "close_50_sma": "50 SMA: Medium-term trend indicator.",
        "close_200_sma": "200 SMA: Long-term trend benchmark.",
        "close_10_ema": "10 EMA: Responsive short-term average.",
        "macd": "MACD: Momentum via EMA differences.",
        "macds": "MACD Signal: EMA smoothing of MACD line.",
        "macdh": "MACD Histogram: Gap between MACD and signal.",
        "rsi": "RSI: Overbought/oversold momentum indicator.",
        "boll": "Bollinger Middle: 20 SMA basis for Bollinger Bands.",
        "boll_ub": "Bollinger Upper Band: 2 std dev above middle.",
        "boll_lb": "Bollinger Lower Band: 2 std dev below middle.",
        "atr": "ATR: Average true range volatility measure.",
        "vwma": "VWMA: Volume-weighted moving average.",
        "mfi": "MFI: Money Flow Index (price + volume momentum).",
    }

    # ── Step 1: Get stock data (use TA's existing CSV cache or download) ──
    config = _get_ta_config()
    cache_dir = config.get('data_cache_dir', '')
    if not cache_dir:
        writable_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'ta_cache'
        )
        cache_dir = os.path.join(writable_dir, 'data_cache')
    os.makedirs(cache_dir, exist_ok=True)

    today_dt = pd.Timestamp.today()
    start_dt = today_dt - pd.DateOffset(years=15)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = today_dt.strftime("%Y-%m-%d")
    csv_path = os.path.join(cache_dir, f"{symbol}-YFin-data-{start_str}-{end_str}.csv")

    if os.path.exists(csv_path):
        data = pd.read_csv(csv_path, on_bad_lines="skip")
        logger.info(f"[DataCache] {symbol} 股价数据从本地CSV读取 ({len(data)} 行)")
    else:
        try:
            import yfinance as yf
            data = yf.download(
                symbol, start=start_str, end=end_str,
                multi_level_index=False, progress=False, auto_adjust=True,
            )
            if data.empty:
                logger.warning(f"[DataCache] {symbol} yfinance 无数据")
                return {}
            data = data.reset_index()
            data.to_csv(csv_path, index=False)
            logger.info(f"[DataCache] {symbol} 股价数据已下载并缓存 ({len(data)} 行)")
        except Exception as e:
            logger.warning(f"[DataCache] {symbol} 股价数据下载失败: {e}")
            return {}

    # ── Step 2: Clean data for stockstats ──
    try:
        # Ensure proper column names
        col_map = {}
        for col in data.columns:
            cl = col.lower().strip()
            if cl == 'date':
                col_map[col] = 'Date'
            elif cl == 'open':
                col_map[col] = 'Open'
            elif cl == 'high':
                col_map[col] = 'High'
            elif cl == 'low':
                col_map[col] = 'Low'
            elif cl in ('close', 'adj close'):
                col_map[col] = 'Close'
            elif cl == 'volume':
                col_map[col] = 'Volume'
        data = data.rename(columns=col_map)

        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors='coerce')

        if 'Date' in data.columns:
            data['Date'] = pd.to_datetime(data['Date'])
        else:
            logger.warning(f"[DataCache] {symbol} 无Date列")
            return {}

        data = data.dropna(subset=['Close'])
    except Exception as e:
        logger.warning(f"[DataCache] {symbol} 数据清洗失败: {e}")
        return {}

    # ── Step 3: Cache get_stock_data (recent 60 days for Market Analyst) ──
    try:
        recent = data.tail(60).copy()
        recent_csv = recent[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy()
        recent_csv['Date'] = recent_csv['Date'].dt.strftime('%Y-%m-%d')
        for col in ['Open', 'High', 'Low', 'Close']:
            recent_csv[col] = recent_csv[col].round(2)
        header = f"# Stock data for {symbol} (recent 60 days, from local cache)\n"
        header += f"# Total records: {len(recent_csv)}\n\n"
        cache[f"get_stock_data:{symbol}"] = header + recent_csv.to_csv(index=False)
    except Exception as e:
        logger.warning(f"[DataCache] {symbol} get_stock_data 缓存失败: {e}")

    # ── Step 4: Calculate all indicators using stockstats ──
    try:
        from stockstats import wrap

        # stockstats needs lowercase columns and no 'Date' column (causes parse errors)
        dates = data['Date'].reset_index(drop=True)
        df_num = data[['Open', 'High', 'Low', 'Close', 'Volume']].copy().reset_index(drop=True)
        df_num.columns = ['open', 'high', 'low', 'close', 'volume']
        for c in df_num.columns:
            df_num[c] = pd.to_numeric(df_num[c], errors='coerce')
        df_num = df_num.ffill().bfill()

        ss = wrap(df_num)

        look_back = 30  # Default look-back window
        curr_date_dt = pd.to_datetime(today_str)
        before_dt = curr_date_dt - pd.Timedelta(days=look_back)

        for indicator in INDICATORS:
            try:
                # Trigger indicator calculation
                vals = ss[indicator]

                # Build date-value pairs for the look-back window
                ind_lines = []
                for idx in range(len(dates)):
                    d = dates.iloc[idx]
                    if d < before_dt or d > curr_date_dt:
                        continue
                    v = vals.iloc[idx] if idx < len(vals) else None
                    d_str = d.strftime('%Y-%m-%d')
                    val_str = "N/A" if v is None or pd.isna(v) else str(round(float(v), 4))
                    ind_lines.append(f"{d_str}: {val_str}")

                if not ind_lines:
                    continue

                result = (
                    f"## {indicator} values from {before_dt.strftime('%Y-%m-%d')} to {today_str}:\n\n"
                    + "\n".join(ind_lines)
                    + "\n\n"
                    + IND_DESC.get(indicator, "")
                )

                cache[f"get_indicators:{symbol}:{indicator}"] = result

            except Exception as e:
                logger.debug(f"[DataCache] {symbol} 指标 {indicator} 计算失败: {e}")

    except ImportError:
        logger.warning("[DataCache] stockstats 未安装，跳过技术指标预计算")
    except Exception as e:
        logger.warning(f"[DataCache] {symbol} 技术指标预计算失败: {e}")

    ind_count = sum(1 for k in cache if 'get_indicators' in k)
    logger.info(f"[DataCache] {symbol} 技术指标: {ind_count}/{len(INDICATORS)} 个, 股价数据: {'✓' if f'get_stock_data:{symbol}' in cache else '✗'}")

    return cache


# ══════════════════════════════════════════════════════════════════════════════
#  Config helper
# ══════════════════════════════════════════════════════════════════════════════

def _get_ta_config() -> dict:
    """
    Build TradingAgents config using the user's current AI provider/key.
    Uses lazy imports to avoid circular dependencies.
    """
    from tradingagents.default_config import DEFAULT_CONFIG
    from app.config.settings import (
        get_ai_provider, get_anthropic_key, get_openai_key, get_minimax_key,
        get_nvidia_key, AI_MODEL, OPENAI_DEFAULT_MODEL, MINIMAX_DEFAULT_MODEL,
        MINIMAX_BASE_URL, NVIDIA_DEFAULT_MODEL, NVIDIA_BASE_URL,
    )

    config = deepcopy(DEFAULT_CONFIG)

    # Redirect cache/results to writable directories (important for Docker read-only mounts)
    writable_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'ta_cache')
    os.makedirs(writable_dir, exist_ok=True)
    config['project_dir'] = writable_dir
    config['data_cache_dir'] = os.path.join(writable_dir, 'data_cache')
    config['results_dir'] = os.path.join(writable_dir, 'results')
    config['memory_log_path'] = os.path.join(writable_dir, 'memory', 'decisions.md')
    config['memory_log_max_entries'] = 200
    try:
        from app.config.database import get_current_db_path
        config['stock_analysis_db_path'] = get_current_db_path()
    except Exception:
        pass
    os.makedirs(config['data_cache_dir'], exist_ok=True)
    os.makedirs(config['results_dir'], exist_ok=True)

    provider = get_ai_provider()

    # Analyst LLM: use Haiku for data analysis (12x cheaper than Sonnet)
    # Debate/judge LLMs: keep Sonnet for quality reasoning
    ANALYST_MODEL = 'claude-haiku-4-5'  # 分析师用Haiku (省75%成本, 快3x)

    if provider == 'claude':
        api_key = get_anthropic_key()
        config['llm_provider'] = 'anthropic'
        config['deep_think_llm'] = AI_MODEL
        config['quick_think_llm'] = AI_MODEL
        config['analyst_llm'] = ANALYST_MODEL
        config['backend_url'] = None
        config['api_key'] = api_key
    elif provider == 'openai':
        api_key = get_openai_key()
        config['llm_provider'] = 'openai'
        config['deep_think_llm'] = OPENAI_DEFAULT_MODEL
        config['quick_think_llm'] = OPENAI_DEFAULT_MODEL
        config['backend_url'] = None
        config['api_key'] = api_key
    elif provider == 'minimax':
        api_key = get_minimax_key()
        config['llm_provider'] = 'openai'
        config['deep_think_llm'] = MINIMAX_DEFAULT_MODEL
        config['quick_think_llm'] = MINIMAX_DEFAULT_MODEL
        config['analyst_llm'] = MINIMAX_DEFAULT_MODEL  # no Haiku for minimax
        config['backend_url'] = MINIMAX_BASE_URL
        config['api_key'] = api_key
    elif provider == 'nvidia':
        api_key = get_nvidia_key()
        config['llm_provider'] = 'openai'
        config['deep_think_llm'] = NVIDIA_DEFAULT_MODEL
        config['quick_think_llm'] = NVIDIA_DEFAULT_MODEL
        config['analyst_llm'] = NVIDIA_DEFAULT_MODEL
        config['backend_url'] = NVIDIA_BASE_URL
        config['api_key'] = api_key
    else:
        # fallback to claude
        api_key = get_anthropic_key()
        config['llm_provider'] = 'anthropic'
        config['deep_think_llm'] = AI_MODEL
        config['quick_think_llm'] = AI_MODEL
        config['analyst_llm'] = ANALYST_MODEL
        config['backend_url'] = None
        config['api_key'] = api_key

    return config


# ══════════════════════════════════════════════════════════════════════════════
#  Per-stock runner (with data injection + retry)
# ══════════════════════════════════════════════════════════════════════════════

# Global throttle lock — ensures LAUNCH_INTERVAL_SEC between TA graph starts
_launch_lock = threading.Lock()
_last_launch_time = 0.0

# Lock for _ta_progress to prevent race conditions across threads
_progress_lock = threading.Lock()

# Progress file path (shared across Gunicorn workers via filesystem)
import json as _json
import tempfile
_PROGRESS_FILE = os.path.join(tempfile.gettempdir(), 'ta_progress.json')

TA_PROGRESS_STAGES = [
    ("Market Analyst", "市场分析"),
    ("Social Analyst", "情绪分析"),
    ("News Analyst", "新闻分析"),
    ("Fundamentals Analyst", "基本面分析"),
    ("Report Compressor", "报告压缩"),
    ("Bull Researcher", "多头研究"),
    ("Bear Researcher", "空头研究"),
    ("Research Manager", "研究经理"),
    ("Trader", "交易员"),
    ("Aggressive Analyst", "进取风险"),
    ("Conservative Analyst", "保守风险"),
    ("Neutral Analyst", "中性风险"),
    ("Risk Judge", "组合经理"),
]
_STAGE_INDEX = {name: idx + 1 for idx, (name, _label) in enumerate(TA_PROGRESS_STAGES)}
_STAGE_LABEL = {name: label for name, label in TA_PROGRESS_STAGES}


def _empty_progress() -> dict:
    return {
        'running': False,
        'phase': 'idle',
        'completed': 0,
        'total': 0,
        'current_symbol': '',
        'results': {},
        'active': {},
        'events': [],
        'stage_total': len(TA_PROGRESS_STAGES),
        'stage_labels': [{'node': node, 'label': label} for node, label in TA_PROGRESS_STAGES],
        'overall_pct': 0,
    }


def _read_progress_unlocked() -> dict:
    try:
        with open(_PROGRESS_FILE, 'r') as f:
            data = _json.load(f)
            return data if isinstance(data, dict) else _empty_progress()
    except (FileNotFoundError, _json.JSONDecodeError, Exception):
        return _empty_progress()


def _write_progress_unlocked(data: dict):
    with open(_PROGRESS_FILE, 'w') as f:
        _json.dump(data, f)


def _save_progress(data: dict):
    """Save progress to a temp file (readable by any Gunicorn worker)."""
    with _progress_lock:
        try:
            _write_progress_unlocked(data)
        except Exception:
            pass


def get_ta_progress() -> dict:
    """Return current TA analysis progress (from shared temp file)."""
    try:
        with open(_PROGRESS_FILE, 'r') as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError, Exception):
        return _empty_progress()


def _append_progress_event(progress: dict, symbol: str, text: str, level: str = 'info'):
    events = progress.setdefault('events', [])
    events.append({
        'ts': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
        'symbol': symbol,
        'text': text,
        'level': level,
    })
    progress['events'] = events[-60:]


def _recompute_progress(progress: dict):
    active = progress.get('active') or {}
    total = progress.get('total') or len(active)
    progress['completed'] = sum(
        1 for info in active.values()
        if info.get('status') in {'completed', 'error'}
    )

    if total <= 0:
        progress['overall_pct'] = 0
        progress['current_symbol'] = ''
        return

    stage_total = max(1, progress.get('stage_total') or len(TA_PROGRESS_STAGES))
    partial_units = 0.0
    for info in active.values():
        status = info.get('status')
        if status in {'completed', 'error'}:
            partial_units += 1.0
        elif status == 'running':
            stage_index = max(0, min(stage_total, int(info.get('stage_index') or 0)))
            stage_status = info.get('stage_status')
            if stage_status == 'completed':
                partial_units += stage_index / stage_total
            else:
                partial_units += max(0, stage_index - 1) / stage_total

    progress['overall_pct'] = min(100, round((partial_units / total) * 100))
    running_symbols = [
        sym for sym, info in active.items()
        if info.get('status') == 'running'
    ]
    progress['current_symbol'] = ', '.join(running_symbols[:3])
    progress['updated_at'] = datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _update_progress(mutator):
    with _progress_lock:
        progress = _read_progress_unlocked()
        mutator(progress)
        _recompute_progress(progress)
        try:
            _write_progress_unlocked(progress)
        except Exception:
            pass
        return progress


def _init_ta_progress(symbols: List[str]):
    now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
    progress = {
        'running': True,
        'phase': 'preparing',
        'completed': 0,
        'total': len(symbols),
        'current_symbol': '',
        'results': {},
        'active': {
            sym: {
                'symbol': sym,
                'status': 'queued',
                'stage': '排队',
                'stage_node': '',
                'stage_index': 0,
                'stage_total': len(TA_PROGRESS_STAGES),
                'stage_pct': 0,
                'stage_status': 'queued',
                'started_at': None,
                'updated_at': now,
                'elapsed_sec': None,
            }
            for sym in symbols
        },
        'events': [],
        'stage_total': len(TA_PROGRESS_STAGES),
        'stage_labels': [{'node': node, 'label': label} for node, label in TA_PROGRESS_STAGES],
        'overall_pct': 0,
        'started_at': now,
        'updated_at': now,
    }
    _append_progress_event(progress, '', f'准备分析 {len(symbols)} 只股票')
    _save_progress(progress)


def _set_ta_progress_phase(phase: str, text: str):
    def mutate(progress):
        progress['phase'] = phase
        _append_progress_event(progress, '', text)
    _update_progress(mutate)


def _mark_ta_stock_started(symbol: str):
    def mutate(progress):
        now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        progress['phase'] = 'analyzing'
        info = progress.setdefault('active', {}).setdefault(symbol, {})
        info.update({
            'symbol': symbol,
            'status': 'running',
            'stage': '等待模型',
            'stage_node': '',
            'stage_status': 'started',
            'updated_at': now,
        })
        if not info.get('started_at'):
            info['started_at'] = now
        _append_progress_event(progress, symbol, '开始分析')
    _update_progress(mutate)


def _handle_ta_node_progress(event: dict):
    symbol = str(event.get('symbol') or '').upper()
    node = str(event.get('node') or '')
    status = event.get('status') or 'started'
    if not symbol or not node:
        return

    def mutate(progress):
        now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        progress['phase'] = 'analyzing'
        info = progress.setdefault('active', {}).setdefault(symbol, {'symbol': symbol})
        old_index = int(info.get('stage_index') or 0)
        stage_index = _STAGE_INDEX.get(node, old_index)
        stage_index = max(old_index, stage_index)
        stage_total = len(TA_PROGRESS_STAGES)
        label = _STAGE_LABEL.get(node, node)
        info.update({
            'symbol': symbol,
            'status': 'running',
            'stage': label,
            'stage_node': node,
            'stage_index': stage_index,
            'stage_total': stage_total,
            'stage_pct': min(99, round(stage_index / stage_total * 100)),
            'stage_status': status,
            'updated_at': now,
        })
        if not info.get('started_at'):
            info['started_at'] = now
        if status == 'completed':
            info['elapsed_sec'] = round(float(event.get('elapsed') or 0), 1)
            _append_progress_event(progress, symbol, f'{label} 完成 ({info["elapsed_sec"]}s)')
        else:
            _append_progress_event(progress, symbol, f'{label} 中')
    _update_progress(mutate)


def _mark_ta_stock_completed(symbol: str, action: str, error: bool = False):
    def mutate(progress):
        now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        info = progress.setdefault('active', {}).setdefault(symbol, {'symbol': symbol})
        info.update({
            'status': 'error' if error else 'completed',
            'stage': '失败' if error else '完成',
            'stage_node': '',
            'stage_index': len(TA_PROGRESS_STAGES),
            'stage_total': len(TA_PROGRESS_STAGES),
            'stage_pct': 100,
            'stage_status': 'completed',
            'updated_at': now,
            'action': action,
        })
        progress.setdefault('results', {})[symbol] = action
        _append_progress_event(progress, symbol, ('失败' if error else f'完成: {action.upper()}'),
                               level='error' if error else 'info')
    _update_progress(mutate)


def _finish_ta_progress():
    def mutate(progress):
        progress['running'] = False
        progress['phase'] = 'done'
        progress['current_symbol'] = ''
        _append_progress_event(progress, '', '全部分析完成')
    _update_progress(mutate)


def run_ta_for_stock(
    symbol: str,
    data_cache: Optional[dict] = None,
    progress_callback=None,
) -> dict:
    """
    Run TradingAgents for a single stock.
    Returns {'action': 'buy'/'sell'/'hold', 'reason': '...'}

    Args:
        symbol: Stock ticker
        data_cache: Optional pre-built cache dict for this symbol.
                    If provided, will be injected via inject_data_cache()
                    so TA reads DB data instead of calling yfinance.
        progress_callback: Optional callback receiving per-node graph progress events.
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.dataflows.interface import inject_data_cache, clear_data_cache

    config = _get_ta_config()
    api_key = config.pop('api_key', None)

    if not api_key:
        return {'action': 'hold', 'rating': 'Hold', 'reason': '未配置 API Key，请在登录时输入'}

    today_str = date_type.today().strftime('%Y-%m-%d')

    logger.info(f"[TA] 开始分析 {symbol} (缓存条目: {len(data_cache) if data_cache else 0})")

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            # ── Throttle: enforce minimum interval between launches ──
            global _last_launch_time
            with _launch_lock:
                now = time.time()
                wait = LAUNCH_INTERVAL_SEC - (now - _last_launch_time)
                if wait > 0:
                    logger.info(f"[TA] {symbol} 等待 {wait:.1f}s (API限流)")
                    time.sleep(wait)
                _last_launch_time = time.time()

            # ── Inject pre-cached data into this thread ──
            if data_cache:
                inject_data_cache(data_cache)

            # ── Set API key in env ──
            env_key_name = None
            old_env_val = None
            if config.get('llm_provider') == 'anthropic' and api_key:
                env_key_name = 'ANTHROPIC_API_KEY'
            elif config.get('llm_provider') == 'openai' and api_key:
                env_key_name = 'OPENAI_API_KEY'

            if env_key_name and api_key:
                old_env_val = os.environ.get(env_key_name)
                os.environ[env_key_name] = api_key

            try:
                ta_graph = TradingAgentsGraph(
                    config=config,
                    progress_callback=progress_callback,
                )
                final_state, decision = ta_graph.propagate(symbol, today_str)
            finally:
                # Restore env
                if env_key_name is not None:
                    if old_env_val is None:
                        os.environ.pop(env_key_name, None)
                    else:
                        os.environ[env_key_name] = old_env_val
                # Always clear cache after use
                clear_data_cache()

            # Parse decision
            rating = decision.strip() if decision else 'Hold'
            rating_upper = rating.upper()
            if rating_upper in ('BUY', 'OVERWEIGHT'):
                action = 'BUY'
            elif rating_upper in ('SELL', 'UNDERWEIGHT'):
                action = 'SELL'
            else:
                action = 'HOLD'

            reason = (final_state or {}).get('final_trade_decision', '') or ''
            # reason 字段为 Text 类型，不限长度，保留完整的多Agent分析报告

            logger.info(f"[TA] {symbol} 决策: {rating} -> {action}")
            return {'action': action.lower(), 'rating': rating, 'reason': reason}

        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(kw in err_str for kw in [
                'rate_limit', 'rate limit', '429', 'too many requests',
                'overloaded', 'timeout', 'timed out',
            ])

            if is_rate_limit and attempt <= MAX_RETRIES:
                wait = RETRY_DELAY_SEC * attempt
                logger.warning(
                    f"[TA] {symbol} 第{attempt}次失败 (限流/超时), "
                    f"{wait}s 后重试: {str(e)[:100]}"
                )
                time.sleep(wait)
                continue
            else:
                logger.error(f"[TA] {symbol} 分析失败 (尝试 {attempt}): {e}", exc_info=True)
                return {
                    'action': 'hold',
                    'rating': 'Hold',
                    'reason': f'TradingAgents 分析失败: {str(e)[:200]}',
                }

    return {'action': 'hold', 'rating': 'Hold', 'reason': 'TradingAgents 分析失败: 超过最大重试次数'}


# ══════════════════════════════════════════════════════════════════════════════
#  Holdings & Cash helpers (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def _init_ta_holdings_from_user():
    """
    如果 TA 没有任何交易记录，用用户当前持仓初始化 TA 持仓。
    只执行一次（首次调用时）。
    同时保存 ta_starting_cash，使 TA 起始资产 == 用户真实资产。
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.user_setting import UserSetting
    from app.services.portfolio_service import compute_holdings, compute_user_cash
    from datetime import timedelta

    existing = db_session.query(AiTradeRecord).filter_by(trader=TRADER_NAME).first()
    if existing:
        return  # 已有记录，不需要初始化

    user_holdings = compute_holdings()
    if not user_holdings:
        return

    yesterday = date_type.today() - timedelta(days=1)
    ta_buy_total = 0.0
    for h in user_holdings:
        shares = h.get('net_shares', 0)
        price = h.get('avg_cost', 0) or h.get('current_price', 0)
        if shares <= 0 or price <= 0:
            continue
        record = AiTradeRecord(
            symbol=h['symbol'],
            action='buy',
            shares=shares,
            price=price,
            trade_date=yesterday,
            reason='初始化：与用户持仓同步',
            trader=TRADER_NAME,
        )
        db_session.add(record)
        ta_buy_total += shares * price

    # 保存 ta_starting_cash = user_cash + ta_buy_total
    user_cash = compute_user_cash()
    ta_starting_cash = round(user_cash + ta_buy_total, 2)
    row = db_session.query(UserSetting).filter_by(key='ta_starting_cash').first()
    if row:
        row.value = str(ta_starting_cash)
    else:
        db_session.add(UserSetting(key='ta_starting_cash', value=str(ta_starting_cash)))

    db_session.commit()
    logger.info(f"[TA] 从用户持仓初始化 {len(user_holdings)} 条记录, ta_starting_cash={ta_starting_cash:.2f}")


def compute_ta_holdings() -> Dict[str, Dict]:
    """
    Compute TradingAgents holdings from AiTradeRecord where trader='tradingagents'.
    首次调用时自动从用户持仓初始化。
    Returns { "AAPL": {"shares": 50, "avg_cost": 150.5}, ... }
    """
    from app.models.ai_trade_record import AiTradeRecord

    _init_ta_holdings_from_user()

    records = (
        db_session.query(AiTradeRecord)
        .filter_by(trader=TRADER_NAME)
        .order_by(AiTradeRecord.trade_date)
        .all()
    )
    holdings: Dict[str, Dict] = {}

    for r in records:
        h = holdings.setdefault(r.symbol, {'shares': 0, 'total_cost': 0.0})
        if r.action == 'buy':
            h['total_cost'] += r.shares * r.price
            h['shares'] += r.shares
        elif r.action == 'sell':
            if h['shares'] > 0:
                avg = h['total_cost'] / h['shares']
                sold = min(r.shares, h['shares'])
                h['total_cost'] -= avg * sold
                h['shares'] -= sold

    return {
        sym: {
            'shares': h['shares'],
            'avg_cost': round(h['total_cost'] / h['shares'], 4) if h['shares'] > 0 else 0,
        }
        for sym, h in holdings.items()
        if h['shares'] > 0
    }


def compute_ta_cash() -> float:
    """
    Compute TradingAgents cash balance.
    Uses 'ta_starting_cash' (fallback to 'total_capital') minus buys plus sells.
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.user_setting import UserSetting

    # Determine starting cash
    try:
        row = db_session.query(UserSetting).filter_by(key='ta_starting_cash').first()
        if row:
            starting_cash = float(row.value)
        else:
            row = db_session.query(UserSetting).filter_by(key='total_capital').first()
            starting_cash = float(row.value) if row else 0
    except Exception:
        starting_cash = 0

    if starting_cash <= 0:
        return 0.0

    records = (
        db_session.query(AiTradeRecord)
        .filter_by(trader=TRADER_NAME)
        .order_by(AiTradeRecord.trade_date)
        .all()
    )
    cash = starting_cash
    for r in records:
        if r.action == 'buy':
            cash -= r.shares * r.price
        elif r.action == 'sell':
            cash += r.shares * r.price
    return round(cash, 2)


def _normalize_ta_rating(decision: dict) -> str:
    rating = str(decision.get('rating') or '').strip().upper()
    if rating in TA_RATING_STRENGTH:
        return rating
    action = str(decision.get('action') or '').strip().upper()
    if action == 'BUY':
        return 'BUY'
    if action == 'SELL':
        return 'SELL'
    return 'HOLD'


def _extract_ta_price_target(reason: str) -> Optional[float]:
    if not reason:
        return None
    patterns = [
        r"\*\*Price Target\*\*\s*:\s*(?:[A-Z]{2,4}\s*)?(?:[$¥]|HK\$)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        r"Price Target\s*:\s*(?:[A-Z]{2,4}\s*)?(?:[$¥]|HK\$)?\s*([0-9][0-9,]*(?:\.\d+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, reason, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            return float(m.group(1).replace(',', ''))
        except (TypeError, ValueError):
            continue
    return None


def _ta_candidate_upside(decision: dict, price: float) -> Optional[float]:
    if not price or price <= 0:
        return None
    target = _extract_ta_price_target(decision.get('reason', ''))
    if target is None or target <= 0:
        return None
    return (target - price) / price


def _score_ta_buy_candidate(decision: dict, price: float) -> float:
    """Conviction score used only for ranking candidates competing for slots."""
    rating = _normalize_ta_rating(decision)
    base = 100 if rating == 'BUY' else 70 if rating == 'OVERWEIGHT' else 0
    upside = _ta_candidate_upside(decision, price)
    if upside is not None:
        base += max(-25, min(35, upside * 100))
    return round(base, 2)


def _target_weight_for_ta_candidate(decision: dict, price: float) -> float:
    """Target portfolio weight for a new or topped-up TA candidate."""
    rating = _normalize_ta_rating(decision)
    if rating == 'BUY':
        target = 0.20
    elif rating == 'OVERWEIGHT':
        target = 0.10
    else:
        return 0.0

    upside = _ta_candidate_upside(decision, price)
    if upside is not None:
        # Price targets adjust sizing within the approved weight bands.
        target += max(-0.02, min(0.05, upside * 0.10))

    return round(max(TA_MIN_NEW_POSITION_WEIGHT, min(TA_MAX_POSITION_WEIGHT, target)), 4)


def _price_for_portfolio_value(symbol: str, holding: dict, price_map: dict) -> float:
    price = price_map.get(symbol)
    if price and price > 0:
        return price
    avg_cost = holding.get('avg_cost', 0)
    return avg_cost if avg_cost and avg_cost > 0 else 0.0


def _portfolio_value(holdings: dict, cash: float, price_map: dict) -> float:
    value = cash
    for symbol, holding in holdings.items():
        shares = holding.get('shares', 0) or 0
        price = _price_for_portfolio_value(symbol, holding, price_map)
        if shares > 0 and price > 0:
            value += shares * price
    return round(value, 2)


def _prepend_execution_note(decision: dict, note: str):
    reason = decision.get('reason') or ''
    if reason.startswith('**Execution Note**:'):
        decision['reason'] = reason
    else:
        decision['reason'] = f"**Execution Note**: {note}\n\n{reason}".strip()


# ══════════════════════════════════════════════════════════════════════════════
#  Main entry: generate trades (with parallel execution)
# ══════════════════════════════════════════════════════════════════════════════

def generate_ta_trades(selected_symbols: Optional[List[str]] = None) -> dict:
    """
    Run TradingAgents for all in-pool stocks, determine position sizing,
    save executed records to AiTradeRecord with trader='tradingagents'.
    Returns { "AAPL": {"action": "buy", "shares": 10, "reason": "..."}, ... }

    Optimizations (v2):
      - Pre-builds data cache from DB for each symbol (financials + news)
      - Runs analyses in parallel with ThreadPoolExecutor
      - Throttles launches and retries on rate-limit errors
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.stock import Stock
    from app.models.ta_recommendation_record import TaRecommendationRecord
    from app.models.user_setting import UserSetting

    today = date_type.today()

    # 1. Check if already executed today (skip when user manually selected stocks)
    if not selected_symbols:
        today_records = (
            db_session.query(AiTradeRecord)
            .filter(
                AiTradeRecord.trader == TRADER_NAME,
                AiTradeRecord.trade_date == today,
                AiTradeRecord.action.in_(('buy', 'sell')),
                AiTradeRecord.shares > 0,
                AiTradeRecord.price > 0,
                ~AiTradeRecord.reason.like('%初始化%'),
                ~AiTradeRecord.reason.like('%重置%'),
            )
            .all()
        )
        if today_records:
            logger.info("[TA] 今日已执行过，返回已有记录")
            return {
                r.symbol: {'action': r.action, 'shares': r.shares, 'reason': r.reason or ''}
                for r in today_records
            }

        last_run = db_session.query(UserSetting).filter_by(key=TA_LAST_RUN_SETTING_KEY).first()
        if last_run and last_run.value == today.isoformat():
            logger.info("[TA] 今日已分析过，无实际交易记录")
            return {}
    else:
        # Delete old records for selected symbols so they can be re-analyzed
        for sym in selected_symbols:
            db_session.query(AiTradeRecord).filter(
                AiTradeRecord.trader == TRADER_NAME,
                AiTradeRecord.trade_date == today,
                AiTradeRecord.symbol == sym.upper(),
                ~AiTradeRecord.reason.like('%初始化%'),
                ~AiTradeRecord.reason.like('%重置%'),
            ).delete()
        db_session.commit()
        logger.info(f"[TA] 手动重跑: {selected_symbols}, 已删除旧记录")

    # 2. Get stock symbols (selected or all in-pool)
    if selected_symbols:
        stocks = db_session.query(Stock).filter(
            Stock.symbol.in_([s.upper() for s in selected_symbols]),
            Stock.in_pool == True,
        ).all()
    else:
        stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
    if not stocks:
        logger.warning("[TA] 无股票可分析")
        return {}

    all_symbols = [s.symbol for s in stocks]
    logger.info(f"[TA] 股票池 ({len(all_symbols)}): {all_symbols}")

    # Build price map from current stock prices
    price_map = {}
    for s in stocks:
        if s.current_price and s.current_price > 0:
            price_map[s.symbol] = s.current_price

    # 3. Get ta_holdings and ta_cash
    ta_holdings = compute_ta_holdings()
    ta_cash = compute_ta_cash()

    # Add prices for existing TA holdings that may not be part of the selected run.
    missing_price_symbols = [sym for sym in ta_holdings if sym not in price_map]
    if missing_price_symbols:
        for s in db_session.query(Stock).filter(Stock.symbol.in_(missing_price_symbols)).all():
            if s.current_price and s.current_price > 0:
                price_map[s.symbol] = s.current_price

    # F5: Smart filtering — only analyze stocks that matter
    # Skip filtering when user explicitly selects stocks
    if selected_symbols:
        symbols = all_symbols
        logger.info(f"[TA] 用户指定股票，跳过智能过滤，分析全部 {len(symbols)} 只")
    else:
        # Auto mode: filter to held stocks + high-priority candidates
        held_symbols = set(ta_holdings.keys())
        priority_symbols = set()
        for s in stocks:
            sym = s.symbol
            # Always analyze held stocks (might need to sell)
            if sym in held_symbols:
                priority_symbols.add(sym)
                continue
            # Analyze stocks with ai_score >= 70 (potential buy candidates)
            if hasattr(s, 'ai_score') and s.ai_score and s.ai_score >= 70:
                priority_symbols.add(sym)
                continue
            # Analyze stocks with significant recent price change (>3%)
            if hasattr(s, 'price_change_pct') and s.price_change_pct and abs(s.price_change_pct) >= 3.0:
                priority_symbols.add(sym)
                continue

        # If no priority stocks found, fall back to all
        if not priority_symbols:
            symbols = all_symbols
            logger.info(f"[TA] 无优先股票，分析全部 {len(symbols)} 只")
        else:
            symbols = [s for s in all_symbols if s in priority_symbols]
            skipped = len(all_symbols) - len(symbols)
            logger.info(
                f"[TA] 智能过滤: 分析 {len(symbols)} 只 (持仓{len(held_symbols)}只 + 高分/大波动), "
                f"跳过 {skipped} 只低优先级股票"
            )
    if ta_cash < 0:
        ta_cash = 0

    logger.info(f"[TA] 可用现金: ${ta_cash:,.2f}, 当前持仓: {list(ta_holdings.keys())}")

    # Reset progress before prefetching so the frontend never shows stale data.
    _init_ta_progress(symbols)

    # 4a. Pre-fetch global news ONCE and share across all stocks
    _set_ta_progress_phase('preparing', '读取全局新闻缓存')
    global_news_cache = _build_global_news_cache()

    # 4b. Pre-build data caches for ALL symbols (fast, DB-only, no API calls)
    symbol_caches: Dict[str, dict] = {}
    for sym in symbols:
        try:
            symbol_caches[sym] = _build_data_cache_for_symbol(sym)
            # Inject shared global news into each symbol's cache
            symbol_caches[sym].update(global_news_cache)
            cached_keys = list(symbol_caches[sym].keys())
            logger.info(f"[TA] {sym} 预缓存: {len(cached_keys)} 条 ({cached_keys})")
        except Exception as e:
            logger.warning(f"[TA] {sym} 预缓存构建失败: {e}")
            symbol_caches[sym] = dict(global_news_cache)  # at least have global news

    total_cached = sum(len(v) for v in symbol_caches.values())
    logger.info(f"[TA] 预缓存完成: {total_cached} 条数据条目 (含全局新闻共享, 省去对应数量的外部API调用)")
    _set_ta_progress_phase('analyzing', f'预缓存完成，开始并行分析 {len(symbols)} 只股票')

    # 5. Parallel analysis with ThreadPoolExecutor + progress tracking
    decisions: Dict[str, dict] = {}
    completed = 0

    def _run_one(sym):
        _mark_ta_stock_started(sym)
        return sym, run_ta_for_stock(
            sym,
            data_cache=symbol_caches.get(sym),
            progress_callback=_handle_ta_node_progress,
        )

    workers = min(MAX_WORKERS, len(symbols))
    logger.info(f"[TA] 启动并行分析: {workers} 个并发线程")

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_one, sym): sym for sym in symbols}

            for future in as_completed(futures):
                sym = futures[future]
                try:
                    sym_result, decision = future.result()
                    decisions[sym_result] = decision
                    completed += 1
                    _mark_ta_stock_completed(sym_result, decision['action'])
                    logger.info(
                        f"[TA] 完成 {completed}/{len(symbols)}: "
                        f"{sym_result} -> {decision['action']}"
                    )
                except Exception as e:
                    decisions[sym] = {'action': 'hold', 'reason': f'并行执行异常: {str(e)[:200]}'}
                    completed += 1
                    _mark_ta_stock_completed(sym, 'error', error=True)
                    logger.error(f"[TA] {sym} 并行执行异常: {e}", exc_info=True)
    finally:
        _finish_ta_progress()

    logger.info(f"[TA] 全部分析完成: {len(decisions)} 只股票")

    # 6. Portfolio execution
    #
    # The TA model produces ratings; execution maps those ratings into a
    # target-weight portfolio. This makes buys deterministic, respects a cash
    # reserve, avoids tiny rebalance trades, and prevents equal-dollar buys
    # when conviction differs.
    symbol_order = {sym: idx for idx, sym in enumerate(symbols)}
    active_holdings = {
        symbol: dict(holding)
        for symbol, holding in ta_holdings.items()
        if (holding.get('shares') or 0) > 0
    }
    valid_trades = {}
    available_cash = ta_cash
    portfolio_value = _portfolio_value(active_holdings, available_cash, price_map)
    reserve_value = portfolio_value * TA_CASH_RESERVE_PCT if portfolio_value > 0 else 0

    logger.info(
        f"[TA] Portfolio execution: value=${portfolio_value:,.2f}, "
        f"cash=${available_cash:,.2f}, reserve=${reserve_value:,.2f}, "
        f"positions={len(active_holdings)}/{MAX_POSITIONS}"
    )

    # Process reductions first. Underweight trims; Sell exits.
    for symbol, dec in decisions.items():
        rating = _normalize_ta_rating(dec)
        if dec.get('action') != 'sell':
            continue
        if symbol not in active_holdings:
            _prepend_execution_note(
                dec,
                f"TA rated this {rating.title()}, but there is no current TA position to reduce.",
            )
            continue

        h = active_holdings.get(symbol, {})
        held_shares = h.get('shares', 0)
        price = _price_for_portfolio_value(symbol, h, price_map)
        if held_shares <= 0 or price <= 0:
            _prepend_execution_note(
                dec,
                "TA recommended reducing this holding, but execution skipped it because no usable price was available.",
            )
            continue

        if rating == 'UNDERWEIGHT':
            shares = round(held_shares * 0.5, 6)
            if shares <= 0:
                continue
            execution_note = (
                "TA rated the holding Underweight, so the portfolio layer trims "
                "toward half-size instead of exiting completely."
            )
        else:
            shares = held_shares
            execution_note = "TA rated the holding Sell, so the portfolio layer exits the position."

        _prepend_execution_note(decisions[symbol], execution_note)
        record = AiTradeRecord(
            trader=TRADER_NAME,
            symbol=symbol,
            action='sell',
            shares=shares,
            price=price,
            trade_date=today,
            reason=decisions[symbol].get('reason', ''),
        )
        db_session.add(record)
        available_cash += shares * price
        remaining_shares = max(0, held_shares - shares)
        if remaining_shares > 0:
            active_holdings[symbol]['shares'] = remaining_shares
        else:
            active_holdings.pop(symbol, None)
        valid_trades[symbol] = {
            'action': 'sell',
            'shares': shares,
            'reason': decisions[symbol].get('reason', ''),
            'rating': decisions[symbol].get('rating'),
        }
        logger.info(f"[TA] SELL {symbol}: {shares} 股 @ ${price:.2f}")

    portfolio_value = _portfolio_value(active_holdings, available_cash, price_map)
    reserve_value = portfolio_value * TA_CASH_RESERVE_PCT if portfolio_value > 0 else 0
    buy_budget = max(0, available_cash - reserve_value)
    open_new_slots = max(0, MAX_POSITIONS - len(active_holdings))

    buy_meta = {}
    new_buy_candidates = []
    existing_topups = []
    for symbol, dec in decisions.items():
        rating = _normalize_ta_rating(dec)
        if rating not in {'BUY', 'OVERWEIGHT'}:
            continue

        price = price_map.get(symbol, 0)
        if price <= 0:
            _prepend_execution_note(
                dec,
                "TA rated this as a buy candidate, but execution skipped it because no current price was available.",
            )
            continue

        holding = active_holdings.get(symbol)
        current_value = (holding.get('shares', 0) * price) if holding else 0
        current_weight = current_value / portfolio_value if portfolio_value > 0 else 0
        target_weight = _target_weight_for_ta_candidate(dec, price)
        target_value = portfolio_value * target_weight
        gap_value = max(0, target_value - current_value)

        if holding and gap_value < portfolio_value * TA_MIN_REBALANCE_WEIGHT:
            _prepend_execution_note(
                dec,
                (
                    f"TA rated this {rating.title()}, but the existing position is already near "
                    f"target ({current_weight:.1%} current vs {target_weight:.1%} target), so no top-up was placed."
                ),
            )
            continue
        if not holding and gap_value < portfolio_value * TA_MIN_NEW_POSITION_WEIGHT:
            _prepend_execution_note(
                dec,
                (
                    f"TA rated this {rating.title()}, but its target position "
                    f"({target_weight:.1%}) is below the minimum new-position threshold."
                ),
            )
            continue

        score = _score_ta_buy_candidate(decisions[symbol], price)
        if holding:
            score += 8  # Small preference for topping up known holdings over opening new names.
        upside = _ta_candidate_upside(decisions[symbol], price)
        buy_meta[symbol] = {
            'score': score,
            'upside': upside,
            'rating': rating,
            'target_weight': target_weight,
            'target_value': target_value,
            'current_weight': current_weight,
            'current_value': current_value,
            'gap_value': gap_value,
            'is_existing': bool(holding),
        }
        if holding:
            existing_topups.append(symbol)
        else:
            new_buy_candidates.append(symbol)

    ranked_new_candidates = sorted(
        new_buy_candidates,
        key=lambda sym: (
            -buy_meta[sym]['score'],
            -TA_RATING_STRENGTH.get(buy_meta[sym]['rating'], 0),
            symbol_order.get(sym, 9999),
        ),
    )
    selected_new_candidates = ranked_new_candidates[:open_new_slots]
    skipped_by_cap = ranked_new_candidates[open_new_slots:]

    ranked_candidates = sorted(
        existing_topups + selected_new_candidates,
        key=lambda sym: (
            -buy_meta[sym]['score'],
            -buy_meta[sym]['target_weight'],
            symbol_order.get(sym, 9999),
        ),
    )
    all_ranked_candidates = sorted(
        existing_topups + ranked_new_candidates,
        key=lambda sym: (
            -buy_meta[sym]['score'],
            -buy_meta[sym]['target_weight'],
            symbol_order.get(sym, 9999),
        ),
    )
    for rank, symbol in enumerate(all_ranked_candidates, start=1):
        buy_meta[symbol]['rank'] = rank

    for symbol in skipped_by_cap:
        meta = buy_meta[symbol]
        _prepend_execution_note(
            decisions[symbol],
            (
                f"TA rated this {meta['rating'].title()}, but it ranked {meta['rank']} "
                f"among new-position candidates and the portfolio had only "
                f"{open_new_slots} open slot(s) under the {MAX_POSITIONS}-position cap."
            ),
        )

    if new_buy_candidates and open_new_slots <= 0:
        for symbol in new_buy_candidates:
            _prepend_execution_note(
                decisions[symbol],
                f"TA rated this as a buy candidate, but the portfolio is already at the {MAX_POSITIONS}-position cap.",
            )

    # Buy the gap to target, in rank order, while preserving cash reserve.
    buys_done = 0
    for symbol in ranked_candidates:
        if buy_budget <= 0:
            _prepend_execution_note(
                decisions[symbol],
                f"TA ranked this for purchase, but available cash above the {TA_CASH_RESERVE_PCT:.0%} reserve was exhausted.",
            )
            continue

        price = price_map.get(symbol, 0)
        meta = buy_meta[symbol]
        target_cash = min(meta['gap_value'], buy_budget)
        min_trade_value = (
            portfolio_value * TA_MIN_REBALANCE_WEIGHT
            if meta['is_existing']
            else portfolio_value * TA_MIN_NEW_POSITION_WEIGHT
        )
        if target_cash < min_trade_value:
            _prepend_execution_note(
                decisions[symbol],
                (
                    f"TA ranked this {meta['rank']}/{len(all_ranked_candidates)} for purchase, "
                    f"but the remaining target gap (${target_cash:,.2f}) is below the "
                    f"{TA_MIN_REBALANCE_WEIGHT:.0%} rebalance threshold."
                    if meta['is_existing']
                    else
                    f"TA ranked this {meta['rank']}/{len(all_ranked_candidates)} for purchase, "
                    f"but available cash (${target_cash:,.2f}) is below the "
                    f"{TA_MIN_NEW_POSITION_WEIGHT:.0%} minimum new-position threshold."
                ),
            )
            continue

        max_shares = int(target_cash / price) if price > 0 else 0
        if max_shares <= 0:
            logger.warning(f"[TA] {symbol} 现金不足买入1股，跳过")
            _prepend_execution_note(
                decisions[symbol],
                (
                    f"TA ranked this {meta['rank']}/{len(all_ranked_candidates)} "
                    f"for purchase, but its target gap (${target_cash:,.2f}) "
                    "was not enough to buy 1 share."
                ),
            )
            decisions[symbol]['reason'] = (
                f"TA建议买入，但模拟账户可用现金不足以买入1股（可用现金 ${available_cash:,.2f}）。\n\n"
                + decisions[symbol].get('reason', '')
            )
            continue
        # Validate against available cash
        cost = max_shares * price
        if cost > buy_budget:
            max_shares = int(buy_budget / price)
            cost = max_shares * price
        if max_shares <= 0:
            decisions[symbol]['reason'] = (
                f"TA建议买入，但模拟账户剩余现金不足以买入1股（可用现金 ${available_cash:,.2f}）。\n\n"
                + decisions[symbol].get('reason', '')
            )
            continue

        upside_text = (
            f", target upside {meta['upside']:.1%}"
            if meta.get('upside') is not None else ""
        )
        _prepend_execution_note(
            decisions[symbol],
            (
                f"Selected for purchase after ranking {meta['rank']}/{len(all_ranked_candidates)} "
                f"target-weight candidates. Rating={meta['rating'].title()}, score={meta['score']}, "
                f"current weight={meta['current_weight']:.1%}, target={meta['target_weight']:.1%}, "
                f"cash used=${cost:,.2f}{upside_text}."
            ),
        )
        record = AiTradeRecord(
            trader=TRADER_NAME,
            symbol=symbol,
            action='buy',
            shares=max_shares,
            price=price,
            trade_date=today,
            reason=decisions[symbol].get('reason', ''),
        )
        db_session.add(record)
        available_cash -= cost
        buy_budget -= cost
        buys_done += 1
        valid_trades[symbol] = {
            'action': 'buy',
            'shares': max_shares,
            'reason': decisions[symbol].get('reason', ''),
            'rating': decisions[symbol].get('rating'),
            'rank': meta['rank'],
            'score': meta['score'],
            'target_weight': meta['target_weight'],
        }
        logger.info(f"[TA] BUY {symbol}: {max_shares} 股 @ ${price:.2f}")

    # 7. Return HOLD analysis results for stocks that were analyzed but not traded.
    # Do not persist these as AiTradeRecord rows: transaction history should only
    # contain executed buy/sell rows with positive quantity and price.
    for symbol, dec in decisions.items():
        if symbol not in valid_trades and dec.get('reason'):
            valid_trades[symbol] = {
                'action': 'hold',
                'shares': 0,
                'reason': dec.get('reason', ''),
                'rating': dec.get('rating'),
            }
            logger.info(f"[TA] HOLD {symbol}: 仅返回分析结果，不写入交易历史")

    if not valid_trades:
        logger.info("[TA] 今日无分析结果")

    try:
        recommendation_items = []
        for symbol in symbols:
            outcome = valid_trades.get(symbol)
            if not outcome:
                continue
            price = price_map.get(symbol, 0) or 0
            shares = outcome.get('shares') or 0
            recommendation_items.append({
                'symbol': symbol,
                'action': outcome.get('action') or 'hold',
                'rating': outcome.get('rating') or decisions.get(symbol, {}).get('rating'),
                'raw_action': decisions.get(symbol, {}).get('action'),
                'shares': shares,
                'price': price,
                'amount': round(shares * price, 2) if shares and price else 0,
                'trade_date': today.isoformat(),
                'reason': outcome.get('reason', ''),
            })

        recommendations_payload = {
            'date': today.isoformat(),
            'generated_at': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            'items': recommendation_items,
        }

        recommendation_symbols = sorted({
            item.get('symbol') for item in recommendation_items if item.get('symbol')
        })
        existing_recommendations = {}
        if recommendation_symbols:
            rows = db_session.query(TaRecommendationRecord).filter(
                TaRecommendationRecord.symbol.in_(recommendation_symbols),
                TaRecommendationRecord.trade_date == today,
            ).all()
            existing_recommendations = {row.symbol: row for row in rows}

        for item in recommendation_items:
            symbol = item.get('symbol')
            if not symbol:
                continue
            record = existing_recommendations.get(symbol)
            if not record:
                record = TaRecommendationRecord(symbol=symbol, trade_date=today)
                db_session.add(record)
                existing_recommendations[symbol] = record
            record.rating = str(item.get('rating') or item.get('action') or 'Hold')
            record.action = str(item.get('action') or 'hold').lower()
            record.raw_action = item.get('raw_action')
            record.shares = float(item.get('shares') or 0)
            record.price = float(item.get('price') or 0)
            record.amount = float(item.get('amount') or 0)
            record.reason = item.get('reason') or ''

        recommendations_row = db_session.query(UserSetting).filter_by(
            key=TA_LAST_RECOMMENDATIONS_SETTING_KEY,
        ).first()
        if recommendations_row:
            recommendations_row.value = _json.dumps(recommendations_payload)
        else:
            db_session.add(UserSetting(
                key=TA_LAST_RECOMMENDATIONS_SETTING_KEY,
                value=_json.dumps(recommendations_payload),
            ))

        if not selected_symbols:
            last_run = db_session.query(UserSetting).filter_by(key=TA_LAST_RUN_SETTING_KEY).first()
            if last_run:
                last_run.value = today.isoformat()
            else:
                db_session.add(UserSetting(key=TA_LAST_RUN_SETTING_KEY, value=today.isoformat()))

        db_session.commit()
        executed_trade_count = sum(
            1 for trade in valid_trades.values()
            if trade.get('action') in ('buy', 'sell') and (trade.get('shares') or 0) > 0
        )
        logger.info(
            f"[TA] 保存 {executed_trade_count} 条实际交易记录，"
            f"返回 {len(valid_trades)} 条分析结果"
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"[TA] 保存交易记录失败: {e}")
        return {}

    return valid_trades
