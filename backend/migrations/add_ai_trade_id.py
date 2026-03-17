"""
Migration: Add ai_trade_id column to conversations table.
Links a conversation to an AI trade record for trade discussion.
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    with engine.connect() as conn:
        # Check if table exists
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'"
        ))]
        if not tables:
            return
        # Check if column already exists
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info('conversations')"))]
        if 'ai_trade_id' in cols:
            return
        try:
            conn.execute(text(
                'ALTER TABLE conversations ADD COLUMN ai_trade_id INTEGER'
            ))
            conn.commit()
            logger.info('Added conversations.ai_trade_id column')
        except Exception as e:
            if 'duplicate column' not in str(e).lower():
                logger.error(f'Failed to add ai_trade_id column: {e}')
            conn.rollback()
