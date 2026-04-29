import csv
import io
import json
import logging
import threading
from datetime import date, datetime, timezone
from typing import Any
from flask import Blueprint, request, Response, current_app
from app.config.database import db_session
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.models.annual_report import AnnualReport
from app.services.stock_service import StockService
from app.services.kpi_calculator import compute_single_period_kpis
from app.utils.response import success_response, error_response, paginated_response
from app.utils.validation import validate_symbol, validate_pagination
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

bp = Blueprint('stocks', __name__)


def _to_date(v):
    """Convert ISO date string to date object, pass through date objects."""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _get_stock_service() -> StockService:
    """延迟创建 StockService，避免模块级绑定 session"""
    return StockService(db_session)


def _decode_upload_bytes(raw: bytes) -> str:
    """Decode uploaded text files from common encodings."""
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def _normalize_csv_key(key: str | None) -> str:
    return (key or '').strip().lower().replace(' ', '_').replace('-', '_')


def _csv_value(row: dict, aliases: tuple[str, ...]) -> str | None:
    normalized = {
        _normalize_csv_key(k): v
        for k, v in row.items()
        if k is not None
    }
    for alias in aliases:
        value = normalized.get(_normalize_csv_key(alias))
        if value is not None and str(value).strip() != '':
            return str(value).strip()
    return None


def _parse_float_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace(',', '')
        .replace('HK$', '')
        .replace('$', '')
        .replace('¥', '')
        .replace('%', '')
    )
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int_value(value: Any) -> int | None:
    number = _parse_float_value(value)
    if number is None:
        return None
    return int(number)


