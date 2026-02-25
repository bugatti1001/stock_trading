"""
Simple in-memory TTL cache for expensive operations.
Avoids repeated DB queries and AI calls within short windows.
"""
import time
import threading
from typing import Any, Optional, Callable
from functools import wraps


class TTLCache:
    """Thread-safe in-memory cache with TTL expiration and max size."""

    def __init__(self, max_size: int = 1000):
        self._store: dict = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        """Get value if key exists and hasn't expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() > entry['expires_at']:
                del self._store[key]
                return None
            return entry['value']

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        """Set a value with TTL (default 5 minutes)."""
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                # Evict expired entries first
                now = time.time()
                expired = [k for k, v in self._store.items() if now > v['expires_at']]
                for k in expired:
                    del self._store[k]
                # Still over limit: remove the oldest entry
                if len(self._store) >= self._max_size:
                    oldest_key = min(self._store, key=lambda k: self._store[k]['expires_at'])
                    del self._store[oldest_key]
            self._store[key] = {
                'value': value,
                'expires_at': time.time() + ttl_seconds,
            }

    def delete(self, key: str):
        """Delete a specific key."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()

    def invalidate_prefix(self, prefix: str):
        """Invalidate all keys starting with prefix."""
        with self._lock:
            keys_to_delete = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._store[k]


# Global cache instance
cache = TTLCache()


def cached(key_prefix: str, ttl_seconds: int = 300):
    """Decorator for caching function results."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key from prefix and args
            cache_key = f"{key_prefix}:{':'.join(str(a) for a in args)}"
            if kwargs:
                cache_key += f":{':'.join(f'{k}={v}' for k, v in sorted(kwargs.items()))}"

            result = cache.get(cache_key)
            if result is not None:
                return result

            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl_seconds)
            return result
        # Expose invalidation method
        wrapper.invalidate = lambda: cache.invalidate_prefix(key_prefix)
        return wrapper
    return decorator
