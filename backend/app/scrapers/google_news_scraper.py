"""
Multi-Market Stock News Scraper
美股: Finnhub Company News API
中国A股/港股: 东方财富(Eastmoney) 搜索接口

Output format (unified across all sources):
    { symbol: [{title, snippet, url, source, published_date, stock_symbol}] }

Caching:
    每天第一次请求某只股票的新闻时会调用外部 API，之后同一天内
    再次请求同一只股票直接返回缓存结果。缓存按 (symbol, date) 键入，
    新的一天自动失效。
"""
import json
import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional

import requests

from app.utils.market_utils import detect_market, MARKET_US

logger = logging.getLogger(__name__)

FINNHUB_NEWS_ENDPOINT = 'https://finnhub.io/api/v1/company-news'
EASTMONEY_SEARCH_ENDPOINT = 'https://search-api-web.eastmoney.com/search/jsonp'

# ── Server-side news cache ──────────────────────────────
# Key: (symbol, 'YYYY-MM-DD')  Value: List[Dict]
# Automatically invalidated when the date changes.
import threading as _threading
_news_cache: Dict[tuple, List[Dict]] = {}
_news_cache_date: str = ''  # tracks the current cache date
_news_cache_lock = _threading.Lock()


def _get_cache(symbol: str) -> Optional[List[Dict]]:
    """Return cached news for symbol if still valid (same calendar day)."""
    global _news_cache, _news_cache_date
    with _news_cache_lock:
        today_str = date.today().isoformat()
        if _news_cache_date != today_str:
            # New day: flush entire cache
            _news_cache.clear()
            _news_cache_date = today_str
            return None
        return _news_cache.get((symbol, today_str))


def _set_cache(symbol: str, news: List[Dict]) -> None:
    """Store news in same-day cache."""
    global _news_cache, _news_cache_date
    with _news_cache_lock:
        today_str = date.today().isoformat()
        if _news_cache_date != today_str:
            _news_cache.clear()
            _news_cache_date = today_str
        _news_cache[(symbol, today_str)] = news


def _filter_today_only(news_list: List[Dict]) -> List[Dict]:
    """Deprecated: no longer filters. Returns all news items as-is."""
    return news_list


class NewsSearchError(Exception):
    """News API error"""
    pass


def _get_finnhub_key() -> str:
    """从当前用户的数据库读取 Finnhub API Key，回退到环境变量"""
    try:
        from app.config.database import db_session
        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='finnhub_api_key').first()
        if row and row.value:
            return row.value
    except Exception:
        pass
    return os.getenv('FINNHUB_API_KEY', '')


# ── Finnhub (US stocks) ─────────────────────────────────

def _search_stock_news_finnhub(symbol: str, stock_name: str = '',
                               days_back: int = 7,
                               num_results: int = 8) -> List[Dict]:
    """Fetch recent company news via Finnhub (US stocks)."""
    api_key = _get_finnhub_key()
    if not api_key:
        logger.warning("FINNHUB_API_KEY not configured in .env")
        return []

    today = datetime.utcnow()
    from_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    to_date = today.strftime('%Y-%m-%d')

    params = {
        'symbol': symbol.upper(),
        'from': from_date,
        'to': to_date,
        'token': api_key,
    }

    try:
        response = requests.get(FINNHUB_NEWS_ENDPOINT, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict) and 'error' in data:
            logger.error(f"Finnhub API error for {symbol}: {data['error']}")
            return []

        if not isinstance(data, list):
            logger.warning(f"Finnhub unexpected response for {symbol}: {type(data)}")
            return []

        data.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        data = data[:num_results]

        news_list = []
        for item in data:
            ts = item.get('datetime', 0)
            published = (datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
                         if ts else None)

            news_list.append({
                'title': item.get('headline', 'Untitled'),
                'snippet': item.get('summary', ''),
                'url': item.get('url', ''),
                'source': item.get('source', ''),
                'published_date': published,
                'stock_symbol': symbol.upper(),
            })

        logger.info(f"Finnhub: fetched {len(news_list)} news items for {symbol}")
        return news_list

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"Finnhub rate limit hit for {symbol}")
        else:
            logger.error(f"Finnhub HTTP error for {symbol}: {e}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Finnhub request failed for {symbol}: {e}")
        return []
    except Exception as e:
        logger.error(f"Finnhub unexpected error for {symbol}: {e}")
        return []


# ── Eastmoney (CN / HK stocks) ─────────────────────────

