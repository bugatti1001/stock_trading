"""
Standardized API response helpers
Ensures consistent response format across all endpoints
"""
from flask import jsonify
from typing import Any, Optional


def success_response(data: Any = None, message: str = None,
                     status_code: int = 200, **kwargs) -> tuple:
    """Return a standardized success response."""
    body = {'success': True}
    if message:
        body['message'] = message
    if data is not None:
        body['data'] = data
    body.update(kwargs)
    return jsonify(body), status_code


def error_response(error: str, status_code: int = 400, **kwargs) -> tuple:
    """Return a standardized error response.
    For 5xx errors, the detailed message is replaced with a generic one
    to avoid leaking internal implementation details.
    """
    if status_code >= 500:
        import logging
        logging.getLogger(__name__).error(f"Internal error (HTTP {status_code}): {error}")
        body = {'success': False, 'error': '服务器内部错误，请稍后重试'}
    else:
        body = {'success': False, 'error': error}
    body.update(kwargs)
    return jsonify(body), status_code


def paginated_response(items: list, total: int, page: int, page_size: int,
                       **kwargs) -> tuple:
    """Return a standardized paginated response."""
    body = {
        'success': True,
        'data': items,
        'pagination': {
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size if page_size > 0 else 0,
        }
    }
    body.update(kwargs)
    return jsonify(body), 200
