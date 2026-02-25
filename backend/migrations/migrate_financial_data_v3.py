"""
Migration v3: 重建 financial_data 表 — 对齐 Excel Raw 表字段结构
支持多币种（USD/CNY/HKD），删除所有旧版衍生比率字段。

策略：DROP + create_all（DB 为空时安全；有数据时需先备份）
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    """执行迁移：重建 financial_data 表为 v3 schema"""
    inspector = inspect(engine)

    # 检查表是否存在
    if 'financial_data' not in inspector.get_table_names():
        logger.info("[Migration v3] financial_data 表不存在，将由 create_all 创建")
        _create_all(engine)
        return

    # 检查是否已经是 v3（通过检测 v3 独有字段 'currency'）
    existing_columns = {col['name'] for col in inspector.get_columns('financial_data')}
    if 'currency' in existing_columns and 'net_income_to_parent' in existing_columns:
        logger.info("[Migration v3] financial_data 已是 v3 schema，跳过")
        return

    # 检查表中是否有数据
    with engine.connect() as conn:
        result = conn.execute(text('SELECT COUNT(*) FROM financial_data'))
        row_count = result.scalar()

    if row_count > 0:
        logger.warning(f"[Migration v3] financial_data 表有 {row_count} 条数据！使用 ALTER TABLE 策略")
        _alter_table_strategy(engine, existing_columns)
    else:
        logger.info("[Migration v3] financial_data 表为空，直接 DROP + CREATE")
        with engine.connect() as conn:
            conn.execute(text('DROP TABLE IF EXISTS financial_data'))
            conn.commit()
        _create_all(engine)

    logger.info("[Migration v3] 迁移完成")


def _create_all(engine):
    """使用 SQLAlchemy create_all 创建所有表"""
    from app.models.base import Base
    Base.metadata.create_all(engine)
    logger.info("[Migration v3] create_all 完成")


def _alter_table_strategy(engine, existing_columns):
    """
    有数据时的安全迁移：只添加新列，不删除旧列（SQLite 不支持 DROP COLUMN）。
    旧列会保留在表中但代码不再使用。
    """
    NEW_COLUMNS_V3 = [
        ('report_name', 'VARCHAR(100)'),
        ('currency', 'VARCHAR(10) DEFAULT "CNY"'),
        ('net_income_to_parent', 'FLOAT'),
        ('admin_expense', 'FLOAT'),
        ('finance_cost', 'FLOAT'),
        ('accounts_receivable', 'FLOAT'),
        ('inventory', 'FLOAT'),
        ('investments', 'FLOAT'),
        ('accounts_payable', 'FLOAT'),
        ('contract_liability_change_pct', 'FLOAT'),
        ('non_current_assets', 'FLOAT'),
        ('nav_per_share', 'FLOAT'),
        ('dividends_per_share', 'FLOAT'),
    ]

    with engine.connect() as conn:
        for col_name, col_type in NEW_COLUMNS_V3:
            if col_name not in existing_columns:
                try:
                    conn.execute(text(f'ALTER TABLE financial_data ADD COLUMN {col_name} {col_type}'))
                    conn.commit()
                    logger.info(f"  + 新增列 financial_data.{col_name}")
                except Exception:
                    conn.rollback()

    logger.info("[Migration v3] ALTER TABLE 策略完成（旧列保留，新列已添加）")