def _search_stock_news_eastmoney(symbol: str, stock_name: str = '',
                                 days_back: int = 7,
                                 num_results: int = 8) -> List[Dict]:
    """Fetch recent stock news via Eastmoney search API (CN A-shares / HK).

    Uses stock_name as search keyword to find relevant news articles.
    Eastmoney returns results ranked by relevance; we use a wider time
    window (at least 30 days) to account for holiday gaps.
    """
    # Eastmoney search returns relevance-ranked results; widen the window
    # to ensure we still get news across market holidays (e.g. Spring Festival)
    effective_days_back = max(days_back, 30)
    keyword = stock_name or symbol
    try:
        resp = requests.get(
            EASTMONEY_SEARCH_ENDPOINT,
            params={
                'cb': 'cb',
                'param': json.dumps({
                    'uid': '',
                    'keyword': keyword,
                    'type': ['cmsArticleWebOld'],
                    'client': 'web',
                    'clientType': 'web',
                    'clientVersion': 'curr',
                    'param': {
                        'cmsArticleWebOld': {
                            'searchScope': 'default',
                            'sort': 'default',
                            'pageIndex': 1,
                            'pageSize': num_results,
                            'preTag': '',
                            'postTag': '',
                        }
                    },
                }),
            },
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': 'https://so.eastmoney.com/',
            },
            timeout=10,
        )
        resp.raise_for_status()

        # Strip JSONP wrapper: cb({...})
        text = resp.text.strip()
        if text.startswith('cb(') and text.endswith(')'):
            text = text[3:-1]

        data = json.loads(text)
        articles = data.get('result', {}).get('cmsArticleWebOld', [])
        if not articles:
            logger.info(f"Eastmoney: no news for {symbol} ({keyword})")
            return []

        cutoff = datetime.utcnow() - timedelta(days=effective_days_back)
        news_list: List[Dict] = []

        for item in articles:
            date_str = item.get('date', '')  # "2026-02-10 18:04:30"
            published = None
            if date_str:
                try:
                    dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    if dt < cutoff:
                        continue
                    published = dt.strftime('%Y-%m-%d')
                except ValueError:
                    published = date_str[:10] if len(date_str) >= 10 else None

            title = item.get('title', 'Untitled')
            snippet = item.get('content', '')
            # Remove any HTML tags
            if '<' in snippet:
                snippet = re.sub(r'<[^>]+>', '', snippet)

            news_list.append({
                'title': title,
                'snippet': snippet[:500],
                'url': item.get('url', ''),
                'source': item.get('mediaName', '东方财富'),
                'published_date': published,
                'stock_symbol': symbol,
            })

        logger.info(f"Eastmoney: fetched {len(news_list)} news items for {symbol}")
        return news_list

    except Exception as e:
        logger.error(f"Eastmoney news fetch failed for {symbol}: {e}")
        return []


# ── Unified entry points ────────────────────────────────

def search_stock_news(symbol: str, stock_name: str = '',
                      days_back: int = 7,
                      num_results: int = 8) -> List[Dict]:
    """
    Fetch recent company news for a single stock.
    Auto-routes to the appropriate data source based on market type.

    Results are filtered to **today's news only** and cached for the rest
    of the calendar day so that repeated page refreshes don't re-call
    external APIs.

    Args:
        symbol: Ticker symbol (e.g. "AAPL", "SH600519", "HK09888")
        stock_name: Stock name (used as search keyword for CN/HK)
        days_back: How many days of history to fetch from the source API
        num_results: Max items to return per stock

    Returns:
        List of news dicts with keys:
        title, snippet, url, source, published_date, stock_symbol
    """
    # Check cache first
    cached = _get_cache(symbol)
    if cached is not None:
        logger.debug(f"Cache hit for {symbol} ({len(cached)} items)")
        return cached

    # Fetch from external API
    market = detect_market(symbol)
    if market == MARKET_US:
        raw = _search_stock_news_finnhub(symbol, stock_name, days_back, num_results)
    else:
        # CN and HK → Eastmoney
        raw = _search_stock_news_eastmoney(symbol, stock_name, days_back, num_results)

    # Only keep today's news
    today_news = _filter_today_only(raw)
    logger.info(f"{symbol}: {len(raw)} raw → {len(today_news)} today-only items")

    # Cache the filtered result
    _set_cache(symbol, today_news)
    return today_news


def fetch_news_for_stocks(stocks: List[Dict],
                          days_back: int = 7,
                          num_per_stock: int = 8,
                          **kwargs) -> Dict[str, List[Dict]]:
    """
    Fetch news for multiple stocks. Returns dict keyed by symbol.
    Auto-routes to Finnhub (US) or Eastmoney (CN/HK) per stock.

    Args:
        stocks: List of dicts with 'symbol' (and optionally 'name') keys.
        days_back: Days of history per stock.
        num_per_stock: Max news items per stock.

    Returns:
        { "AAPL": [news_item, ...], "SH600519": [...], "HK09888": [...] }
    """
    results = {}

    def _fetch_one(stock):
        symbol = stock['symbol']
        name = stock.get('name', symbol)
        return symbol, search_stock_news(symbol, name, days_back, num_per_stock)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, s): s['symbol'] for s in stocks}
        for future in as_completed(futures, timeout=60):
            try:
                symbol, news = future.result()
                results[symbol] = news or []
            except Exception as e:
                symbol = futures[future]
                logger.warning(f"Failed to fetch news for {symbol}: {e}")
                results[symbol] = []

    total = sum(len(v) for v in results.values())
    logger.info(f"News: total {total} items for {len(stocks)} stocks")
    return results
