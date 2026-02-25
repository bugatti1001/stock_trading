"""
Trade Journal API Routes
交易监督：记录买卖操作，AI 分析违规风险
"""
import csv
import io
import logging
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
    return success_response()


@bp.route('/api/trades/stats', methods=['GET'])
def trade_stats() -> tuple:
    """交易习惯统计：常见违规 Top5、平均风险分等"""
    try:
        trades = db_session.query(TradeRecord).all()
        if not trades:
            return success_response(stats={
                'total': 0, 'avg_risk_score': 0, 'top_violations': []
            })

        total = len(trades)
        scores = [t.risk_score for t in trades if t.risk_score is not None]
        avg_score = sum(scores) / len(scores) if scores else 0

        # 统计违规频率
        violation_count = {}
        for t in trades:
            for v in (t.violations or []):
                violation_count[v] = violation_count.get(v, 0) + 1

        top_violations = sorted(violation_count.items(), key=lambda x: -x[1])[:5]

        # 高风险交易比例
        high_risk = sum(1 for s in scores if s >= 70)

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


@bp.route('/api/trades/export', methods=['GET'])
def export_trades() -> Response:
    """导出交易记录为 CSV 文件下载"""
    try:
        trades = db_session.query(TradeRecord).order_by(desc(TradeRecord.trade_date)).all()

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
