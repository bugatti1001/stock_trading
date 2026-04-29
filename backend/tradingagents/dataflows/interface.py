import logging
import threading
from typing import Annotated, Optional

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError
from .stock_analysis_db import (
    get_fundamentals as get_sa_fundamentals,
    get_balance_sheet as get_sa_balance_sheet,
    get_cashflow as get_sa_cashflow,
    get_income_statement as get_sa_income_statement,
    get_news as get_sa_news,
    get_global_news as get_sa_global_news,
)

# Configuration and routing logic
from .config import get_config

logger = logging.getLogger(__name__)

# ── Thread-local pre-injected data cache ──────────────────────────────────
# Allows callers (e.g. stock_analysis) to inject DB data so that
# route_to_vendor returns it instantly without hitting any external API.
#
# Structure:  _tls.cache = { "get_fundamentals:AAPL": "...", ... }
# The key format is  "<method>:<first_arg_uppercased>"  for per-ticker
# methods, or  "<method>"  for global methods like get_global_news.
_tls = threading.local()


def inject_data_cache(cache: dict):
    """Inject pre-fetched data into the current thread's cache.

    Args:
        cache: dict mapping  "<method>:<TICKER>"  (or  "<method>")  to the
               pre-formatted string that the tool would normally return.
               Example: {"get_fundamentals:AAPL": "...", "get_news:AAPL": "..."}
    """
    _tls.cache = cache
    logger.info(f"[DataCache] Injected {len(cache)} cached entries for current thread")


def clear_data_cache():
    """Remove all pre-injected data from the current thread."""
    _tls.cache = {}


def get_prefetched_data(ticker: str, keys: list) -> dict:
    """Retrieve pre-fetched data from cache for given keys.

    Used by analyst nodes to check if data is already available,
    so they can inject it directly into messages and skip tool calls.

    Args:
        ticker: Stock ticker symbol
        keys: List of method names (e.g. ["get_fundamentals", "get_balance_sheet"])

    Returns:
        dict mapping method name -> cached data string.
        Only includes keys that had cache hits.
    """
    cache = getattr(_tls, 'cache', None)
    if not cache:
        return {}

    result = {}
    sym = ticker.upper()
    for method in keys:
        # Try method:TICKER first, then method alone
        for key in [f"{method}:{sym}", method]:
            hit = cache.get(key)
            if hit is not None:
                result[method] = hit
                break
        # Also try method:TICKER:* prefix match for indicators
        if method not in result:
            prefix = f"{method}:{sym}:"
            for k, v in cache.items():
                if k.startswith(prefix):
                    sub_key = k.split(":", 2)[-1]  # e.g. "rsi"
                    result.setdefault(method, "")
                    result[method] += f"\n--- {sub_key} ---\n{v}\n"

    return result


def _lookup_cache(method: str, *args) -> Optional[str]:
    """Try to find a cached result for this method + args combination.

    Lookup order (most specific → least specific):
      1. method:arg0:arg1   (e.g. "get_indicators:AAPL:rsi")
      2. method:arg0        (e.g. "get_fundamentals:AAPL")
      3. method             (e.g. "get_global_news")
    """
    cache = getattr(_tls, 'cache', None)
    if not cache:
        return None

    # Try increasingly general keys
    keys_to_try = []
    if len(args) >= 2:
        keys_to_try.append(f"{method}:{str(args[0]).upper()}:{str(args[1])}")
    if args:
        keys_to_try.append(f"{method}:{str(args[0]).upper()}")
    keys_to_try.append(method)

    for key in keys_to_try:
        hit = cache.get(key)
        if hit is not None:
            logger.info(f"[DataCache] HIT  {key}  ({len(hit)} chars)")
            return hit

    return None

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "stock_analysis",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "stock_analysis": get_sa_fundamentals,
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "stock_analysis": get_sa_balance_sheet,
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "stock_analysis": get_sa_cashflow,
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "stock_analysis": get_sa_income_statement,
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "stock_analysis": get_sa_news,
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "stock_analysis": get_sa_global_news,
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support.

    Priority order:
    1. Thread-local injected cache (from inject_data_cache)
    2. Configured vendor chain (primary → fallback)
    """
    # ── 1. Check pre-injected cache first ──
    cached = _lookup_cache(method, *args)
    if cached is not None:
        return cached

    # ── 2. Normal vendor routing ──
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)
        except AlphaVantageRateLimitError:
            continue  # Only rate limits trigger fallback

    raise RuntimeError(f"No available vendor for '{method}'")