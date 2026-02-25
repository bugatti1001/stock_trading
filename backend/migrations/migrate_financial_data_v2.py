"""
Migration: 为 financial_data 表新增 v2 字段（SQLite ALTER TABLE）
包含：资本支出明细、扣非净利润、有息负债结构、ROIC、股东回报、毛利率、扩展指标等
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


NEW_COLUMNS = [
    ('capital_expenditure', 'FLOAT'),
    ('maintenance_capex', 'FLOAT'),
    ('growth_capex', 'FLOAT'),
    ('non_recurring_items', 'FLOAT'),
    ('adjusted_net_income', 'FLOAT'),
    ('interest_bearing_debt', 'FLOAT'),
    ('short_term_borrowings', 'FLOAT'),
    ('long_term_borrowings', 'FLOAT'),
    ('roic', 'FLOAT'),
    ('invested_capital', 'FLOAT'),
    ('dividends_paid', 'FLOAT'),
    ('share_buyback_amount', 'FLOAT'),
    ('shares_outstanding', 'FLOAT'),
    ('gross_margin', 'FLOAT'),
    ('extended_metrics', 'TEXT'),
]


def run(engine):
    """执行迁移：为 financial_data 表添加 v2 字段"""
    with engine.connect() as conn:
        for col_name, col_type in NEW_COLUMNS:
            try:
                conn.execute(text(f'ALTER TABLE financial_data ADD COLUMN {col_name} {col_type}'))
                conn.commit()
                logger.info(f"  ✓ 新增列 financial_data.{col_name}")
            except Exception:
                conn.rollback()  # 列已存在，回滚后继续
