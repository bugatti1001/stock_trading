"""
市场检测与符号规范化工具
支持美股(US)、中国A股(CN)、港股(HK)三个市场
"""
import re
from typing import Optional

# Market type constants
MARKET_US = 'US'
MARKET_CN = 'CN'   # A-shares (Shanghai + Shenzhen)
MARKET_HK = 'HK'

# Symbol patterns
_CN_PATTERN = re.compile(r'^(SH|SZ)\d{6}$')
_HK_PATTERN = re.compile(r'^HK\d{5}$')
_US_PATTERN = re.compile(r'^[A-Z]{1,10}$')

# Currency mapping
MARKET_CURRENCY = {
    MARKET_US: 'USD',
    MARKET_CN: 'CNY',
    MARKET_HK: 'HKD',
}

# Currency display symbol
CURRENCY_SIGN = {
    'USD': '$',
    'CNY': '¥',
    'HKD': 'HK$',
}

# Exchange mapping by symbol prefix
_EXCHANGE_MAP = {
    'SH': 'SSE',    # Shanghai Stock Exchange
    'SZ': 'SZSE',   # Shenzhen Stock Exchange
    'HK': 'HKEX',   # Hong Kong Stock Exchange
}


def normalize_symbol(symbol: str) -> str:
    """Normalize stock symbol: strip whitespace, uppercase."""
    return symbol.strip().upper()


def detect_market(symbol: str) -> str:
    """Detect market type from symbol format.

    Returns MARKET_US, MARKET_CN, or MARKET_HK.
    """
    s = symbol.upper()
    if _CN_PATTERN.match(s):
        return MARKET_CN
    if _HK_PATTERN.match(s):
        return MARKET_HK
    return MARKET_US


def get_currency_for_symbol(symbol: str) -> str:
    """Return currency code (USD/CNY/HKD) for the given symbol."""
    return MARKET_CURRENCY.get(detect_market(symbol), 'USD')


def get_currency_sign(currency: str) -> str:
    """Return display symbol ($, ¥, HK$) for a currency code."""
    return CURRENCY_SIGN.get(currency, '$')


def get_exchange_for_symbol(symbol: str) -> Optional[str]:
    """Return exchange name based on symbol prefix.

    SH→SSE, SZ→SZSE, HK→HKEX, US→None (determined from data source).
    """
    s = symbol.upper()
    prefix = s[:2]
    return _EXCHANGE_MAP.get(prefix)


def is_cn_stock(symbol: str) -> bool:
    return detect_market(symbol) == MARKET_CN


def is_hk_stock(symbol: str) -> bool:
    return detect_market(symbol) == MARKET_HK


def is_us_stock(symbol: str) -> bool:
    return detect_market(symbol) == MARKET_US
