"""
Migration: add indexes for common dashboard, scorer, and agent history queries.
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


INDEXES = (
    (
        'stock_news_analysis',
        'ix_news_analysis_analyzed_at',
        'CREATE INDEX IF NOT EXISTS ix_news_analysis_analyzed_at '
        'ON stock_news_analysis (analyzed_at)',
    ),
    (
        'stock_news_analysis',
        'ix_news_analysis_symbol_analyzed_at',
        'CREATE INDEX IF NOT EXISTS ix_news_analysis_symbol_analyzed_at '
        'ON stock_news_analysis (symbol, analyzed_at)',
    ),
    (
        'ai_trade_records',
        'ix_ai_trades_trader_date',
        'CREATE INDEX IF NOT EXISTS ix_ai_trades_trader_date '
        'ON ai_trade_records (trader, trade_date)',
    ),
)


def run(engine):
    with engine.connect() as conn:
        tables = {
            r[0] for r in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ))
        }

        for table_name, index_name, sql in INDEXES:
            if table_name not in tables:
                continue
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.warning(f'Failed to create index {index_name}: {e}')
