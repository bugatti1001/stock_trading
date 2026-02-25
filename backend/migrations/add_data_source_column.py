"""
Migration: 为 financial_data 表新增 data_source 列。
记录每条财务记录的主要数据来源（'Xueqiu', 'Yahoo Finance', 'SEC EDGAR' 等）。
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    """执行迁移"""
    inspector = inspect(engine)

    if 'financial_data' not in inspector.get_table_names():
        logger.info('financial_data 表不存在，将由 create_all 创建')
        return

    existing = {col['name'] for col in inspector.get_columns('financial_data')}

    if 'data_source' in existing:
        logger.info('financial_data.data_source 列已存在，跳过')
        return

    with engine.connect() as conn:
        try:
            conn.execute(text(
                'ALTER TABLE financial_data ADD COLUMN data_source VARCHAR(50)'
            ))
            conn.commit()
            logger.info('  + financial_data.data_source 列已添加')
        except Exception as e:
            conn.rollback()
            logger.warning(f'  跳过 financial_data.data_source: {e}')

    logger.info('[Migration] add_data_source_column 完成')
