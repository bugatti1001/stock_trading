"""
AI 财务数据补全服务 — 从互联网获取财务数据页面，用 Claude 单次提取缺失字段
Strategy: fetch financial data pages ourselves, then send to Claude in ONE call.
"""
import json
import logging
import os
import re
import time
from datetime import date
from typing import Dict, Any, List

from app.config.database import db_session
from app.config.settings import get_anthropic_key, AI_MODEL
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.services.ai_extractor import (
    _ALL_MERGE_FIELDS, _values_conflict, _is_rate_limit_error, _parse_retry_after,
)
from app.utils.ai_helpers import parse_ai_json_response

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

_METADATA_FIELDS = {'period_end_date', 'fiscal_year', 'period', 'report_name', 'currency'}
BACKFILL_FIELDS = [f for f in _ALL_MERGE_FIELDS if f not in _METADATA_FIELDS]

WEB_FETCH_MAX_CHARS = 15000

# ── Data Fetching (we do it ourselves, no tool_use) ───────────

def _fetch_page_text(url: str) -> str:
    """Fetch a URL and return clean text content."""
    import requests
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'head', 'meta', 'link', 'nav', 'footer', 'header', 'aside', 'iframe']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text[:WEB_FETCH_MAX_CHARS]
    except Exception as e:
        logger.warning(f"[AI Backfill] Fetch {url} failed: {e}")
        return ""


def _google_search(query: str) -> List[Dict]:
    """Search Google CSE, return list of {title, snippet, url}."""
    import requests
    api_key = os.getenv('GOOGLE_CUSTOM_SEARCH_API_KEY', '')
    cx = os.getenv('GOOGLE_CUSTOM_SEARCH_CX', '')
    if not api_key or not cx or api_key == 'your_google_cse_api_key_here':
        return []
    try:
        resp = requests.get(
            'https://www.googleapis.com/customsearch/v1',
            params={'key': api_key, 'cx': cx, 'q': query, 'num': 3},
            timeout=10,
        )
        resp.raise_for_status()
        return [{'title': i.get('title', ''), 'url': i.get('link', '')}
                for i in resp.json().get('items', [])[:3]]
    except Exception as e:
        logger.warning(f"[AI Backfill] Google search failed: {e}")
        return []


def _gather_financial_text(symbol: str, stock_name: str, target_years: List[int]) -> str:
    """
    Gather financial data text from known sources.
    Try macrotrends first (comprehensive), fall back to stockanalysis, then Google search.
    """
    texts = []

    # Strategy 1: Try well-known financial data sites directly
    ticker = symbol.replace('SH', '').replace('SZ', '').replace('HK', '')
    urls_to_try = [
        f"https://www.macrotrends.net/stocks/charts/{ticker}/{ticker}/financial-statements",
        f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/",
        f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/balance-sheet/",
        f"https://stockanalysis.com/stocks/{ticker.lower()}/financials/cash-flow-statement/",
    ]

    for url in urls_to_try:
        text = _fetch_page_text(url)
        if text and len(text) > 500:
            texts.append(f"=== Source: {url} ===\n{text}")
            logger.info(f"[AI Backfill] Fetched {len(text)} chars from {url}")
        if len('\n'.join(texts)) > 30000:
            break

    # Strategy 2: If we got very little, try Google search
    if len('\n'.join(texts)) < 2000:
        year_str = ' '.join(str(y) for y in target_years[:2])
        search_results = _google_search(f"{symbol} {stock_name} annual financial data {year_str} revenue net income total assets")
        for item in search_results:
            text = _fetch_page_text(item['url'])
            if text and len(text) > 500:
                texts.append(f"=== Source: {item['url']} ===\n{text}")
                logger.info(f"[AI Backfill] Fetched {len(text)} chars from {item['url']}")
            if len('\n'.join(texts)) > 30000:
                break

    combined = '\n\n'.join(texts)
    logger.info(f"[AI Backfill] Total gathered text: {len(combined)} chars from {len(texts)} sources")
    return combined


# ── Claude Single-Call Extraction ─────────────────────────────

