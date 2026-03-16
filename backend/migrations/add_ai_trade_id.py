"""
Migration: Add ai_trade_id column to conversations table.
Links a conversation to an AI trade record for trade discussion.
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    inspector = inspect(engine)
    if 'conversations' not in inspector.get_table_names():
        return
    existing = {col['name'] for col in inspector.get_columns('conversations')}
    if 'ai_trade_id' in existing:
        return
    with engine.connect() as conn:
        try:
            conn.execute(text(
                'ALTER TABLE conversations ADD COLUMN ai_trade_id INTEGER'
            ))
            conn.commit()
            logger.info('Added conversations.ai_trade_id column')
        except Exception as e:
            logger.error(f'Failed to add ai_trade_id column: {e}')
            conn.rollback()
