from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

import yfinance as yf
from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.financial_data import FinancialData
from app.services.data_source_manager import data_source_manager
from app.utils.cache import cache
from app.utils.validation import parse_date_safe

logger = logging.getLogger(__name__)


class StockService:
    """Service for stock-related operations with intelligent data source switching"""

    def __init__(self, db_session: Session) -> None:
        self.db: Session = db_session
        self.data_manager = data_source_manager

    def get_all_stocks(self, in_pool_only: bool = True) -> list[Stock]:
        """Get all stocks, optionally filter by pool status"""
        query = self.db.query(Stock)
        if in_pool_only:
            query = query.filter_by(in_pool=True)
        return query.all()

    def get_stock_by_symbol(self, symbol: str) -> Optional[Stock]:
        """Get stock by symbol"""
        return self.db.query(Stock).filter_by(symbol=symbol.upper()).first()

    def add_stock(self, symbol: str, name: Optional[str] = None, fetch_data: bool = True) -> Stock:
        """Add a new stock to the pool with intelligent data source switching"""
        from app.utils.market_utils import (
            normalize_symbol, detect_market,
            get_currency_for_symbol, get_exchange_for_symbol,
        )

        symbol = normalize_symbol(symbol)
        market = detect_market(symbol)
        currency = get_currency_for_symbol(symbol)

        if fetch_data:
            # 使用智能数据源管理器获取数据
            logger.info(f"添加股票 {symbol} (市场={market})，使用智能数据源...")
            data = self.data_manager.fetch_stock_info(symbol)

            if data:
                # 成功从某个数据源获取数据
                ipo_date = parse_date_safe(data.get('ipo_date'))

                stock = Stock(
                    symbol=symbol,
                    name=name or data.get('name', symbol),
                    market=market,
                    currency=currency,
                    exchange=data.get('exchange') or get_exchange_for_symbol(symbol),
                    sector=data.get('sector'),
                    industry=data.get('industry'),
                    description=data.get('description'),
                    website=data.get('website'),
                    market_cap=data.get('market_cap'),
                    employees=data.get('employees'),
                    ipo_date=ipo_date,
                    current_price=data.get('current_price'),
                    volume=data.get('volume'),
                    avg_volume=data.get('avg_volume'),
                    pe_ratio=data.get('pe_ratio'),
                    pb_ratio=data.get('pb_ratio'),
                    dividend_yield=data.get('dividend_yield'),
                    eps=data.get('eps'),
                    in_pool=True,
                    is_active=True
                )

                # Store metadata（包含字段级数据来源）
                stock.extra_data = {
                    'data_source': data.get('data_source'),
                    'fetched_at': data.get('fetched_at'),
                    'field_sources': data.get('field_sources'),
                    'cik': data.get('cik')  # 如果是SEC数据
                }

                logger.info(f"✅ 成功从 {data.get('data_source')} 获取 {symbol} 数据")

            else:
                # 所有数据源都失败，创建基础条目
                logger.warning(f"⚠️  所有数据源失败，创建 {symbol} 基础条目")
                stock = Stock(
                    symbol=symbol,
                    name=name or symbol,
                    market=market,
                    currency=currency,
                    exchange=get_exchange_for_symbol(symbol),
                    in_pool=True,
                    is_active=True,
                    extra_data={
                        'error': 'All data sources failed',
                        'fetched_at': datetime.now(timezone.utc).isoformat()
                    }
                )
        else:
            # Create basic entry without fetching
            stock = Stock(
                symbol=symbol,
                name=name or symbol,
                market=market,
                currency=currency,
                exchange=get_exchange_for_symbol(symbol),
                in_pool=True,
                is_active=True
            )

        self.db.add(stock)
        self.db.commit()
        self.db.refresh(stock)

        # 自动获取财务数据（行情之后）
        if fetch_data:
            try:
                count = self.fetch_and_store_financials(symbol)
                logger.info(f"添加 {symbol} 时自动获取 {count} 条财务数据")
            except Exception as e:
                logger.warning(f"添加 {symbol} 时获取财务数据失败: {e}")

        cache.invalidate_prefix("stocks_summary:")
        return stock

    def update_stock(self, symbol: str, data: dict[str, Any]) -> Optional[Stock]:
        """Update stock information"""
        stock = self.get_stock_by_symbol(symbol)
        if not stock:
            return None

        # Update allowed fields
        allowed_fields = [
            'name', 'sector', 'industry', 'description', 'website',
            'market_cap', 'current_price', 'pe_ratio', 'pb_ratio',
            'dividend_yield', 'eps', 'notes', 'in_pool'
        ]

        for field in allowed_fields:
            if field in data:
                setattr(stock, field, data[field])

        self.db.commit()
        self.db.refresh(stock)
        cache.invalidate_prefix("stocks_summary:")
        return stock

    def remove_from_pool(self, symbol: str) -> bool:
        """Remove stock from pool (soft delete)"""
        stock = self.get_stock_by_symbol(symbol)
        if not stock:
            return False

        stock.in_pool = False
        self.db.commit()
        cache.invalidate_prefix("stocks_summary:")
        return True

    def refresh_stock_data(self, symbol: str, price_only: bool = False) -> Optional[Stock]:
        """Refresh stock data with intelligent data source switching.

        Args:
            symbol: Stock ticker symbol
            price_only: If True, only fetch market data (price, volume, market_cap)
                        and skip financial/fundamental data refresh.
                        Useful after manual upload to avoid overwriting user data.
        """
        stock = self.get_stock_by_symbol(symbol)
        if not stock:
            return None

        logger.info(f"刷新股票 {symbol} 数据，使用智能数据源...")

        # 使用智能数据源管理器
        data = self.data_manager.fetch_stock_info(symbol)

        if data:
            # 成功获取数据，更新股票信息
            # For string fields, use `or` (empty string / None should fall back)
            stock.name = data.get('name') or stock.name
            stock.exchange = data.get('exchange') or stock.exchange
            stock.sector = data.get('sector') or stock.sector
            stock.industry = data.get('industry') or stock.industry
            stock.description = data.get('description') or stock.description
            stock.website = data.get('website') or stock.website

            # 解析 ipo_date
            if not stock.ipo_date:
                parsed_ipo = parse_date_safe(data.get('ipo_date'))
                if parsed_ipo:
                    stock.ipo_date = parsed_ipo

            # For numeric fields, use explicit None check so that 0 is preserved
            val = data.get('market_cap');  stock.market_cap = val if val is not None else stock.market_cap
            val = data.get('employees');   stock.employees = val if val is not None else stock.employees
            val = data.get('current_price'); stock.current_price = val if val is not None else stock.current_price
            val = data.get('volume');      stock.volume = val if val is not None else stock.volume
            val = data.get('avg_volume');  stock.avg_volume = val if val is not None else stock.avg_volume
            val = data.get('pe_ratio');    stock.pe_ratio = val if val is not None else stock.pe_ratio
            val = data.get('pb_ratio');    stock.pb_ratio = val if val is not None else stock.pb_ratio
            val = data.get('dividend_yield'); stock.dividend_yield = val if val is not None else stock.dividend_yield
            val = data.get('eps');         stock.eps = val if val is not None else stock.eps

            # Update extra_data - must reassign to trigger SQLAlchemy change detection
            new_extra = dict(stock.extra_data) if stock.extra_data else {}
            new_extra['last_refreshed'] = datetime.now(timezone.utc).isoformat()
            new_extra['data_source'] = data.get('data_source')
            new_extra['field_sources'] = data.get('field_sources')
            if data.get('ps_ratio') is not None:
                new_extra['ps_ratio'] = data.get('ps_ratio')
            if data.get('beta') is not None:
                new_extra['beta'] = data.get('beta')
            if data.get('roa_pct') is not None:
                new_extra['roa_pct'] = data.get('roa_pct')
            stock.extra_data = new_extra

            logger.info(f"✅ 成功从 {data.get('data_source')} 刷新 {symbol}")

            self.db.commit()
            self.db.refresh(stock)

            # 同时刷新财务数据（price_only 模式跳过，保留手动上传的数据）
            if not price_only:
                try:
                    count = self.fetch_and_store_financials(symbol)
                    logger.info(f"刷新 {symbol} 时更新 {count} 条财务数据")
                except Exception as e:
                    logger.warning(f"刷新 {symbol} 时获取财务数据失败: {e}")
            else:
                logger.info(f"⏭️  price_only 模式，跳过 {symbol} 财务数据刷新")

            cache.invalidate_prefix("stocks_summary:")
            return stock

        else:
            # 所有数据源都失败，记录错误但返回现有数据（不抛出异常）
            logger.warning(f"⚠️  所有数据源暂时不可用，{symbol} 保留现有数据")
            new_extra = dict(stock.extra_data) if stock.extra_data else {}
            new_extra['last_refresh_error'] = 'All data sources temporarily unavailable (Yahoo rate limited, SEC EDGAR unavailable)'
            new_extra['last_refresh_attempt'] = datetime.now(timezone.utc).isoformat()
            stock.extra_data = new_extra
            self.db.commit()
            self.db.refresh(stock)
            # 返回带警告标记的股票对象，让调用者知道数据未更新
            stock._refresh_warning = 'All data sources temporarily unavailable. Showing existing data.'
            return stock

    def get_stock_financials(self, symbol: str) -> Optional[list[FinancialData]]:
        """Get financial data for a stock"""
        stock = self.get_stock_by_symbol(symbol)
        if not stock:
            return None

        return self.db.query(FinancialData).filter_by(
            stock_id=stock.id
        ).order_by(FinancialData.fiscal_year.desc()).all()

    def fetch_and_store_financials(self, symbol: str, years: int = 5) -> int:
        """
        获取并存储财务数据 - 雪球优先 + 其他源补缺

        Args:
            symbol: 股票代码
            years: 获取年数

        Returns:
            保存的财务数据记录数
        """
        import json as _json
        stock = self.get_stock_by_symbol(symbol)
        if not stock:
            logger.error(f"股票 {symbol} 不存在")
            return 0

        logger.info(f"获取 {symbol} 的财务数据（{years}年）...")

        # 使用智能数据源管理器（现在返回带 field_sources 的合并数据）
        financial_data_list = self.data_manager.fetch_financial_data(symbol, years)

        if not financial_data_list:
            logger.error(f"无法从任何数据源获取 {symbol} 的财务数据")
            return 0

        # FinancialData 模型的有效列名
        valid_columns = {c.name for c in FinancialData.__table__.columns} - {'id', 'created_at', 'updated_at', 'stock_id'}

        from app.models.financial_data import ReportPeriod
        period_map = {'FY': ReportPeriod.ANNUAL, 'Annual': ReportPeriod.ANNUAL,
                      'Q1': ReportPeriod.Q1, 'Q2': ReportPeriod.Q2,
                      'Q3': ReportPeriod.Q3, 'Q4': ReportPeriod.Q4}

        saved_count = 0
        for fin_data in financial_data_list:
            try:
                _raw_p = fin_data.get('period', 'FY')
                _query_period = period_map.get(_raw_p, ReportPeriod.ANNUAL) if isinstance(_raw_p, str) else _raw_p

                existing = self.db.query(FinancialData).filter_by(
                    stock_id=stock.id,
                    fiscal_year=fin_data.get('fiscal_year'),
                    period=_query_period
                ).first()

                # 提取 field_sources（不属于模型列，单独处理）
                field_sources = fin_data.pop('field_sources', None)

                # 只保留模型上有的字段
                clean_data = {k: v for k, v in fin_data.items() if k in valid_columns}

                # report_date 必须是 date 对象（SQLite 要求）
                rd = clean_data.get('report_date')
                if rd and not isinstance(rd, date):
                    clean_data['report_date'] = parse_date_safe(rd)

                # period 映射：'FY' → ReportPeriod.ANNUAL
                raw_period = clean_data.get('period', 'FY')
                if isinstance(raw_period, str):
                    clean_data['period'] = period_map.get(raw_period, ReportPeriod.ANNUAL)

                if existing:
                    # 更新现有记录
                    for key, value in clean_data.items():
                        if value is not None:
                            setattr(existing, key, value)
                    # 保存 field_sources 到 extended_metrics
                    if field_sources:
                        ext = existing.extended_metrics_dict or {}
                        ext['field_sources'] = field_sources
                        existing.extended_metrics_dict = ext
                    logger.debug(f"更新 {symbol} {fin_data.get('fiscal_year')} 财务数据 (source: {clean_data.get('data_source')})")
                else:
                    # 创建新记录
                    new_financial = FinancialData(
                        stock_id=stock.id,
                        **clean_data
                    )
                    # 保存 field_sources 到 extended_metrics
                    if field_sources:
                        new_financial.extended_metrics_dict = {'field_sources': field_sources}
                    self.db.add(new_financial)
                    logger.debug(f"新增 {symbol} {fin_data.get('fiscal_year')} 财务数据 (source: {clean_data.get('data_source')})")

                saved_count += 1

            except Exception as e:
                logger.error(f"保存财务数据失败: {e}")
                continue

        self.db.commit()
        logger.info(f"✅ 成功保存 {symbol} 的 {saved_count} 条财务数据")

        return saved_count