def _build_extraction_prompt(stock: Stock, yearly_data: Dict[int, Dict], target_years: List[int], financial_text: str) -> str:
    """Build prompt for single-call extraction."""
    identity = f"Stock: {stock.symbol} ({stock.name or 'N/A'})"
    if stock.market:
        identity += f", Market: {stock.market}"

    year_sections = []
    for year in target_years:
        data = yearly_data.get(year, {})
        missing = [f for f in BACKFILL_FIELDS if data.get(f) is None]
        if not missing:
            continue
        known_summary = ", ".join(f"{f}={data[f]}" for f in BACKFILL_FIELDS if data.get(f) is not None)
        year_sections.append(
            f"FY{year} — Missing: {', '.join(missing)}"
            + (f"\n  Already known: {known_summary}" if known_summary else "")
        )

    if not year_sections:
        return ""

    return f"""你是专业财务分析师。从以下财务数据网页中提取缺失的年度财务指标。
Extract missing annual financial data from the web page content below.

{identity}

需要补全的字段 / Fields to fill:
{chr(10).join(year_sections)}

字段说明 / Field descriptions:
- revenue: 营业收入 (actual full value, e.g. 60922000000)
- cost_of_revenue: 营业成本
- operating_income: 营业利润
- net_income: 净利润
- net_income_to_parent: 归母净利润 (same as net_income for US stocks)
- adjusted_net_income: 扣非净利润
- selling_expense: 销售费用（美股通常为 SG&A 合并值，含管理费用）/ Selling Expense (for US stocks, use SG&A if not separately reported)
- admin_expense: 管理费用（美股如已计入 selling_expense 的 SG&A 中则填 null）/ G&A Expense (null if already included in SG&A above)
- rd_expense: 研发费用
- finance_cost: 财务费用/利息支出
- cash_and_equivalents: 现金及等价物
- accounts_receivable: 应收账款
- inventory: 存货
- investments: 可变现金融资产（不含长期股权投资）/ Liquid financial investments: short-term investments + marketable securities + trading securities + available-for-sale securities. EXCLUDE long-term equity investments in subsidiaries/associates (strategic holdings)
- accounts_payable: 应付账款
- contract_liability_change_pct: 合同负债同比变动(小数, 0.2=20%)
- short_term_borrowings: 短期借款（含一年内到期的长期借款）/ Short-term Borrowings (incl. current portion of long-term debt)
- long_term_borrowings: 长期借款 + 应付债券（不含一年内到期部分）/ Long-term Debt (incl. bonds payable, excl. current portion)
- total_assets: 总资产
- total_equity: 归属于母公司股东权益（不含少数股东权益）/ Stockholders' Equity attributable to parent (EXCLUDE minority/non-controlling interest)
- non_current_assets: 非流动资产
- current_liabilities: 流动负债
- operating_cash_flow: 经营现金流
- capital_expenditure: 资本开支(正数)
- shares_outstanding: 总股本(实际股数) / Total shares outstanding (actual number, e.g. 1090000000)
- dividends_per_share: 每股分红
- nav_per_share: 每股净资产

要求 / Requirements:
1. 所有金额为实际完整数值 / All monetary values in ACTUAL full numbers (not millions/billions)
   e.g. "$60.9B" → 60900000000, "$1.2M" → 1200000
2. shares_outstanding 为实际股数 / actual number of shares (e.g. 1.09B shares = 1090000000)
3. 找不到填 null / Use null if not found in the data
4. 只返回 JSON / Return ONLY the JSON object below, no other text

Return format:
{{
  "{target_years[0]}": {{"revenue": ..., "cost_of_revenue": ..., ...}},
  ...
}}

以下是从财务数据网站获取的内容 / Financial data from web sources:
{financial_text}"""


# ── Main Entry Point ──────────────────────────────────────────

