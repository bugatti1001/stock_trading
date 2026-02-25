import csv
import io
import logging
from datetime import date
from flask import Blueprint, request, Response
from app.config.database import db_session
from app.models.stock import Stock
from app.models.financial_data import FinancialData
from app.services.stock_service import StockService
from app.services.kpi_calculator import compute_single_period_kpis
from app.utils.response import success_response, error_response, paginated_response
from app.utils.validation import validate_symbol, validate_pagination
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

bp = Blueprint('stocks', __name__)


def _get_stock_service() -> StockService:
    """延迟创建 StockService，避免模块级绑定 session"""
    return StockService(db_session)


@bp.route('/', methods=['GET'])
def get_stocks() -> tuple[Response, int]:
    """Get all stocks in pool, with optional pagination."""
    try:
        in_pool_only = request.args.get('in_pool', 'true').lower() == 'true'

        # Backward compatibility: if no 'page' param, return all results
        if request.args.get('page') is None:
            stocks = _get_stock_service().get_all_stocks(in_pool_only=in_pool_only)
            return success_response(
                data=[stock.to_dict() for stock in stocks],
                count=len(stocks),
            )

        # Paginated path
        page, page_size = validate_pagination()

        query = db_session.query(Stock)
        if in_pool_only:
            query = query.filter_by(in_pool=True)

        total = query.count()
        stocks = query.offset((page - 1) * page_size).limit(page_size).all()

        return paginated_response(
            items=[stock.to_dict() for stock in stocks],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as e:
        return error_response(str(e), 500)


@bp.route('/search', methods=['GET'])
def search_stocks() -> tuple[Response, int]:
    """搜索股票（支持中文名、拼音、代码）"""
    try:
        q = request.args.get('q', '').strip()
        if not q:
            return error_response('请输入搜索关键词', 400)

        from app.scrapers.xueqiu_scraper import xueqiu_scraper
        results = xueqiu_scraper.search_stocks(q, size=10)
        return success_response(data=results, count=len(results))
    except Exception as e:
        logger.error(f"搜索股票失败: {e}")
        return error_response(str(e), 500)


@bp.route('/<symbol>', methods=['GET'])
def get_stock(symbol: str) -> tuple[Response, int]:
    """Get stock by symbol"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        return success_response(data=stock.to_dict())
    except Exception as e:
        return error_response(str(e), 500)


@bp.route('/', methods=['POST'])
def add_stock() -> tuple[Response, int]:
    """Add a new stock to pool"""
    try:
        data = request.get_json()

        raw_symbol = data.get('symbol') if data else None
        symbol, err = validate_symbol(raw_symbol or '')
        if err:
            return error_response(err, 400)

        # Check if stock already exists
        existing = _get_stock_service().get_stock_by_symbol(symbol)
        if existing:
            if not existing.in_pool:
                # 重新激活已软删除的股票
                existing.in_pool = True
                existing.is_active = True
                db_session.commit()
                db_session.refresh(existing)
                return success_response(
                    data=existing.to_dict(),
                    message=f"Stock {symbol} re-activated",
                    status_code=201,
                )
            return error_response(f"Stock {symbol} already exists", 409)

        # Create new stock
        stock = _get_stock_service().add_stock(
            symbol=symbol,
            name=data.get('name'),
            fetch_data=data.get('fetch_data', True)  # Whether to fetch data from Yahoo Finance
        )

        return success_response(
            data=stock.to_dict(),
            message=f"Stock {stock.symbol} added successfully",
            status_code=201,
        )

    except IntegrityError:
        db_session.rollback()
        return error_response('Stock already exists', 409)
    except Exception as e:
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/batch', methods=['POST'])
def add_stocks_batch() -> tuple[Response, int]:
    """批量添加股票到池"""
    try:
        data = request.get_json()
        symbols_raw = data.get('symbols', [])
        fetch_data = data.get('fetch_data', True)

        if not symbols_raw:
            return error_response('请提供至少一个股票代码', 400)

        # 去重 + 规范化 + 校验
        symbols = []
        invalid = []
        seen = set()
        for s in symbols_raw:
            s = s.strip()
            if not s:
                continue
            clean, err = validate_symbol(s)
            if err:
                invalid.append(s)
                continue
            if clean not in seen:
                seen.add(clean)
                symbols.append(clean)
        if not symbols and not invalid:
            return error_response('请提供至少一个有效股票代码', 400)

        results = {'added': [], 'skipped': [], 'failed': []}
        if invalid:
            for inv in invalid:
                results['failed'].append({'symbol': inv, 'error': '无效股票代码格式'})

        svc = _get_stock_service()

        for sym in symbols:
            try:
                existing = svc.get_stock_by_symbol(sym)
                if existing:
                    if not existing.in_pool:
                        # 重新激活已软删除的股票
                        existing.in_pool = True
                        existing.is_active = True
                        db_session.commit()
                        results['added'].append(sym)
                    else:
                        results['skipped'].append(sym)
                    continue
                svc.add_stock(symbol=sym, name=None, fetch_data=fetch_data)
                results['added'].append(sym)
            except Exception as e:
                logger.warning(f"批量添加 {sym} 失败: {e}")
                results['failed'].append({'symbol': sym, 'error': str(e)})

        return success_response(
            status_code=201,
            results=results,
            message=f"添加 {len(results['added'])} 只，跳过 {len(results['skipped'])} 只已有，{len(results['failed'])} 只失败",
        )

    except Exception as e:
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>', methods=['PUT'])
def update_stock(symbol: str) -> tuple[Response, int]:
    """Update stock information"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        data = request.get_json()
        stock = _get_stock_service().update_stock(symbol, data)

        if not stock:
            return error_response('Stock not found', 404)

        return success_response(
            data=stock.to_dict(),
            message=f"Stock {symbol} updated successfully",
        )
    except Exception as e:
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>', methods=['DELETE'])
def remove_stock(symbol: str) -> tuple[Response, int]:
    """Remove stock from pool (soft delete)"""
    try:
        # 对删除操作放宽校验：先尝试标准校验，失败则用原始 symbol 查找
        clean, err = validate_symbol(symbol)
        lookup = clean if not err else symbol.strip()

        success = _get_stock_service().remove_from_pool(lookup)

        if not success:
            return error_response('Stock not found', 404)

        return success_response(message=f"Stock {lookup} removed from pool")
    except Exception as e:
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>/refresh', methods=['POST'])
def refresh_stock_data(symbol: str) -> tuple[Response, int]:
    """Refresh stock data from Yahoo Finance"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().refresh_stock_data(symbol)

        if not stock:
            return error_response('Stock not found', 404)

        # Check if refresh had a warning (data sources unavailable but stock returned)
        warning = getattr(stock, '_refresh_warning', None)
        return success_response(
            data=stock.to_dict(),
            message=f"Stock {symbol} data refreshed" if not warning else warning,
            warning=warning,
        )
    except Exception as e:
        return error_response(str(e), 500)


@bp.route('/<symbol>/financials', methods=['GET'])
def get_financials(symbol: str) -> tuple[Response, int]:
    """Get financial data for a stock"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        financials = [fd.to_dict() for fd in stock.financial_data]

        return success_response(
            data=financials,
            symbol=symbol,
            count=len(financials),
        )
    except Exception as e:
        return error_response(str(e), 500)


# FinancialData 上允许直接编辑的字段白名单（v3 — 对齐 Raw 表）
_EDITABLE_FLOAT_FIELDS = {
    # 利润表
    'revenue', 'cost_of_revenue', 'operating_income', 'net_income',
    'net_income_to_parent', 'adjusted_net_income',
    'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
    # 资产负债表
    'cash_and_equivalents', 'accounts_receivable', 'inventory',
    'investments', 'accounts_payable', 'contract_liability_change_pct',
    'short_term_borrowings', 'long_term_borrowings',
    'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
    # 现金流
    'operating_cash_flow', 'capital_expenditure',
    # 每股数据
    'shares_outstanding', 'nav_per_share', 'dividends_per_share',
}

_EDITABLE_STRING_FIELDS = {'currency', 'report_name'}


@bp.route('/<symbol>/financials/<int:fd_id>', methods=['PATCH'])
def update_financial_data(symbol: str, fd_id: int) -> tuple[Response, int]:
    """
    更新指定 FinancialData 记录的字段值。
    支持两种字段路径：
      - 顶层字段：{"revenue": 1234.5}
      - extended_metrics 嵌套字段：{"extended_metrics.moat_indicators.market_share_pct": 25.5}
    更新后自动重算衍生比率并提交到数据库。
    """
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        fd = db_session.query(FinancialData).filter_by(
            id=fd_id, stock_id=stock.id
        ).first()
        if not fd:
            return error_response('Financial data record not found', 404)

        data = request.get_json(silent=True) or {}
        if not data:
            return error_response('请提供要更新的字段', 400)

        updated_fields = []

        for key, value in data.items():
            if key.startswith('_'):
                continue  # 跳过 _type 等辅助字段
            if key.startswith('extended_metrics.'):
                # 嵌套 JSON 字段更新，支持 dict 和 array index
                # 如 extended_metrics.moat_indicators.market_share_pct
                # 或 extended_metrics.business_segments.0.name
                parts = key.split('.')[1:]  # 去掉 'extended_metrics' 前缀
                ext = fd.extended_metrics_dict or {}
                target = ext
                for part in parts[:-1]:
                    if part.isdigit():
                        idx = int(part)
                        # 确保 target 是 list 且索引有效
                        if isinstance(target, list):
                            while len(target) <= idx:
                                target.append({})
                            target = target[idx]
                        else:
                            break
                    else:
                        if part not in target or (not isinstance(target[part], (dict, list))):
                            target[part] = {}
                        target = target[part]
                # 设置最终值（name 字段保留为字符串，其他转 float）
                final_key = parts[-1]
                if final_key.isdigit() and isinstance(target, list):
                    idx = int(final_key)
                    while len(target) <= idx:
                        target.append(None)
                    target[idx] = value if isinstance(value, str) else (float(value) if value is not None and value != '' else None)
                elif isinstance(target, dict):
                    if data.get('_type') == 'text' or final_key == 'name':
                        target[final_key] = str(value) if value is not None and value != '' else None
                    else:
                        target[final_key] = float(value) if value is not None and value != '' else None
                fd.extended_metrics_dict = ext
                updated_fields.append(key)

            elif key in _EDITABLE_FLOAT_FIELDS:
                # 顶层 Float 字段
                setattr(fd, key, float(value) if value is not None and value != '' else None)
                updated_fields.append(key)
            elif key in _EDITABLE_STRING_FIELDS:
                # 顶层 String 字段（currency, report_name）
                setattr(fd, key, str(value) if value is not None and value != '' else None)
                updated_fields.append(key)
            else:
                logger.warning(f"[财务数据] 忽略不可编辑字段: {key}")

        if not updated_fields:
            return error_response('没有可更新的字段', 400)

        # 如果修改了影响 nav_per_share 的字段，重新计算
        from app.services.kpi_calculator import backfill_nav_per_share
        backfill_nav_per_share(fd)

        db_session.commit()
        db_session.refresh(fd)

        logger.info(f"[财务数据] {symbol} fd_id={fd_id} 更新字段: {updated_fields}")

        return success_response(
            data=fd.to_dict(),
            message=f'已更新 {len(updated_fields)} 个字段',
            updated_fields=updated_fields,
        )

    except ValueError as e:
        db_session.rollback()
        return error_response(f'数值格式错误: {e}', 400)
    except Exception as e:
        db_session.rollback()
        logger.error(f"[财务数据] 更新失败: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/export', methods=['GET'])
def export_stocks() -> Response:
    """导出股票池为 CSV 文件下载"""
    try:
        stocks = _get_stock_service().get_all_stocks(in_pool_only=True)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'symbol', 'name', 'sector', 'industry', 'market_cap',
            'current_price', 'pe_ratio', 'pb_ratio', 'dividend_yield', 'eps',
        ])
        for s in stocks:
            writer.writerow([
                s.symbol, s.name or '', s.sector or '', s.industry or '',
                s.market_cap or '', s.current_price or '',
                s.pe_ratio or '', s.pb_ratio or '',
                s.dividend_yield or '', s.eps or '',
            ])

        today = date.today().strftime('%Y%m%d')
        csv_bytes = output.getvalue().encode('utf-8-sig')
        return Response(
            csv_bytes,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=stock_pool_{today}.csv'},
        )
    except Exception as e:
        logger.error(f"export_stocks 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/<symbol>/financials/export', methods=['GET'])
def export_financials(symbol: str) -> Response:
    """导出某只股票的财务数据为 CSV（含计算 KPI）"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        financials = sorted(
            stock.financial_data,
            key=lambda fd: (fd.fiscal_year, fd.period.value if fd.period else ''),
            reverse=True,
        )

        # Raw fields + computed KPIs
        raw_fields = [
            'fiscal_year', 'period', 'report_date', 'currency',
            'revenue', 'cost_of_revenue', 'operating_income',
            'net_income', 'net_income_to_parent', 'adjusted_net_income',
            'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
            'cash_and_equivalents', 'accounts_receivable', 'inventory',
            'investments', 'accounts_payable', 'contract_liability_change_pct',
            'short_term_borrowings', 'long_term_borrowings',
            'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
            'operating_cash_flow', 'capital_expenditure',
            'shares_outstanding', 'dividends_per_share', 'nav_per_share',
        ]
        kpi_fields = [
            'gross_margin', 'operating_margin', 'net_margin',
            'parent_to_net_ratio', 'adjusted_eps', 'parent_eps',
            'net_cash', 'total_assets_minus_current_liab',
        ]

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(raw_fields + kpi_fields)

        for fd in financials:
            kpis = compute_single_period_kpis(fd)
            row = []
            for f in raw_fields:
                val = getattr(fd, f, None)
                if hasattr(val, 'value'):  # Enum
                    val = val.value
                elif hasattr(val, 'isoformat'):  # Date
                    val = val.isoformat()
                row.append(val if val is not None else '')
            for k in kpi_fields:
                val = kpis.get(k)
                row.append(f'{val:.6f}' if val is not None else '')
            writer.writerow(row)

        csv_bytes = output.getvalue().encode('utf-8-sig')
        return Response(
            csv_bytes,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={symbol}_financials.csv'},
        )
    except Exception as e:
        logger.error(f"export_financials 错误: {e}")
        return error_response(str(e), 500)
