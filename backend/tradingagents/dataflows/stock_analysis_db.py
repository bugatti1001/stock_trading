"""
stock_analysis SQLite vendor for TradingAgents.

Reads fundamental data and news from the stock_analysis project's SQLite database
instead of fetching from yfinance / Alpha Vantage.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Annotated

from .config import get_config

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    config = get_config()
    return config.get(
        "stock_analysis_db_path",
        "/Users/hongyuanyuan/Documents/claude_projects/stock_analysis/backend/data/stock_trading_admin.db",
    )


def _connect():
    path = _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------

def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date in yyyy-mm-dd format"] = None,
) -> str:
    try:
        with _connect() as conn:
            cur = conn.cursor()

            # Company info
            cur.execute(
                "SELECT * FROM stocks WHERE UPPER(symbol) = ? LIMIT 1",
                (ticker.upper(),),
            )
            stock = cur.fetchone()
            if not stock:
                return f"No data found for symbol '{ticker}' in stock_analysis database"

            stock_id = stock["id"]

            # Latest annual financial data
            cur.execute(
                """SELECT * FROM financial_data
                   WHERE stock_id = ? AND UPPER(period) = 'ANNUAL'
                   ORDER BY fiscal_year DESC LIMIT 1""",
                (stock_id,),
            )
            fd = cur.fetchone()

        # Build report
        lines = []
        lines.append(f"# Company Fundamentals for {ticker.upper()}")
        lines.append(f"# Source: stock_analysis SQLite database")
        lines.append("")

        # Stock-level info
        _add(lines, "Name", stock["name"])
        _add(lines, "Sector", stock["sector"])
        _add(lines, "Industry", stock["industry"])
        _add(lines, "Exchange", stock["exchange"])
        _add(lines, "Market", stock["market"])
        _add(lines, "Market Cap", stock["market_cap"])
        _add(lines, "PE Ratio", stock["pe_ratio"])
        _add(lines, "PB Ratio", stock["pb_ratio"])
        _add(lines, "EPS", stock["eps"])
        _add(lines, "Dividend Yield", stock["dividend_yield"])
        _add(lines, "Current Price", stock["current_price"])
        _add(lines, "Volume", stock["volume"])
        _add(lines, "Avg Volume", stock["avg_volume"])
        _add(lines, "Employees", stock["employees"])
        _add(lines, "Website", stock["website"])

        if fd:
            lines.append("")
            lines.append(f"## Financial Data (FY{fd['fiscal_year']})")
            # Income statement highlights
            _add(lines, "Revenue", fd["revenue"])
            _add(lines, "Cost of Revenue", fd["cost_of_revenue"])
            _add(lines, "Operating Income", fd["operating_income"])
            _add(lines, "Net Income", fd["net_income"])
            _add(lines, "Net Income to Parent", fd["net_income_to_parent"])
            _add(lines, "R&D Expense", fd["rd_expense"])
            # Margins (derived)
            if fd["revenue"] and fd["cost_of_revenue"] is not None:
                gm = (fd["revenue"] - fd["cost_of_revenue"]) / fd["revenue"]
                lines.append(f"Gross Margin: {gm:.2%}")
            if fd["revenue"] and fd["operating_income"] is not None:
                om = fd["operating_income"] / fd["revenue"]
                lines.append(f"Operating Margin: {om:.2%}")
            if fd["revenue"] and fd["net_income"] is not None:
                nm = fd["net_income"] / fd["revenue"]
                lines.append(f"Net Margin: {nm:.2%}")
            # Balance sheet highlights
            _add(lines, "Total Assets", fd["total_assets"])
            _add(lines, "Total Equity", fd["total_equity"])
            _add(lines, "Cash and Equivalents", fd["cash_and_equivalents"])
            _add(lines, "Short Term Borrowings", fd["short_term_borrowings"])
            _add(lines, "Long Term Borrowings", fd["long_term_borrowings"])
            # Cash flow highlights
            _add(lines, "Operating Cash Flow", fd["operating_cash_flow"])
            _add(lines, "Capital Expenditure", fd["capital_expenditure"])
            if fd["operating_cash_flow"] and fd["capital_expenditure"] is not None:
                fcf = fd["operating_cash_flow"] - fd["capital_expenditure"]
                lines.append(f"Free Cash Flow: {fcf}")
            # Per share
            _add(lines, "Shares Outstanding", fd["shares_outstanding"])
            _add(lines, "Dividends Per Share", fd["dividends_per_share"])
            _add(lines, "NAV Per Share", fd["nav_per_share"])
            _add(lines, "Currency", fd["currency"])

        return "\n".join(lines)

    except Exception as e:
        return f"Error retrieving fundamentals for {ticker} from stock_analysis DB: {str(e)}"


def _add(lines: list, label: str, value):
    if value is not None:
        lines.append(f"{label}: {value}")


# ---------------------------------------------------------------------------
# Financial statements (balance sheet, cashflow, income statement)
# ---------------------------------------------------------------------------

def _get_financial_statement(
    ticker: str,
    freq: str,
    curr_date: str,
    fields: list[tuple[str, str]],
    title: str,
) -> str:
    """Generic financial statement query.

    Args:
        fields: list of (display_name, column_name) pairs
    """
    try:
        with _connect() as conn:
            cur = conn.cursor()

            cur.execute(
                "SELECT id FROM stocks WHERE UPPER(symbol) = ? LIMIT 1",
                (ticker.upper(),),
            )
            row = cur.fetchone()
            if not row:
                return f"No data found for symbol '{ticker}' in stock_analysis database"
            stock_id = row["id"]

            if freq.lower() == "quarterly":
                period_filter = "UPPER(fd.period) IN ('Q1','Q2','Q3','Q4')"
                limit = 8
            else:
                period_filter = "UPPER(fd.period) = 'ANNUAL'"
                limit = 4

            cur.execute(
                f"""SELECT fd.* FROM financial_data fd
                    WHERE fd.stock_id = ? AND {period_filter}
                    ORDER BY fd.fiscal_year DESC, fd.period DESC
                    LIMIT ?""",
                (stock_id, limit),
            )
            rows = cur.fetchall()

        if not rows:
            return f"No {title.lower()} data found for symbol '{ticker}'"

        # Build CSV-style output (columns = periods, rows = fields)
        # Header row: period labels
        period_labels = []
        for r in rows:
            period_upper = r["period"].upper() if r["period"] else ""
            if period_upper == "ANNUAL":
                label = f"FY{r['fiscal_year']}"
            else:
                label = f"FY{r['fiscal_year']} {r['period']}"
            period_labels.append(label)

        header = f"# {title} data for {ticker.upper()} ({freq})\n"
        header += f"# Source: stock_analysis SQLite database\n\n"

        # CSV header
        csv_lines = ["," + ",".join(period_labels)]
        for display_name, col_name in fields:
            vals = []
            for r in rows:
                v = r[col_name]
                vals.append(str(v) if v is not None else "")
            csv_lines.append(f"{display_name}," + ",".join(vals))

        return header + "\n".join(csv_lines)

    except Exception as e:
        return f"Error retrieving {title.lower()} for {ticker} from stock_analysis DB: {str(e)}"


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    fields = [
        ("Cash And Equivalents", "cash_and_equivalents"),
        ("Accounts Receivable", "accounts_receivable"),
        ("Inventory", "inventory"),
        ("Investments", "investments"),
        ("Total Assets", "total_assets"),
        ("Non Current Assets", "non_current_assets"),
        ("Accounts Payable", "accounts_payable"),
        ("Current Liabilities", "current_liabilities"),
        ("Short Term Borrowings", "short_term_borrowings"),
        ("Long Term Borrowings", "long_term_borrowings"),
        ("Total Equity", "total_equity"),
        ("Shares Outstanding", "shares_outstanding"),
        ("NAV Per Share", "nav_per_share"),
    ]
    return _get_financial_statement(ticker, freq, curr_date, fields, "Balance Sheet")


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    fields = [
        ("Operating Cash Flow", "operating_cash_flow"),
        ("Capital Expenditure", "capital_expenditure"),
    ]
    return _get_financial_statement(ticker, freq, curr_date, fields, "Cash Flow")


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date"] = None,
) -> str:
    fields = [
        ("Revenue", "revenue"),
        ("Cost Of Revenue", "cost_of_revenue"),
        ("Operating Income", "operating_income"),
        ("Net Income", "net_income"),
        ("Net Income To Parent", "net_income_to_parent"),
        ("Adjusted Net Income", "adjusted_net_income"),
        ("Selling Expense", "selling_expense"),
        ("Admin Expense", "admin_expense"),
        ("R&D Expense", "rd_expense"),
        ("Finance Cost", "finance_cost"),
    ]
    return _get_financial_statement(ticker, freq, curr_date, fields, "Income Statement")


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def get_news(
    ticker: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "start date yyyy-mm-dd"],
    end_date: Annotated[str, "end date yyyy-mm-dd"],
) -> str:
    try:
        with _connect() as conn:
            cur = conn.cursor()

            cur.execute(
                """SELECT sna.* FROM stock_news_analysis sna
                   JOIN stocks s ON sna.stock_id = s.id
                   WHERE UPPER(s.symbol) = ?
                     AND date(sna.analyzed_at) BETWEEN ? AND ?
                   ORDER BY sna.analyzed_at DESC""",
                (ticker.upper(), start_date, end_date),
            )
            rows = cur.fetchall()

        if not rows:
            return f"No news found for {ticker} between {start_date} and {end_date}"

        news_str = ""
        for r in rows:
            sentiment = r["sentiment"] or "unknown"
            summary = r["summary"] or ""
            analyzed_at = r["analyzed_at"] or ""

            stock_name = r["stock_name"] if r["stock_name"] else ticker
            news_str += f"### [{sentiment.upper()}] {stock_name} ({analyzed_at})\n"
            news_str += f"{summary}\n"

            # Include key events if available
            key_events = r["key_events"]
            if key_events:
                try:
                    events = json.loads(key_events) if isinstance(key_events, str) else key_events
                    if events:
                        news_str += "Key events:\n"
                        for evt in events:
                            news_str += f"  - {evt}\n"
                except (json.JSONDecodeError, TypeError):
                    pass

            # Include news sources if available
            sources = r["news_sources"]
            if sources:
                try:
                    src_list = json.loads(sources) if isinstance(sources, str) else sources
                    if src_list:
                        news_str += "Sources:\n"
                        for src in src_list:
                            if isinstance(src, dict):
                                news_str += f"  - {src.get('title', src.get('url', str(src)))}\n"
                            else:
                                news_str += f"  - {src}\n"
                except (json.JSONDecodeError, TypeError):
                    pass

            news_str += "\n"

        return f"## {ticker} News Analysis, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching news for {ticker} from stock_analysis DB: {str(e)}"


def get_global_news(
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "days to look back"] = 7,
    limit: Annotated[int, "max articles"] = 10,
) -> str:
    try:
        start_dt = datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
        start_date = start_dt.strftime("%Y-%m-%d")

        with _connect() as conn:
            cur = conn.cursor()

            cur.execute(
                """SELECT sna.*, s.symbol, s.name as stock_name_joined
                   FROM stock_news_analysis sna
                   LEFT JOIN stocks s ON sna.stock_id = s.id
                   WHERE date(sna.analyzed_at) BETWEEN ? AND ?
                   ORDER BY sna.analyzed_at DESC
                   LIMIT ?""",
                (start_date, curr_date, limit),
            )
            rows = cur.fetchall()

        if not rows:
            return f"No global news found for {curr_date}"

        news_str = ""
        for r in rows:
            symbol = r["symbol"] if r["symbol"] else (r["stock_name_joined"] if r["stock_name_joined"] else "N/A")
            sentiment = r["sentiment"] or "unknown"
            summary = r["summary"] or ""
            analyzed_at = r["analyzed_at"] or ""

            news_str += f"### [{sentiment.upper()}] {symbol} ({analyzed_at})\n"
            news_str += f"{summary}\n\n"

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        return f"Error fetching global news from stock_analysis DB: {str(e)}"