def run_ai_backfill(symbol: str) -> Dict[str, Any]:
    """
    Run AI-powered backfill: fetch financial pages, extract with single Claude call.
    """
    import anthropic

    api_key = get_anthropic_key()
    if not api_key:
        raise ValueError("未配置 Claude API Key，请在登录时输入")

    stock = db_session.query(Stock).filter_by(symbol=symbol.upper()).first()
    if not stock:
        raise ValueError(f"股票 {symbol} 不存在")

    # Find target years: actual DB years with empty fields
    from sqlalchemy import desc
    all_annual_fds = db_session.query(FinancialData).filter_by(
        stock_id=stock.id, period=ReportPeriod.ANNUAL,
    ).order_by(desc(FinancialData.fiscal_year)).limit(5).all()

    if all_annual_fds:
        candidate_years = []
        for fd in all_annual_fds:
            has_empty = any(getattr(fd, f, None) is None for f in BACKFILL_FIELDS)
            if has_empty:
                candidate_years.append(fd.fiscal_year)
            if len(candidate_years) >= 3:
                break
        target_years = candidate_years if candidate_years else [fd.fiscal_year for fd in all_annual_fds[:3]]
    else:
        current_year = date.today().year
        target_years = list(range(current_year - 1, current_year - 4, -1))

    # Load existing data
    yearly_data: Dict[int, Dict] = {}
    currency_map: Dict[int, str] = {}
    for year in target_years:
        fd = db_session.query(FinancialData).filter_by(
            stock_id=stock.id, fiscal_year=year, period=ReportPeriod.ANNUAL,
        ).first()
        if fd:
            yearly_data[year] = {f: getattr(fd, f, None) for f in BACKFILL_FIELDS}
            currency_map[year] = fd.currency or 'USD'
        else:
            yearly_data[year] = {f: None for f in BACKFILL_FIELDS}
            currency_map[year] = stock.currency or 'USD'

    # Step 1: Gather financial text from web (2-5 seconds)
    logger.info(f"[AI Backfill] {symbol}: gathering web data for years {target_years}")
    financial_text = _gather_financial_text(symbol, stock.name or '', target_years)
    if not financial_text or len(financial_text) < 200:
        raise RuntimeError(f"无法获取 {symbol} 的财务数据网页，请检查网络连接")

    # Step 2: Build prompt
    prompt = _build_extraction_prompt(stock, yearly_data, target_years, financial_text)
    if not prompt:
        return {
            'auto_filled': {}, 'conflicts': {}, 'unchanged': {}, 'not_found': {},
            'stock_info': {'symbol': stock.symbol, 'name': stock.name, 'market': stock.market},
            'target_years': [str(y) for y in target_years], 'currency': currency_map,
        }

    # Step 3: Single Claude call (5-15 seconds)
    logger.info(f"[AI Backfill] {symbol}: calling Claude for extraction ({len(prompt)} chars prompt)")
    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=AI_MODEL,
                max_tokens=4096,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < 2:
                wait = _parse_retry_after(e) or 15
                logger.warning(f"[AI Backfill] Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    else:
        raise RuntimeError("Claude API 调用失败")

    final_text = response.content[0].text
    logger.info(f"[AI Backfill] {symbol}: got {len(final_text)} chars response, "
                f"in/out={response.usage.input_tokens}/{response.usage.output_tokens}")

    # Step 4: Parse JSON
    ai_result = parse_ai_json_response(final_text)
    if not ai_result:
        json_match = re.search(r'\{[\s\S]*\}', final_text)
        if json_match:
            try:
                ai_result = json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        if not ai_result:
            raise RuntimeError(f"AI 返回数据解析失败: {final_text[:300]}")

    # Step 5: Compare with existing data
    auto_filled = {}
    conflicts = {}
    unchanged = {}
    not_found = {}

    for year in target_years:
        year_key = str(year)
        ai_year_data = ai_result.get(year_key, ai_result.get(year, {}))
        if not isinstance(ai_year_data, dict):
            not_found[year_key] = BACKFILL_FIELDS[:]
            continue

        existing = yearly_data.get(year, {})
        year_auto, year_conflicts, year_unchanged, year_not_found = {}, {}, [], []

        for field in BACKFILL_FIELDS:
            ai_val = ai_year_data.get(field)
            current_val = existing.get(field)

            if ai_val is not None:
                try:
                    ai_val = float(ai_val)
                except (ValueError, TypeError):
                    if str(ai_val).strip().lower() in ('', 'null', 'none', 'n/a'):
                        ai_val = None

            if ai_val is None:
                if current_val is None:
                    year_not_found.append(field)
                continue

            if current_val is None:
                year_auto[field] = ai_val
            elif _values_conflict(field, current_val, ai_val):
                year_conflicts[field] = {'current': current_val, 'ai': ai_val}
            else:
                year_unchanged.append(field)

        if year_auto:
            auto_filled[year_key] = year_auto
        if year_conflicts:
            conflicts[year_key] = year_conflicts
        if year_unchanged:
            unchanged[year_key] = year_unchanged
        if year_not_found:
            not_found[year_key] = year_not_found

    logger.info(f"[AI Backfill] {symbol}: auto={sum(len(v) for v in auto_filled.values())}, "
                f"conflict={sum(len(v) for v in conflicts.values())}, "
                f"not_found={sum(len(v) for v in not_found.values())}")

    return {
        'auto_filled': auto_filled,
        'conflicts': conflicts,
        'unchanged': unchanged,
        'not_found': not_found,
        'stock_info': {'symbol': stock.symbol, 'name': stock.name, 'market': stock.market},
        'target_years': [str(y) for y in target_years],
        'currency': currency_map,
    }
