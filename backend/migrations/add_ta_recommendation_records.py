"""
Migration: create ta_recommendation_records table.

Stores one raw TradingAgents recommendation per symbol per day so the UI can
show rating consistency/trend independently of executed transaction history.
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def run(engine):
    with engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ta_recommendation_records'"
        ))]
        if tables:
            return

        try:
            conn.execute(text("""
                CREATE TABLE ta_recommendation_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    symbol VARCHAR(20) NOT NULL,
                    trade_date DATE NOT NULL,
                    rating VARCHAR(20) NOT NULL DEFAULT 'Hold',
                    action VARCHAR(10) NOT NULL DEFAULT 'hold',
                    raw_action VARCHAR(20),
                    shares FLOAT NOT NULL DEFAULT 0,
                    price FLOAT NOT NULL DEFAULT 0,
                    amount FLOAT NOT NULL DEFAULT 0,
                    reason TEXT,
                    CONSTRAINT uq_ta_rec_symbol_date UNIQUE (symbol, trade_date)
                )
            """))
            conn.execute(text(
                "CREATE INDEX ix_ta_rec_symbol ON ta_recommendation_records (symbol)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_ta_rec_trade_date ON ta_recommendation_records (trade_date)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_ta_rec_date_symbol ON ta_recommendation_records (trade_date, symbol)"
            ))
            conn.commit()
            logger.info('Created ta_recommendation_records table')
        except Exception as e:
            logger.error(f'Failed to create ta_recommendation_records table: {e}')
            conn.rollback()