def _parse_bool_value(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {'1', 'true', 'yes', 'y', 'on', '是', '真'}:
        return True
    if text in {'0', 'false', 'no', 'n', 'off', '否', '假'}:
        return False
    return None


_CSV_ALIASES = {
    'symbol': ('symbol', 'ticker', 'code', 'stock_code', '股票代码', '代码'),
    'name': ('name', 'company', 'company_name', 'stock_name', '名称', '股票名称', '公司名称'),
    'exchange': ('exchange', '交易所'),
    'market': ('market', '市场'),
    'currency': ('currency', '币种', '货币'),
    'sector': ('sector', '板块', '行业大类'),
    'industry': ('industry', 'industry_name', '行业'),
    'description': ('description', '简介', '公司简介'),
    'website': ('website', 'url', '官网'),
    'market_cap': ('market_cap', 'marketcap', '市值'),
    'ipo_date': ('ipo_date', 'ipo', '上市日期'),
    'employees': ('employees', 'employee_count', '员工数'),
    'current_price': ('current_price', 'price', 'last_price', '现价', '价格'),
    'volume': ('volume', '成交量'),
    'avg_volume': ('avg_volume', 'average_volume', '平均成交量'),
    'pe_ratio': ('pe_ratio', 'pe', 'p/e', '市盈率'),
    'pb_ratio': ('pb_ratio', 'pb', 'p/b', '市净率'),
    'dividend_yield': ('dividend_yield', 'yield', '股息率'),
    'eps': ('eps', '每股收益'),
    'notes': ('notes', 'note', '备注'),
    'is_active': ('is_active', 'active', '是否活跃'),
}


def _csv_has_symbol_header(fieldnames: list[str | None] | None) -> bool:
    if not fieldnames:
        return False
    normalized = {_normalize_csv_key(f) for f in fieldnames if f}
    return any(_normalize_csv_key(alias) in normalized for alias in _CSV_ALIASES['symbol'])


def _parse_stock_csv(content: str) -> tuple[list[dict], list[dict]]:
    """Parse stock-list CSV content into normalized stock rows."""
    if not content.strip():
        raise ValueError('CSV 文件为空')

    dict_reader = csv.DictReader(io.StringIO(content))
    has_header = _csv_has_symbol_header(dict_reader.fieldnames)

    if has_header:
        raw_rows = list(dict_reader)
        first_row_number = 2
    else:
        raw_rows = []
        for cols in csv.reader(io.StringIO(content)):
            if not any(str(c).strip() for c in cols):
                continue
            raw_rows.append({
                'symbol': cols[0].strip() if len(cols) >= 1 else '',
                'name': cols[1].strip() if len(cols) >= 2 else '',
            })
        first_row_number = 1

    parsed: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()

    for offset, row in enumerate(raw_rows):
        row_number = first_row_number + offset
        raw_symbol = _csv_value(row, _CSV_ALIASES['symbol'])
        if not raw_symbol:
            skipped.append({'row': row_number, 'error': '缺少股票代码'})
            continue

        symbol, err = validate_symbol(raw_symbol)
        if err:
            skipped.append({'row': row_number, 'symbol': raw_symbol, 'error': '无效股票代码格式'})
            continue
        if symbol in seen:
            skipped.append({'row': row_number, 'symbol': symbol, 'error': '重复股票代码'})
            continue
        seen.add(symbol)

        values: dict[str, Any] = {}
        for field, aliases in _CSV_ALIASES.items():
            if field == 'symbol':
                continue
            value = _csv_value(row, aliases)
            if value is not None:
                values[field] = value

        for field in ('market_cap', 'current_price', 'volume', 'avg_volume',
                      'pe_ratio', 'pb_ratio', 'dividend_yield', 'eps'):
            if field in values:
                values[field] = _parse_float_value(values[field])
        if 'employees' in values:
            values['employees'] = _parse_int_value(values['employees'])
        if 'ipo_date' in values:
            values['ipo_date'] = _to_date(values['ipo_date'])
        if 'is_active' in values:
            parsed_bool = _parse_bool_value(values['is_active'])
            values['is_active'] = True if parsed_bool is None else parsed_bool
        if 'market' in values and values['market']:
            values['market'] = str(values['market']).strip().upper()
        if 'currency' in values and values['currency']:
            values['currency'] = str(values['currency']).strip().upper()

        parsed.append({'symbol': symbol, 'values': values})

    return parsed, skipped


def _import_stock_csv(content: str) -> tuple:
    """Import a CSV stock list by adding/updating stocks without deleting existing data."""
    from app.utils.market_utils import (
        detect_market,
        get_currency_for_symbol,
        get_exchange_for_symbol,
    )

    try:
        rows, skipped = _parse_stock_csv(content)
    except ValueError as e:
        return error_response(str(e), 400)

    if not rows:
        return error_response('CSV 未找到可导入的股票代码', 400, data={'skipped': skipped})

    created = 0
    updated = 0
    stock_fields = {c.name for c in Stock.__table__.columns} - {'id', 'symbol', 'created_at', 'updated_at'}

    for item in rows:
        symbol = item['symbol']
        values = {
            k: v for k, v in item['values'].items()
            if k in stock_fields and v is not None
        }

        stock = db_session.query(Stock).filter_by(symbol=symbol).first()
        if stock:
            for field, value in values.items():
                setattr(stock, field, value)
            stock.in_pool = True
            if 'is_active' not in values:
                stock.is_active = True
            updated += 1
            continue

        market = values.get('market') or detect_market(symbol)
        currency = values.get('currency') or get_currency_for_symbol(symbol)
        exchange = values.get('exchange') or get_exchange_for_symbol(symbol)
        stock = Stock(
            symbol=symbol,
            name=values.pop('name', None) or symbol,
            market=market,
            currency=currency,
            exchange=exchange,
            in_pool=True,
            is_active=values.pop('is_active', True),
        )
        for field, value in values.items():
            if field not in {'market', 'currency', 'exchange'}:
                setattr(stock, field, value)
        db_session.add(stock)
        created += 1

    db_session.commit()
    imported = created + updated
    return success_response(
        data={
            'imported': imported,
            'created': created,
            'updated': updated,
            'skipped': skipped,
        },
        message=f'CSV 导入完成：新增 {created} 只，更新 {updated} 只，跳过 {len(skipped)} 行',
    )


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

        # 如果该股票有手动上传的财务数据，自动使用 price_only 模式保护手动数据
        has_manual = db_session.query(FinancialData).join(Stock).filter(
            Stock.symbol == symbol,
            FinancialData.data_source == 'Manual Upload',
        ).first() is not None

        stock = _get_stock_service().refresh_stock_data(symbol, price_only=has_manual)

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


# ── 自动刷新：页面加载时检查过期数据并后台刷新 ──

_auto_refresh_lock = threading.Lock()
_auto_refresh_running = False          # 全局标记，防止并发刷新


@bp.route('/auto_refresh', methods=['POST'])
def auto_refresh_stale():
    """检查池中股票，行情超过 stale_hours 小时未更新的自动后台刷新。
    前端页面加载时调用，非阻塞 — 立即返回，后台线程处理。
    """
    global _auto_refresh_running
    if _auto_refresh_running:
        return success_response(message='刷新已在进行中', data={'triggered': 0})

    stale_hours = float(request.args.get('stale_hours', 4))
    now_utc = datetime.now(timezone.utc)

    stocks = db_session.query(Stock).filter_by(in_pool=True).all()
    stale_symbols = []
    for s in stocks:
        last = None
        if s.extra_data and s.extra_data.get('last_refreshed'):
            try:
                last = datetime.fromisoformat(s.extra_data['last_refreshed'])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if last is None or (now_utc - last).total_seconds() > stale_hours * 3600:
            stale_symbols.append(s.symbol)

    if not stale_symbols:
        return success_response(message='所有股票数据均为最新', data={'triggered': 0})

    # 后台线程逐只刷新，不阻塞请求
    app = current_app._get_current_object()

    def _bg_refresh(symbols):
        global _auto_refresh_running
        _auto_refresh_running = True
        import time
        try:
            with app.app_context():
                svc = StockService(db_session)
                ok = fail = 0
                for sym in symbols:
                    try:
                        has_manual = db_session.query(FinancialData).join(Stock).filter(
                            Stock.symbol == sym,
                            FinancialData.data_source == 'Manual Upload',
                        ).first() is not None
                        svc.refresh_stock_data(sym, price_only=has_manual)
                        ok += 1
                    except Exception as exc:
                        logger.warning(f'Auto-refresh {sym} failed: {exc}')
                        fail += 1
                    time.sleep(1)          # 限流：每只间隔 1s
                logger.info(f'✅ 自动刷新完成：成功 {ok}，失败 {fail}')
        finally:
            _auto_refresh_running = False

    threading.Thread(target=_bg_refresh, args=(stale_symbols,), daemon=True).start()
    return success_response(
        message=f'后台刷新已启动：{len(stale_symbols)} 只过期股票',
        data={'triggered': len(stale_symbols), 'symbols': stale_symbols},
    )


@bp.route('/auto_refresh/status', methods=['GET'])
def auto_refresh_status():
    """查询自动刷新是否仍在运行"""
    return success_response(data={'running': _auto_refresh_running})


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
    """导出股票池为 JSON 文件下载（含财务数据和年报摘要）"""
    try:
        stocks = _get_stock_service().get_all_stocks(in_pool_only=True)

        # Fields to exclude from export
        _internal_fields = {'id', 'stock_id', 'created_at', 'updated_at'}
        _annual_report_fields = {
            'fiscal_year', 'report_type', 'filing_date', 'period_end_date',
            'accession_number', 'filing_url', 'summary', 'key_points',
        }

        def _serialize_stock(s):
            data = s.to_dict()
            for f in _internal_fields:
                data.pop(f, None)

            # financial_data
            fd_list = []
            for fd in sorted(s.financial_data,
                             key=lambda x: (x.fiscal_year, x.period.value if x.period else ''),
                             reverse=True):
                fd_dict = fd.to_dict()  # handles period enum + extended_metrics
                for f in _internal_fields:
                    fd_dict.pop(f, None)
                fd_list.append(fd_dict)
            data['financial_data'] = fd_list

            # annual_reports (metadata only)
            ar_list = []
            for ar in sorted(s.annual_reports,
                             key=lambda x: x.fiscal_year, reverse=True):
                ar_dict = {}
                for f in _annual_report_fields:
                    v = getattr(ar, f, None)
                    if hasattr(v, 'isoformat'):
                        v = v.isoformat()
                    ar_dict[f] = v
                ar_list.append(ar_dict)
            data['annual_reports'] = ar_list

            return data

        export_data = {
            'export_date': date.today().isoformat(),
            'version': 1,
            'stocks': [_serialize_stock(s) for s in stocks],
        }

        today = date.today().strftime('%Y%m%d')
        json_bytes = json.dumps(export_data, ensure_ascii=False, indent=2).encode('utf-8')
        return Response(
            json_bytes,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=stock_pool_{today}.json'},
        )
    except Exception as e:
        logger.error(f"export_stocks 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/import', methods=['POST'])
def import_stocks() -> Response:
    """导入股票池文件。JSON 为替换模式，CSV 为新增/更新模式。"""
    try:
        f = request.files.get('file')
        if not f:
            return error_response('未上传文件', 400)
        filename = (f.filename or '').lower()
        if not (filename.endswith('.json') or filename.endswith('.csv')):
            return error_response('仅支持 JSON 或 CSV 文件', 400)

        raw = f.read()
        content = _decode_upload_bytes(raw)

        if filename.endswith('.csv'):
            return _import_stock_csv(content)

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return error_response(f'JSON 解析失败: {e}', 400)

        stocks_data = data.get('stocks')
        if not isinstance(stocks_data, list):
            return error_response('JSON 格式错误：缺少 stocks 数组', 400)

        import_symbols = set()
        duplicate_symbols = set()
        for sd in stocks_data:
            symbol = str(sd.get('symbol', '')).strip().upper() if isinstance(sd, dict) else ''
            if not symbol:
                return error_response('JSON 格式错误：每只股票必须包含 symbol', 400)
            if symbol in import_symbols:
                duplicate_symbols.add(symbol)
            import_symbols.add(symbol)
        if duplicate_symbols:
            return error_response(f'JSON 中存在重复股票代码: {", ".join(sorted(duplicate_symbols))}', 400)

        # Replace mode: delete all in-pool stocks plus soft-deleted duplicates
        old_stock_ids = {
            s.id for s in db_session.query(Stock.id).filter(Stock.in_pool.is_(True)).all()
        }
        if import_symbols:
            old_stock_ids.update(
                s.id for s in db_session.query(Stock.id).filter(Stock.symbol.in_(import_symbols)).all()
            )
        if old_stock_ids:
            db_session.query(FinancialData).filter(FinancialData.stock_id.in_(old_stock_ids)).delete(synchronize_session=False)
            db_session.query(AnnualReport).filter(AnnualReport.stock_id.in_(old_stock_ids)).delete(synchronize_session=False)
            db_session.query(Stock).filter(Stock.id.in_(old_stock_ids)).delete(synchronize_session=False)
        db_session.flush()

        # Import each stock
        _stock_fields = {c.name for c in Stock.__table__.columns} - {'id', 'created_at', 'updated_at'}
        _fd_fields = {c.name for c in FinancialData.__table__.columns} - {'id', 'stock_id', 'created_at', 'updated_at'}
        _ar_fields = {
            'fiscal_year', 'report_type', 'filing_date', 'period_end_date',
            'accession_number', 'filing_url', 'summary', 'key_points',
        }

        # Date fields that need str -> date conversion
        _date_fields = {'ipo_date', 'report_date', 'filing_date', 'period_end_date'}

        imported_count = 0
        for sd in stocks_data:
            # Create Stock
            stock_kwargs = {k: sd[k] for k in _stock_fields if k in sd}
            for df in _date_fields & stock_kwargs.keys():
                stock_kwargs[df] = _to_date(stock_kwargs[df])
            stock = Stock(**stock_kwargs)
            db_session.add(stock)
            db_session.flush()  # get stock.id

            # Create FinancialData
            for fd_data in sd.get('financial_data', []):
                fd_kwargs = {}
                for k in _fd_fields:
                    if k not in fd_data:
                        continue
                    v = fd_data[k]
                    if k == 'period' and v is not None:
                        v = ReportPeriod(v)
                    elif k == 'extended_metrics' and isinstance(v, dict):
                        v = json.dumps(v, ensure_ascii=False)
                    elif k in _date_fields:
                        v = _to_date(v)
                    fd_kwargs[k] = v
                fd_kwargs['stock_id'] = stock.id
                db_session.add(FinancialData(**fd_kwargs))

            # Create AnnualReport
            for ar_data in sd.get('annual_reports', []):
                ar_kwargs = {k: ar_data[k] for k in _ar_fields if k in ar_data}
                for df in _date_fields & ar_kwargs.keys():
                    ar_kwargs[df] = _to_date(ar_kwargs[df])
                ar_kwargs['stock_id'] = stock.id
                db_session.add(AnnualReport(**ar_kwargs))

            imported_count += 1

        db_session.commit()
        return success_response(
            data={'imported': imported_count},
            message=f'成功导入 {imported_count} 只股票',
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"import_stocks 错误: {e}", exc_info=True)
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


# ── Manual Upload ──────────────────────────────────────────────


@bp.route('/manual-upload/match', methods=['POST'])
def manual_upload_match() -> tuple[Response, int]:
    """接收股票名称列表，返回匹配结果"""
    try:
        data = request.get_json(silent=True) or {}
        names = data.get('names', [])
        if not names:
            return error_response('请提供股票名称列表', 400)

        results = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            # 精确匹配
            stock = db_session.query(Stock).filter(Stock.name == name).first()
            # 模糊匹配（正向：stock.name 包含 name，或反向：name 包含 stock.name）
            if not stock:
                stock = db_session.query(Stock).filter(Stock.name.ilike(f'%{name}%')).first()

            if stock:
                results.append({
                    'name': name,
                    'matched': True,
                    'stock_id': stock.id,
                    'symbol': stock.symbol,
                    'stock_name': stock.name,
                    'market': stock.market,
                })
            else:
                results.append({
                    'name': name,
                    'matched': False,
                    'stock_id': None,
                    'symbol': None,
                    'stock_name': None,
                    'market': None,
                })

        return success_response(data=results, count=len(results))
    except Exception as e:
        logger.error(f"manual_upload_match 错误: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/manual-upload/confirm', methods=['POST'])
def manual_upload_confirm() -> tuple[Response, int]:
    """接收确认后的数据，写入数据库（upsert）"""
    try:
        data = request.get_json(silent=True) or {}
        stocks_data = data.get('stocks', [])
        if not stocks_data:
            return error_response('无数据可导入', 400)

        from app.services.kpi_calculator import backfill_nav_per_share

        imported = 0
        created_stocks = 0
        updated_records = 0
        created_records = 0
        new_stock_symbols = []  # track newly created stocks for auto-refresh

        for item in stocks_data:
            stock_id = item.get('stock_id')

            # 如果未匹配，需要创建新股票
            if not stock_id and item.get('create_new'):
                symbol = item.get('symbol', '').strip()
                name = item.get('name', '').strip()
                market = item.get('market', '').strip()
                if not symbol or not name:
                    continue
                # 检查是否已存在
                existing = db_session.query(Stock).filter(Stock.symbol == symbol.upper()).first()
                if existing:
                    stock_id = existing.id
                else:
                    currency_map = {'CN': 'CNY', 'HK': 'HKD', 'US': 'USD'}
                    exchange_map = {'CN': 'SSE', 'HK': 'HKEX', 'US': 'NASDAQ'}
                    new_stock = Stock(
                        symbol=symbol.upper(),
                        name=name,
                        market=market or 'CN',
                        currency=currency_map.get(market, 'CNY'),
                        exchange=exchange_map.get(market, 'SSE'),
                        in_pool=True,
                        is_active=True,
                    )
                    db_session.add(new_stock)
                    db_session.flush()
                    stock_id = new_stock.id
                    created_stocks += 1
                    new_stock_symbols.append(symbol.upper())

            if not stock_id:
                continue

            # 确保股票在池中（恢复软删除的股票）
            stk = db_session.query(Stock).get(stock_id)
            if stk and not stk.in_pool:
                stk.in_pool = True
                stk.is_active = True

            # 先清掉该股票的所有旧财务数据，再写入新数据
            deleted = db_session.query(FinancialData).filter_by(
                stock_id=stock_id).delete()
            if deleted:
                logger.info(f"清除 stock_id={stock_id} 旧财务数据 {deleted} 条")

            # 写入新的财务记录
            for record in item.get('records', []):
                fiscal_year = record.get('fiscal_year')
                period_str = record.get('period')
                if not fiscal_year or not period_str:
                    continue

                period = ReportPeriod(period_str)

                fd = FinancialData(
                    stock_id=stock_id,
                    fiscal_year=fiscal_year,
                    period=period,
                )
                db_session.add(fd)
                created_records += 1

                # 设置字段
                fields = record.get('fields', {})
                for field_name, value in fields.items():
                    if hasattr(fd, field_name) and field_name in _EDITABLE_FLOAT_FIELDS:
                        setattr(fd, field_name, float(value) if value is not None else None)

                # 元数据
                fd.report_name = record.get('report_name')
                fd.currency = record.get('currency', 'CNY')
                fd.report_date = _to_date(record.get('report_date'))
                fd.data_source = 'Manual Upload'

                backfill_nav_per_share(fd)

            imported += 1

        db_session.commit()

        # Auto-refresh market data only for newly created stocks (price, market cap, etc.)
        # price_only=True ensures manually uploaded financial data is NOT overwritten
        refreshed = 0
        for sym in new_stock_symbols:
            try:
                stock_service = _get_stock_service()
                stock_service.refresh_stock_data(sym, price_only=True)
                refreshed += 1
            except Exception as e:
                logger.warning(f"新股 {sym} 行情刷新失败: {e}")

        return success_response(
            message=f'导入完成：{imported} 只股票，新建 {created_stocks} 只，'
                    f'新增 {created_records} 条记录，更新 {updated_records} 条记录'
                    + (f'，已刷新 {refreshed} 只新股行情' if refreshed else ''),
            data={
                'imported_stocks': imported,
                'created_stocks': created_stocks,
                'created_records': created_records,
                'updated_records': updated_records,
            },
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"manual_upload_confirm 错误: {e}", exc_info=True)
        return error_response(str(e), 500)


# ── AI Backfill ───────────────────────────────────────────────


@bp.route('/<symbol>/ai-backfill', methods=['POST'])
def ai_backfill(symbol: str) -> tuple[Response, int]:
    """触发 AI 补全：搜索互联网补全缺失的财务数据"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        from app.services.ai_backfill_service import run_ai_backfill
        result = run_ai_backfill(symbol)
        return success_response(data=result)
    except ValueError as e:
        return error_response(str(e), 400)
    except RuntimeError as e:
        # RuntimeError from ai_backfill_service: "无法获取网页" etc.
        # These are user-facing errors, return 422 so the message is not masked
        logger.warning(f"[AI Backfill] {symbol}: {e}")
        return error_response(str(e), 422)
    except Exception as e:
        err_str = str(e)
        err_lower = err_str.lower()
        type_name = type(e).__name__

        # Auth errors
        if 'authentication_error' in err_str or 'invalid x-api-key' in err_str:
            return error_response('Claude API Key 无效，请在登录页重新输入正确的 API Key', 401)

        # Rate limit / overload / timeout → return retry_after hint for frontend
        is_retryable = any(k in type_name for k in ('RateLimit', 'Timeout', 'Overloaded', 'APITimeout', 'APIConnection'))
        if not is_retryable:
            is_retryable = any(k in err_lower for k in ('rate', 'timeout', 'overloaded', '529', '503'))

        if is_retryable:
            # Try to extract retry-after from error response headers
            retry_after = 30
            if hasattr(e, 'response') and e.response is not None:
                try:
                    ra = e.response.headers.get('retry-after')
                    if ra:
                        retry_after = int(float(ra)) + 5
                except (ValueError, TypeError, AttributeError):
                    pass

            logger.warning(f"[AI Backfill] {symbol} 可重试错误: {type_name}: {err_str}")
            return error_response(
                f'AI 服务暂时不可用（{type_name}），请稍后重试',
                429,
                retry_after=retry_after,
            )

        logger.error(f"[AI Backfill] {symbol} 失败: {e}", exc_info=True)
        return error_response(err_str, 500)


@bp.route('/<symbol>/ai-backfill/confirm', methods=['POST'])
def ai_backfill_confirm(symbol: str) -> tuple[Response, int]:
    """确认并保存 AI 补全结果"""
    try:
        symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        stock = _get_stock_service().get_stock_by_symbol(symbol)
        if not stock:
            return error_response('Stock not found', 404)

        data = request.get_json(silent=True) or {}
        auto_filled = data.get('auto_filled', {})
        conflict_resolutions = data.get('conflict_resolutions', {})
        ai_values = data.get('ai_values', {})

        from app.services.kpi_calculator import backfill_nav_per_share

        saved_count = 0

        for year_str, fields in auto_filled.items():
            year = int(year_str)
            fd = db_session.query(FinancialData).filter_by(
                stock_id=stock.id,
                fiscal_year=year,
                period=ReportPeriod.ANNUAL,
            ).first()

            if not fd:
                # Create new record
                fd = FinancialData(
                    stock_id=stock.id,
                    fiscal_year=year,
                    period=ReportPeriod.ANNUAL,
                    report_date=date(year, 12, 31),
                    currency=stock.currency or 'USD',
                    data_source='AI Backfill',
                )
                db_session.add(fd)
                db_session.flush()

            # Set auto-filled fields
            ext = fd.extended_metrics_dict or {}
            field_sources = ext.get('field_sources', {})

            for field_name, value in fields.items():
                if hasattr(fd, field_name) and field_name in _EDITABLE_FLOAT_FIELDS:
                    setattr(fd, field_name, float(value) if value is not None else None)
                    field_sources[field_name] = 'AI Backfill'
                    saved_count += 1

            ext['field_sources'] = field_sources
            fd.extended_metrics_dict = ext
            backfill_nav_per_share(fd)

        # Process conflict resolutions
        for year_str, resolutions in conflict_resolutions.items():
            year = int(year_str)
            fd = db_session.query(FinancialData).filter_by(
                stock_id=stock.id,
                fiscal_year=year,
                period=ReportPeriod.ANNUAL,
            ).first()

            if not fd:
                continue

            ext = fd.extended_metrics_dict or {}
            field_sources = ext.get('field_sources', {})
            year_ai_vals = ai_values.get(year_str, {})

            for field_name, choice in resolutions.items():
                if choice == 'ai' and field_name in year_ai_vals:
                    ai_val = year_ai_vals[field_name]
                    if hasattr(fd, field_name) and field_name in _EDITABLE_FLOAT_FIELDS:
                        setattr(fd, field_name, float(ai_val) if ai_val is not None else None)
                        field_sources[field_name] = 'AI Backfill'
                        saved_count += 1
                # choice == 'current' → keep existing, no action needed

            ext['field_sources'] = field_sources
            fd.extended_metrics_dict = ext
            backfill_nav_per_share(fd)

        db_session.commit()

        # Invalidate cache
        from app.services.stock_analysis_service import invalidate_stock_cache
        invalidate_stock_cache()

        logger.info(f"[AI Backfill] {symbol}: saved {saved_count} fields")

        return success_response(
            message=f'AI 补全完成，共保存 {saved_count} 个字段',
            data={'saved_count': saved_count},
        )

    except Exception as e:
        db_session.rollback()
        logger.error(f"[AI Backfill Confirm] {symbol} 失败: {e}", exc_info=True)
        return error_response(str(e), 500)
