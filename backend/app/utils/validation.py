"""
Input validation utilities for API endpoints.
"""
import re
from datetime import date
from typing import Optional, Tuple
from flask import request

from app.config.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def validate_symbol(symbol: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate and normalize stock symbol.

    Supports:
      - US stocks: 1-10 uppercase letters (e.g., AAPL)
      - CN A-shares: SH/SZ + 6 digits (e.g., SH600519, SZ000858)
      - HK stocks: HK + 5 digits (e.g., HK00700)

    Returns (symbol, error).
    """
    if not symbol:
        return None, 'Stock symbol is required'
    symbol = symbol.strip().upper()
    if not (re.match(r'^[A-Z]{1,10}$', symbol) or
            re.match(r'^(SH|SZ)\d{6}$', symbol) or
            re.match(r'^HK\d{5}$', symbol)):
        return None, (
            f'Invalid stock symbol: {symbol}. '
            'US: 1-10 letters; CN A-shares: SH/SZ + 6 digits; '
            'HK: HK + 5 digits.'
        )
    return symbol, None


def validate_pagination() -> Tuple[int, int]:
    """Extract and validate pagination params from request args."""
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(MAX_PAGE_SIZE, max(1, int(request.args.get('page_size', DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page_size = DEFAULT_PAGE_SIZE
    return page, page_size


def validate_required_fields(data: dict, fields: list) -> Optional[str]:
    """Check that all required fields are present and non-empty. Returns error message or None."""
    if not data:
        return 'Request body is required'
    missing = [f for f in fields if not data.get(f)]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"
    return None


def validate_positive_number(value, field_name: str) -> Tuple[Optional[float], Optional[str]]:
    """Validate that a value is a positive number. Returns (value, error)."""
    try:
        num = float(value)
        if num <= 0:
            return None, f'{field_name} must be positive'
        return num, None
    except (ValueError, TypeError):
        return None, f'{field_name} must be a valid number'


def parse_date_safe(value) -> Optional[date]:
    """Safely parse a value to a date object.

    Accepts:
      - date object: returned as-is
      - str: parsed via fromisoformat (first 10 chars)
      - None / invalid: returns None
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except (ValueError, TypeError):
            return None
    return None
