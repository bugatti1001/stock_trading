"""
Trade Journal API Routes
交易监督：记录买卖操作，AI 分析违规风险
"""
import csv
import io
import logging
import re
from datetime import date, datetime
from flask import Blueprint, request, Response
from sqlalchemy import desc

from app.config.database import db_session
from app.models.trade_record import TradeRecord
from app.models.stock import Stock
from app.models.financial_data import FinancialData
from app.services import ai_agent_service
from app.utils.response import success_response, error_response, paginated_response
from app.utils.validation import validate_symbol, validate_pagination, validate_required_fields, validate_positive_number

logger = logging.getLogger(__name__)

bp = Blueprint('trades', __name__)


@bp.route('/api/trades', methods=['GET'])
def list_trades() -> tuple:
    """列出历史交易记录"""
    try:
        symbol = request.args.get('symbol', '').upper()
        try:
            limit = int(request.args.get('limit', 50))
        except (ValueError, TypeError):
            limit = 50
        limit = max(1, min(limit, 500))
        page_param = request.args.get('page')

        q = db_session.query(TradeRecord).order_by(desc(TradeRecord.trade_date), desc(TradeRecord.created_at))
        if symbol:
            q = q.filter(TradeRecord.symbol == symbol)

        # Paginated mode when 'page' param is provided
        if page_param is not None:
            page, page_size = validate_pagination()
            total = q.count()
            trades = q.offset((page - 1) * page_size).limit(page_size).all()
            return paginated_response(
                items=[t.to_dict() for t in trades],
                total=total,
                page=page,
                page_size=page_size,
            )

        # Backward-compatible: return limited results without pagination envelope
        trades = q.limit(limit).all()
        return success_response(trades=[t.to_dict() for t in trades])

    except Exception as e:
        logger.error(f"list_trades 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades', methods=['POST'])
def create_trade() -> tuple:
    """
    记录一笔交易并触发 AI 分析
    请求体: {
        symbol, action, price, quantity, trade_date,
        reason_text(可选)
    }
    """
    try:
        data = request.get_json()

        # 必填字段验证
        required = ['symbol', 'action', 'price', 'quantity', 'trade_date']
        field_err = validate_required_fields(data, required)
        if field_err:
            return error_response(field_err)

        # Symbol 验证
        symbol, sym_err = validate_symbol(data['symbol'])
        if sym_err:
            return error_response(sym_err)

        # Action 验证
        action = data['action'].lower()
        if action not in ('buy', 'sell'):
            return error_response('action 必须是 buy 或 sell')

        # Price / Quantity 验证
        price, price_err = validate_positive_number(data['price'], 'price')
        if price_err:
            return error_response(price_err)

        quantity, qty_err = validate_positive_number(data['quantity'], 'quantity')
        if qty_err:
            return error_response(qty_err)

        # trade_date 格式验证
        try:
            trade_date = date.fromisoformat(data['trade_date'])
        except ValueError:
            return error_response('trade_date 格式应为 YYYY-MM-DD')

        # 查找关联股票数据
        stock = db_session.query(Stock).filter_by(symbol=symbol).first()
        stock_data = {}
        financial_data = {}
        if stock:
            stock_data = {
                'current_price': stock.current_price,
                'pe_ratio': stock.pe_ratio,
                'pb_ratio': stock.pb_ratio,
                'market_cap': stock.market_cap,
            }
            fd = (db_session.query(FinancialData)
                  .filter_by(stock_id=stock.id)
                  .order_by(FinancialData.fiscal_year.desc())
                  .first())
            if fd:
                from app.services.kpi_calculator import compute_single_period_kpis
                kpis = compute_single_period_kpis(fd)
                financial_data = {
                    'revenue': fd.revenue,
                    'net_income': fd.net_income,
                    'net_income_to_parent': fd.net_income_to_parent,
                    'operating_income': fd.operating_income,
                    'total_assets': fd.total_assets,
                    'total_equity': fd.total_equity,
                    'operating_cash_flow': fd.operating_cash_flow,
                    'currency': fd.currency,
                    # 计算的 KPI
                    'gross_margin': kpis.get('gross_margin'),
                    'operating_margin': kpis.get('operating_margin'),
                    'net_margin': kpis.get('net_margin'),
                    'net_cash': kpis.get('net_cash'),
                }

        # 创建交易记录
        trade = TradeRecord(
            stock_id=stock.id if stock else None,
            symbol=symbol,
            action=action,
            price=price,
            quantity=quantity,
            trade_date=trade_date,
            reason_text=data.get('reason_text', ''),
            pe_at_trade=stock_data.get('pe_ratio'),
            pb_at_trade=stock_data.get('pb_ratio'),
            market_cap_at_trade=stock_data.get('market_cap'),
        )
        db_session.add(trade)
        db_session.flush()  # 获取 id，先不 commit

        # AI 分析（同步，会稍慢，可接受）
        ai_result = ai_agent_service.analyze_trade({
            'symbol': symbol,
            'action': action,
            'price': price,
            'quantity': quantity,
            'trade_date': data['trade_date'],
            'reason_text': data.get('reason_text', ''),
            'stock_data': stock_data,
            'financial_data': financial_data,
        })

        if 'error' not in ai_result:
            trade.violations = ai_result.get('violations', [])
            trade.risk_score = ai_result.get('risk_score', 50)
            trade.ai_analysis = ai_result.get('analysis', '')
            trade.suggestions = ai_result.get('suggestions', '')

        db_session.commit()

        # 清除持仓缓存
        from app.services.portfolio_service import invalidate_portfolio_cache
        invalidate_portfolio_cache()

        return success_response(trade=trade.to_dict(), status_code=201)

    except Exception as e:
        db_session.rollback()
        logger.error(f"create_trade 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades/<int:trade_id>', methods=['GET'])
def get_trade(trade_id: int) -> tuple:
    """获取单笔交易详情"""
    trade = db_session.query(TradeRecord).get(trade_id)
    if not trade:
        return error_response('记录不存在', status_code=404)
    return success_response(trade=trade.to_dict())


@bp.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id: int) -> tuple:
    """删除交易记录"""
    trade = db_session.query(TradeRecord).get(trade_id)
    if not trade:
        return error_response('记录不存在', status_code=404)
    db_session.delete(trade)
    db_session.commit()

    from app.services.portfolio_service import invalidate_portfolio_cache
    invalidate_portfolio_cache()

    return success_response()


@bp.route('/api/trades/stats', methods=['GET'])
def trade_stats() -> tuple:
    """交易习惯统计：常见违规 Top5、平均风险分等"""
    try:
        from sqlalchemy import func

        # 用聚合查询代替全表加载
        total = db_session.query(func.count(TradeRecord.id)).scalar() or 0
        if not total:
            return success_response(stats={
                'total': 0, 'avg_risk_score': 0, 'top_violations': []
            })

        avg_score_result = db_session.query(
            func.avg(TradeRecord.risk_score)
        ).filter(TradeRecord.risk_score.isnot(None)).scalar()
        avg_score = float(avg_score_result) if avg_score_result else 0

        high_risk = db_session.query(
            func.count(TradeRecord.id)
        ).filter(TradeRecord.risk_score >= 70).scalar() or 0

        # violations 是 JSON 字段，仍需加载但限制条数（只需有 violations 的记录）
        trades_with_violations = db_session.query(TradeRecord.violations).filter(
            TradeRecord.violations.isnot(None)
        ).limit(5000).all()

        violation_count = {}
        for (violations,) in trades_with_violations:
            for v in (violations or []):
                violation_count[v] = violation_count.get(v, 0) + 1

        top_violations = sorted(violation_count.items(), key=lambda x: -x[1])[:5]

        return success_response(stats={
            'total': total,
            'avg_risk_score': round(avg_score, 1),
            'high_risk_count': high_risk,
            'high_risk_pct': round(high_risk / total * 100, 1) if total else 0,
            'top_violations': [{'name': v, 'count': c} for v, c in top_violations],
        })
    except Exception as e:
        logger.error(f"trade_stats 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades/portfolio', methods=['GET'])
def portfolio() -> tuple:
    """获取当前持仓（从交易日志聚合计算）"""
    try:
        from app.services.portfolio_service import compute_holdings
        holdings = compute_holdings()
        return success_response(holdings=holdings)
    except Exception as e:
        logger.error(f"portfolio 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades/total_capital', methods=['GET'])
def get_total_capital() -> tuple:
    """获取用户设定的初始资金和当前现金余额"""
    try:
        from app.models.user_setting import UserSetting
        from app.services.portfolio_service import compute_user_cash
        row = db_session.query(UserSetting).filter_by(key='total_capital').first()
        value = float(row.value) if row else 0
        user_cash = compute_user_cash() if value > 0 else 0
        return success_response(total_capital=value, user_cash=user_cash)
    except Exception as e:
        logger.error(f"get_total_capital 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades/total_capital', methods=['PUT'])
def set_total_capital() -> tuple:
    """设置用户现金余额，反推初始投入资金"""
    try:
        data = request.get_json()
        if not data:
            return error_response('缺少请求数据')

        if 'cash' in data:
            # 用户输入现金，反推 total_capital = cash + 总买入 - 总卖出
            cash = float(data['cash'])
            from app.models.trade_record import TradeRecord
            trades = db_session.query(TradeRecord).all()
            total_bought = sum(t.quantity * t.price for t in trades if t.action == 'buy')
            total_sold = sum(t.quantity * t.price for t in trades if t.action == 'sell')
            val = cash + total_bought - total_sold
        elif 'total_capital' in data:
            val = float(data['total_capital'])
        else:
            return error_response('缺少 cash 或 total_capital 字段')

        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='total_capital').first()
        if row:
            row.value = str(val)
        else:
            db_session.add(UserSetting(key='total_capital', value=str(val)))
        db_session.commit()
        return success_response(total_capital=val)
    except Exception as e:
        db_session.rollback()
        logger.error(f"set_total_capital 错误: {e}")
        return error_response(str(e), status_code=500)


@bp.route('/api/trades/import_csv', methods=['POST'])
def import_csv() -> tuple:
    """
    导入交易记录 CSV，自动识别两种格式：

    1. Robinhood 交易历史 CSV:
       Activity Date, Process Date, Settle Date, Instrument, Description,
       Trans Code, Quantity, Price, Amount

    2. 持仓快照 CSV (如从 Robinhood 导出的当前持仓):
       ,average price,share number
       NIO,3.85,4667.77196
       第一列为股票代码，无列头或列头为空
    """
    try:
        if 'file' not in request.files:
            return error_response('请上传 CSV 文件')

        file = request.files['file']
        filename = file.filename or ''
        if not filename.lower().endswith('.csv'):
            return error_response('请上传 .csv 格式文件')

        # 读取 CSV 内容
        content = file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))

        if reader.fieldnames is None:
            return error_response('CSV 文件为空或格式不正确')

        # 自动检测格式：持仓快照 vs Robinhood 交易历史
        normalized_fields = {f.strip().lower() for f in reader.fieldnames if f}
        is_position_snapshot = (
            'average price' in normalized_fields and 'share number' in normalized_fields
        )

        if is_position_snapshot:
            return _import_position_snapshot(reader, filename)
        else:
            return _import_robinhood_trades(reader)

    except Exception as e:
        db_session.rollback()
        logger.error(f"import_csv 错误: {e}")
        return error_response(str(e), status_code=500)


def _extract_date_from_filename(filename: str) -> date:
    """尝试从文件名提取日期，如 '2026-03-12 stock position.csv' → 2026-03-12"""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return date.today()


def _import_position_snapshot(reader, filename: str) -> tuple:
    """导入持仓快照 CSV：每行视为一笔 buy 交易"""
    trade_date = _extract_date_from_filename(filename)

    imported = 0
    skipped = 0
    errors_list = []

    for row_num, row in enumerate(reader, start=2):
        row = {k.strip(): v.strip() if v else '' for k, v in row.items()}

        # 第一列（列头为空或空字符串）存储 symbol
        symbol = ''
        for key in row:
            if key == '' or key is None:
                symbol = row[key].strip().upper()
                break
        # 也尝试常见列名
        if not symbol:
            symbol = row.get('symbol', row.get('Symbol', row.get('SYMBOL', ''))).strip().upper()
        if not symbol:
            skipped += 1
            errors_list.append(f"行{row_num}: 缺少股票代码")
            continue

        # 提取价格和数量
        try:
            price_str = row.get('average price', row.get('Average Price', '0'))
            price_str = price_str.replace('$', '').replace(',', '').strip()
            price = float(price_str) if price_str else 0

            qty_str = row.get('share number', row.get('Share Number', '0'))
            qty_str = qty_str.replace(',', '').strip()
            quantity = abs(float(qty_str)) if qty_str else 0

            if price <= 0 or quantity <= 0:
                skipped += 1
                continue
        except (ValueError, TypeError):
            skipped += 1
            errors_list.append(f"行{row_num}: 价格或数量格式错误")
            continue

        # 查重
        existing = db_session.query(TradeRecord).filter_by(
            symbol=symbol,
            trade_date=trade_date,
            action='buy',
            price=price,
            quantity=quantity,
        ).first()
        if existing:
            skipped += 1
            continue

        stock = db_session.query(Stock).filter_by(symbol=symbol).first()

        trade = TradeRecord(
            stock_id=stock.id if stock else None,
            symbol=symbol,
            action='buy',
            price=price,
            quantity=quantity,
            trade_date=trade_date,
            reason_text=f'持仓快照导入 ({trade_date.isoformat()})',
        )
        db_session.add(trade)
        imported += 1

    db_session.commit()

    from app.services.portfolio_service import invalidate_portfolio_cache
    invalidate_portfolio_cache()

    return success_response(
        imported=imported,
        skipped=skipped,
        errors=errors_list[:20],
        format='position_snapshot',
        trade_date=trade_date.isoformat(),
        status_code=201,
    )


def _import_robinhood_trades(reader) -> tuple:
    """导入 Robinhood 交易历史 CSV"""
    ACTION_MAP = {
        'Buy': 'buy', 'BUY': 'buy',
        'Sell': 'sell', 'SELL': 'sell',
    }
    DATE_FORMATS = ['%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y', '%m/%d/%y']

    imported = 0
    skipped = 0
    errors_list = []

    for row_num, row in enumerate(reader, start=2):
        row = {k.strip(): v.strip() if v else '' for k, v in row.items()}

        trans_code = row.get('Trans Code', row.get('trans_code', row.get('TransCode', '')))
        action = ACTION_MAP.get(trans_code)
        if not action:
            skipped += 1
            continue

        symbol = row.get('Instrument', row.get('instrument', row.get('Symbol', ''))).strip().upper()
        if not symbol:
            skipped += 1
            errors_list.append(f"行{row_num}: 缺少股票代码")
            continue

        try:
            price_str = row.get('Price', row.get('price', '0'))
            price_str = price_str.replace('$', '').replace(',', '').strip()
            price = float(price_str) if price_str else 0

            qty_str = row.get('Quantity', row.get('quantity', '0'))
            qty_str = qty_str.replace(',', '').strip()
            quantity = abs(float(qty_str)) if qty_str else 0

            if price <= 0 or quantity <= 0:
                skipped += 1
                continue
        except (ValueError, TypeError):
            skipped += 1
            errors_list.append(f"行{row_num}: 价格或数量格式错误")
            continue

        date_str = row.get('Activity Date', row.get('activity_date', row.get('Date', ''))).strip()
        trade_date = None
        for fmt in DATE_FORMATS:
            try:
                trade_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        if not trade_date:
            skipped += 1
            errors_list.append(f"行{row_num}: 日期格式无法解析 '{date_str}'")
            continue

        existing = db_session.query(TradeRecord).filter_by(
            symbol=symbol,
            trade_date=trade_date,
            action=action,
            price=price,
            quantity=quantity,
        ).first()
        if existing:
            skipped += 1
            continue

        stock = db_session.query(Stock).filter_by(symbol=symbol).first()

        trade = TradeRecord(
            stock_id=stock.id if stock else None,
            symbol=symbol,
            action=action,
            price=price,
            quantity=quantity,
            trade_date=trade_date,
            reason_text='Robinhood CSV 导入',
        )
        db_session.add(trade)
        imported += 1

    db_session.commit()

    from app.services.portfolio_service import invalidate_portfolio_cache
    invalidate_portfolio_cache()

    return success_response(
        imported=imported,
        skipped=skipped,
        errors=errors_list[:20],
        format='robinhood',
        status_code=201,
    )


@bp.route('/api/trades/export', methods=['GET'])
def export_trades() -> Response:
    """导出交易记录为 CSV 文件下载"""
    try:
        trades = db_session.query(TradeRecord).order_by(desc(TradeRecord.trade_date)).limit(10000).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'symbol', 'action', 'price', 'quantity', 'trade_date',
            'reason_text', 'risk_score', 'ai_analysis',
            'violations', 'suggestions',
            'pe_at_trade', 'pb_at_trade', 'market_cap_at_trade',
        ])
        for t in trades:
            writer.writerow([
                t.symbol, t.action, t.price, t.quantity,
                t.trade_date.isoformat() if t.trade_date else '',
                t.reason_text or '',
                t.risk_score if t.risk_score is not None else '',
                t.ai_analysis or '',
                '; '.join(t.violations) if t.violations else '',
                t.suggestions or '',
                t.pe_at_trade or '',
                t.pb_at_trade or '',
                t.market_cap_at_trade or '',
            ])

        today = date.today().strftime('%Y%m%d')
        csv_bytes = output.getvalue().encode('utf-8-sig')
        return Response(
            csv_bytes,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=trades_{today}.csv'},
        )
    except Exception as e:
        logger.error(f"export_trades 错误: {e}")
        return error_response(str(e), status_code=500)
