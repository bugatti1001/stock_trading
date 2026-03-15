"""
Migration: 修复 shares_outstanding 单位不一致问题。

问题：
雪球 API 对部分股票的 total_shares 返回"亿股"单位（值 < 10000），
而其他所有字段（revenue, total_assets 等）均为实际完整数值。
导致 shares_outstanding 与其他字段量级不匹配。

修复策略：
- shares_outstanding < 10000 且同一条记录的 total_assets 或 revenue >= 1e8
  → 判定为"亿股"单位，乘以 1e8 修正为实际股数
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    """执行迁移"""
    inspector = inspect(engine)

    if 'financial_data' not in inspector.get_table_names():
        return

    with engine.connect() as conn:
        # 查找需要修复的记录
        rows = conn.execute(text("""
            SELECT id, shares_outstanding, total_assets, revenue
            FROM financial_data
            WHERE shares_outstanding IS NOT NULL
              AND shares_outstanding < 10000
              AND (total_assets >= 1e8 OR revenue >= 1e8)
        """)).fetchall()

        if not rows:
            logger.info('  shares_outstanding 单位无需修复')
            return

        count = 0
        for row in rows:
            new_val = row[1] * 1e8
            conn.execute(text("""
                UPDATE financial_data
                SET shares_outstanding = :new_val
                WHERE id = :id
            """), {'new_val': new_val, 'id': row[0]})
            count += 1

        conn.commit()
        logger.info(f'  修复 {count} 条 shares_outstanding 单位 (亿股 → 实际股数)')
