"""
Migration: Add trader column to ai_trade_records table.
Identifies which trader strategy created the record (default 'scorer').
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def run(engine):
    with engine.connect() as conn:
        # Check if table exists
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_trade_records'"
        ))]
        if not tables:
            return
        # Check if column already exists
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info('ai_trade_records')"))]
        if 'trader' in cols:
            return
        try:
            conn.execute(text(
                "ALTER TABLE ai_trade_records ADD COLUMN trader VARCHAR(30) NOT NULL DEFAULT 'scorer'"
            ))
            conn.commit()
            logger.info('Added ai_trade_records.trader column')
        except Exception as e:
            if 'duplicate column' not in str(e).lower():
                logger.error(f'Failed to add trader column: {e}')
            conn.rollback()
