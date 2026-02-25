"""Gunicorn configuration for production deployment."""

bind = "0.0.0.0:8000"
workers = 2  # SQLite doesn't handle high concurrency well
timeout = 120  # AI requests may be slow
keepalive = 5

# Logging
accesslog = "-"  # stdout
errorlog = "-"   # stderr
loglevel = "info"

# Security
limit_request_line = 8190
limit_request_fields = 100
