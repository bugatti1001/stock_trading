"""
Migration: 为 stocks 表新增 market/currency 列，拓宽 symbol 列。
同时拓宽 trade_records.symbol 列。
SQLite 兼容 — 使用 ALTER TABLE ADD COLUMN（SQLite 不支持 ALTER COLUMN）。
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    """执行迁移"""
    inspector = inspect(engine)

    # ── stocks 表 ──
    if 'stocks' in inspector.get_table_names():
        existing = {col['name'] for col in inspector.get_columns('stocks')}

        new_columns = [
            ('market', 'VARCHAR(5)'),
            ('currency', 'VARCHAR(5) DEFAULT "USD"'),
        ]

        with engine.connect() as conn:
            for col_name, col_type in new_columns:
                if col_name not in existing:
                    try:
                        conn.execute(text(
                            f'ALTER TABLE stocks ADD COLUMN {col_name} {col_type}'
                        ))
                        conn.commit()
                        logger.info(f'  + stocks.{col_name} 列已添加')
                    except Exception as e:
                        conn.rollback()
                        logger.warning(f'  跳过 stocks.{col_name}: {e}')

            # 回填已有股票为 US 市场
            if 'market' not in existing:
                try:
                    conn.execute(text(
                        "UPDATE stocks SET market = 'US' WHERE market IS NULL"
                    ))
                    conn.execute(text(
                        "UPDATE stocks SET currency = 'USD' WHERE currency IS NULL"
                    ))
                    conn.commit()
                    logger.info('  回填现有股票 market=US, currency=USD')
                except Exception as e:
                    conn.rollback()
                    logger.warning(f'  回填失败: {e}')
    else:
        logger.info('stocks 表不存在，将由 create_all 创建')

    # 注意：SQLite 不支持 ALTER COLUMN 修改列宽度。
    # symbol 列 String(10) → String(20) 的变更会在下次 create_all 时自动生效。
    # 对于已有表，SQLite 实际上不强制 VARCHAR 长度限制，所以不需要额外操作。

    logger.info('[Migration] add_market_currency 完成')
