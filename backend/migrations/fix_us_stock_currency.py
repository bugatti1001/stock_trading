"""
Migration: 修复美股 financial_data 的 currency 和 data_source 字段。

问题：
1. 早期数据 currency 默认为 'CNY'，美股应为 'USD'
2. 早期数据 data_source 为 NULL，需根据数据模式推断来源

修复策略：
- US 市场股票的 financial_data.currency = 'CNY' → 'USD'
  （排除中概股如 NIO/BABA 等用 CNY 报告的公司）
- data_source=NULL 且 revenue >= 1,000,000 → 推断为 'Yahoo Finance'（标准单位）
- data_source=NULL 且 revenue < 1,000,000 → 推断为 'Xueqiu'（万元/百万单位）
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)

# 中概股列表：虽然 market=US，但财报用 CNY
# 如需要可在此扩充
_CNY_REPORTING_US_STOCKS = {'NIO', 'BABA', 'PDD', 'JD', 'BIDU', 'LI', 'XPEV', 'BILI',
                             'TME', 'IQ', 'HSAI', 'ZH', 'MNSO', 'VNET', 'WB'}


def run(engine):
    """执行迁移"""
    inspector = inspect(engine)

    if 'financial_data' not in inspector.get_table_names():
        logger.info('financial_data 表不存在，跳过')
        return

    if 'stocks' not in inspector.get_table_names():
        logger.info('stocks 表不存在，跳过')
        return

    with engine.connect() as conn:
        # 快速检查是否还有需要修复的记录
        cnt = conn.execute(text("""
            SELECT COUNT(*) FROM financial_data
            WHERE (currency = 'CNY' AND stock_id IN (
                       SELECT id FROM stocks WHERE market = 'US'
                       AND symbol NOT IN ({placeholders})
                   ))
               OR (data_source IS NULL AND revenue IS NOT NULL
                   AND stock_id IN (SELECT id FROM stocks))
        """.format(
            placeholders=','.join(f"'{s}'" for s in _CNY_REPORTING_US_STOCKS)
        ))).scalar()
        if cnt == 0:
            logger.info('  无需修复，跳过 fix_us_stock_currency')
            return
        # ── 1. 修复 US 市场非中概股的 currency ──
        try:
            # 获取 US 市场、非中概股、currency=CNY 的 financial_data 记录
            result = conn.execute(text("""
                UPDATE financial_data
                SET currency = 'USD'
                WHERE currency = 'CNY'
                  AND stock_id IN (
                      SELECT id FROM stocks
                      WHERE market = 'US'
                        AND symbol NOT IN ({placeholders})
                  )
            """.format(
                placeholders=','.join(f"'{s}'" for s in _CNY_REPORTING_US_STOCKS)
            )))
            conn.commit()
            count = result.rowcount
            logger.info(f'  修复 {count} 条美股 financial_data currency: CNY → USD')
        except Exception as e:
            conn.rollback()
            logger.warning(f'  修复 currency 失败: {e}')

        # ── 2. 修复 data_source=NULL 的记录 ──
        try:
            # 大值记录（revenue >= 1,000,000）→ Yahoo Finance
            result = conn.execute(text("""
                UPDATE financial_data
                SET data_source = 'Yahoo Finance'
                WHERE data_source IS NULL
                  AND revenue IS NOT NULL
                  AND revenue >= 1000000
                  AND stock_id IN (
                      SELECT id FROM stocks WHERE market = 'US'
                  )
            """))
            conn.commit()
            count1 = result.rowcount
            logger.info(f'  推断 {count1} 条记录 data_source = Yahoo Finance (大值)')
        except Exception as e:
            conn.rollback()
            logger.warning(f'  推断 Yahoo Finance 来源失败: {e}')

        try:
            # 小值记录（revenue < 1,000,000 且非 NULL）→ Xueqiu
            result = conn.execute(text("""
                UPDATE financial_data
                SET data_source = 'Xueqiu'
                WHERE data_source IS NULL
                  AND revenue IS NOT NULL
                  AND revenue < 1000000
                  AND stock_id IN (
                      SELECT id FROM stocks WHERE market = 'US'
                  )
            """))
            conn.commit()
            count2 = result.rowcount
            logger.info(f'  推断 {count2} 条记录 data_source = Xueqiu (小值)')
        except Exception as e:
            conn.rollback()
            logger.warning(f'  推断 Xueqiu 来源失败: {e}')

        # ── 3. CN/HK 市场 data_source=NULL → Xueqiu ──
        try:
            result = conn.execute(text("""
                UPDATE financial_data
                SET data_source = 'Xueqiu'
                WHERE data_source IS NULL
                  AND stock_id IN (
                      SELECT id FROM stocks WHERE market IN ('CN', 'HK')
                  )
            """))
            conn.commit()
            count3 = result.rowcount
            logger.info(f'  推断 {count3} 条 CN/HK 记录 data_source = Xueqiu')
        except Exception as e:
            conn.rollback()
            logger.warning(f'  推断 CN/HK Xueqiu 来源失败: {e}')

    logger.info('[Migration] fix_us_stock_currency 完成')
