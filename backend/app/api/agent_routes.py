"""
AI Agent API Routes
提供 AI 投资讨论的 REST + SSE 端点
"""
import logging
from flask import Blueprint, request, Response, stream_with_context

from app.services import ai_agent_service
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

bp = Blueprint('agent', __name__)


@bp.route('/api/agent/conversations', methods=['GET'])
def list_conversations():
    """列出历史对话"""
    try:
        convs = ai_agent_service.get_conversations()
        return success_response(conversations=[c.to_dict() for c in convs])
    except Exception as e:
        logger.error(f"list_conversations 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations', methods=['POST'])
def create_conversation():
    """新建对话"""
    try:
        data = request.get_json() or {}
        title = data.get('title', '新对话')
        context_mode = data.get('context_mode', 'global')
        stock_id = data.get('stock_id')
        include_principles = bool(data.get('include_principles', False))

        conv = ai_agent_service.create_conversation(
            title, context_mode, stock_id,
            include_principles=include_principles,
        )
        return success_response(conversation=conv.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"create_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>', methods=['PATCH'])
def rename_conversation(conv_id: int):
    """重命名对话"""
    try:
        data = request.get_json() or {}
        new_title = data.get('title', '').strip()
        if not new_title:
            return error_response('标题不能为空', 400)

        conv = ai_agent_service.rename_conversation(conv_id, new_title)
        if not conv:
            return error_response('对话不存在', 404)
        return success_response(conversation=conv.to_dict())
    except Exception as e:
        logger.error(f"rename_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>', methods=['DELETE'])
def delete_conversation(conv_id: int):
    """删除对话"""
    try:
        ok = ai_agent_service.delete_conversation(conv_id)
        if not ok:
            return error_response('对话不存在', 404)
        return success_response()
    except Exception as e:
        logger.error(f"delete_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>/messages', methods=['GET'])
def get_messages(conv_id: int):
    """获取对话消息列表"""
    try:
        msgs = ai_agent_service.get_messages(conv_id)
        return success_response(messages=[m.to_dict() for m in msgs])
    except Exception as e:
        logger.error(f"get_messages 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>/chat', methods=['POST'])
def chat(conv_id: int):
    """
    流式聊天端点（SSE）
    请求体: { "message": "用户消息" }
    响应: text/event-stream
    """
    data = request.get_json()
    if not data or not data.get('message'):
        return error_response('缺少 message 字段', 400)

    user_message = data['message'].strip()
    if not user_message:
        return error_response('消息不能为空', 400)

    def generate():
        yield from ai_agent_service.chat_stream(conv_id, user_message)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@bp.route('/api/agent/conversations/<int:conv_id>/extract_principles', methods=['POST'])
def extract_principles(conv_id: int):
    """从指定对话中提炼用户投资原则"""
    try:
        result = ai_agent_service.extract_principles(conv_id)
        if 'error' in result:
            return error_response(result['error'], 500)
        return success_response(
            principles=result['principles'],
            source_conv_id=result.get('source_conv_id')
        )
    except Exception as e:
        logger.error(f"extract_principles 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/dashboard_insight', methods=['GET'])
def dashboard_insight():
    """生成 Dashboard AI 选股建议（非流式）"""
    try:
        insight = ai_agent_service.generate_dashboard_insight()
        return success_response(insight=insight)
    except Exception as e:
        logger.error(f"dashboard_insight 错误: {e}")
        return error_response(str(e), 500)


# ============================================================
# 每日评分推荐
# ============================================================

@bp.route('/api/agent/daily_scores', methods=['GET'])
def daily_scores():
    """获取股票池评分列表（评分决策不依赖估值，估值仅供展示参考）"""
    try:
        from app.services.stock_scorer import score_all_stocks
        scores = score_all_stocks()
        # 估值数据仅供前端展示参考，不参与交易决策
        try:
            from app.services.valuation_service import valuate_all_stocks, get_valuation_params
            params = get_valuation_params()
            vals = {v['symbol']: v for v in valuate_all_stocks(params)}
            for s in scores:
                val = vals.get(s['symbol'])
                if val:
                    s['intrinsic_value'] = val['composite'].get('intrinsic_value')
                    s['margin_of_safety'] = val['margin_of_safety']
                    s['tags'] = val.get('tags', [])
                else:
                    s['intrinsic_value'] = None
                    s['margin_of_safety'] = {'pct': None, 'signal': 'unknown',
                                              'signal_label': '无法估值', 'signal_emoji': '⚫'}
                    s['tags'] = []
        except Exception as e:
            logger.debug(f"估值附加失败(不影响评分): {e}")
        return success_response(scores=scores)
    except Exception as e:
        logger.error(f"daily_scores 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/daily_scores/ai_reasoning', methods=['POST'])
def daily_scores_ai_reasoning():
    """基于评分结果生成 AI 补充分析"""
    try:
        from app.services.stock_scorer import score_all_stocks, generate_ai_recommendations
        scores = score_all_stocks()
        reasoning = generate_ai_recommendations(scores)
        return success_response(reasoning=reasoning)
    except Exception as e:
        logger.error(f"daily_scores_ai_reasoning 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/daily_scores/ai_trades', methods=['POST'])
def daily_scores_ai_trades():
    """AI 根据可用现金、投资原则和新闻给出具体买卖数量建议（不依赖估值模型）"""
    try:
        # 前置检查1：今天是否已执行过 AI 交易
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from datetime import date as date_type
        today = date_type.today()
        today_records = db_session.query(AiTradeRecord).filter(
            AiTradeRecord.trade_date == today,
            ~AiTradeRecord.reason.like('%初始化%'),
            ~AiTradeRecord.reason.like('%重置%'),
        ).all()
        if today_records:
            # 返回今天的交易结果（缓存），标记已执行
            trades = {
                r.symbol: {'action': r.action, 'shares': r.shares, 'reason': r.reason or ''}
                for r in today_records if r.action in ('buy', 'sell')
            }
            hold_record = any(r.action == 'hold' for r in today_records)
            msg = '今日AI已决定不交易' if hold_record and not trades else '今日AI交易已执行'
            return success_response(trades=trades, already_executed=True, message=msg)

        # 前置检查2：当日新闻分析是否过半完成
        from app.services.news_analysis_service import _get_existing_today_symbols
        from app.models.stock import Stock
        pool_symbols = {s.symbol for s in db_session.query(Stock.symbol).filter_by(in_pool=True).all()}
        analyzed_symbols = _get_existing_today_symbols()
        analyzed_count = len(analyzed_symbols)
        total_count = len(pool_symbols)
        if total_count > 0 and analyzed_count < total_count / 2:
            return error_response(
                f'请先完成当日新闻分析再执行AI交易（需过半）。'
                f'已分析 {analyzed_count}/{total_count} 只',
                400,
            )

        from app.services.stock_scorer import score_all_stocks, generate_ai_trades
        scores = score_all_stocks()
        trades = generate_ai_trades(scores)
        return success_response(trades=trades)
    except Exception as e:
        logger.error(f"daily_scores_ai_trades 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_holdings', methods=['GET'])
def ai_holdings():
    """获取 AI 模拟持仓"""
    try:
        from app.services.stock_scorer import compute_ai_holdings, compute_ai_cash
        from app.config.database import db_session
        from app.models.stock import Stock
        holdings = compute_ai_holdings()
        ai_cash = compute_ai_cash()
        # 附加当前价格和市值
        result = {}
        for symbol, h in holdings.items():
            stock = db_session.query(Stock).filter_by(symbol=symbol).first()
            price = stock.current_price if stock else 0
            result[symbol] = {
                'shares': h['shares'],
                'avg_cost': h['avg_cost'],
                'current_price': price,
                'market_value': round(h['shares'] * price, 2) if price else 0,
            }
        return success_response(holdings=result, ai_cash=ai_cash)
    except Exception as e:
        logger.error(f"ai_holdings 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_holdings/reset', methods=['POST'])
def reset_ai_holdings():
    """重置 AI 模拟账户：清空交易记录，用用户当前持仓重新初始化。
    关键：保存 ai_starting_cash = user_cash + AI买入总额，
    使重置瞬间 AI 总资产 == 用户总资产（含用户已实现盈亏）。
    """
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from app.models.user_setting import UserSetting
        from app.services.portfolio_service import compute_holdings, compute_user_cash
        from datetime import date, timedelta

        # 清空所有 AI 交易记录
        db_session.query(AiTradeRecord).delete()
        db_session.commit()

        # 获取用户当前真实现金余额
        user_cash = compute_user_cash()

        # 用用户当前持仓重新初始化（昨天日期）
        yesterday = date.today() - timedelta(days=1)
        user_holdings = compute_holdings()
        count = 0
        ai_buy_total = 0.0
        for h in user_holdings:
            shares = h.get('net_shares', 0)
            price = h.get('avg_cost', 0) or h.get('current_price', 0)
            if shares <= 0 or price <= 0:
                continue
            record = AiTradeRecord(
                symbol=h['symbol'],
                action='buy',
                shares=shares,
                price=price,
                trade_date=yesterday,
                reason='重置：与用户持仓同步',
            )
            db_session.add(record)
            ai_buy_total += shares * price
            count += 1

        # 保存 ai_starting_cash，使 AI 现金起点 = 用户真实现金 + AI买入成本
        # 这样 compute_ai_cash() 算出来的 AI 现金 == 用户现金
        ai_starting_cash = round(user_cash + ai_buy_total, 2)
        row = db_session.query(UserSetting).filter_by(key='ai_starting_cash').first()
        if row:
            row.value = str(ai_starting_cash)
        else:
            db_session.add(UserSetting(key='ai_starting_cash', value=str(ai_starting_cash)))

        db_session.commit()
        logger.info(f"AI 重置: user_cash={user_cash:.2f}, ai_buy_total={ai_buy_total:.2f}, "
                     f"ai_starting_cash={ai_starting_cash:.2f}")
        return success_response(message=f'AI 账户已重置，同步 {count} 只持仓')
    except Exception as e:
        db_session.rollback()
        logger.error(f"reset_ai_holdings 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_trade_history', methods=['GET'])
def ai_trade_history():
    """获取 AI 交易历史记录"""
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from sqlalchemy import desc
        records = db_session.query(AiTradeRecord).filter(
            AiTradeRecord.trader == 'scorer',
            ~AiTradeRecord.reason.like('%初始化%'),
            ~AiTradeRecord.reason.like('%重置%'),
        ).order_by(desc(AiTradeRecord.trade_date), desc(AiTradeRecord.id)).all()
        return success_response(trades=[{
            'id': r.id,
            'symbol': r.symbol,
            'action': r.action,
            'shares': r.shares,
            'price': r.price,
            'trade_date': r.trade_date.isoformat() if r.trade_date else None,
            'reason': r.reason,
            'amount': round(r.shares * r.price, 2),
        } for r in records])
    except Exception as e:
        logger.error(f"ai_trade_history 错误: {e}")
        return error_response(str(e), 500)




# ============================================================
# TradingAgents 多Agent交易
# ============================================================

@bp.route('/api/agent/ta_trades', methods=['POST'])
def ta_trades():
    """TradingAgents 多Agent模拟交易"""
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from datetime import date as date_type
        today = date_type.today()

        # 前置检查1：今天是否已执行过 TA 交易
        today_records = db_session.query(AiTradeRecord).filter(
            AiTradeRecord.trade_date == today,
            AiTradeRecord.trader == 'tradingagents',
            ~AiTradeRecord.reason.like('%初始化%'),
            ~AiTradeRecord.reason.like('%重置%'),
        ).all()
        if today_records:
            trades = {
                r.symbol: {'action': r.action, 'shares': r.shares, 'reason': r.reason or ''}
                for r in today_records if r.action in ('buy', 'sell')
            }
            hold_record = any(r.action == 'hold' for r in today_records)
            msg = '今日TA已决定不交易' if hold_record and not trades else '今日TA交易已执行'
            return success_response(trades=trades, already_executed=True, message=msg)

        # 前置检查2：当日新闻分析是否过半完成
        from app.services.news_analysis_service import _get_existing_today_symbols
        from app.models.stock import Stock
        pool_symbols = {s.symbol for s in db_session.query(Stock.symbol).filter_by(in_pool=True).all()}
        analyzed_symbols = _get_existing_today_symbols()
        analyzed_count = len(analyzed_symbols)
        total_count = len(pool_symbols)
        if total_count > 0 and analyzed_count < total_count / 2:
            return error_response(
                f'请先完成当日新闻分析再执行TA交易（需过半）。'
                f'已分析 {analyzed_count}/{total_count} 只',
                400,
            )

        from app.services.tradingagents_service import generate_ta_trades
        trades = generate_ta_trades()
        return success_response(trades=trades)
    except Exception as e:
        logger.error(f"ta_trades 错误: {e}", exc_info=True)
        return error_response(f'TA交易失败: {str(e)[:200]}', 422)


@bp.route('/api/agent/ta_progress', methods=['GET'])
def ta_progress():
    """获取 TradingAgents 分析进度（前端轮询）"""
    try:
        from app.services.tradingagents_service import get_ta_progress
        return success_response(**get_ta_progress())
    except Exception as e:
        return error_response(str(e), 500)


@bp.route('/api/agent/ta_holdings', methods=['GET'])
def ta_holdings():
    """获取 TradingAgents 模拟持仓"""
    try:
        from app.services.tradingagents_service import compute_ta_holdings, compute_ta_cash
        from app.config.database import db_session
        from app.models.stock import Stock
        holdings = compute_ta_holdings()
        ta_cash = compute_ta_cash()
        result = {}
        for symbol, h in holdings.items():
            stock = db_session.query(Stock).filter_by(symbol=symbol).first()
            price = stock.current_price if stock else 0
            result[symbol] = {
                'shares': h['shares'],
                'avg_cost': h['avg_cost'],
                'current_price': price,
                'market_value': round(h['shares'] * price, 2) if price else 0,
            }
        return success_response(holdings=result, ta_cash=ta_cash)
    except Exception as e:
        logger.error(f"ta_holdings 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ta_holdings/reset', methods=['POST'])
def reset_ta_holdings():
    """重置 TradingAgents 模拟账户"""
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from app.models.user_setting import UserSetting
        from app.services.portfolio_service import compute_holdings, compute_user_cash
        from datetime import date, timedelta

        # 清空所有 TA 交易记录
        db_session.query(AiTradeRecord).filter(
            AiTradeRecord.trader == 'tradingagents'
        ).delete()
        db_session.commit()

        # 获取用户当前真实现金余额
        user_cash = compute_user_cash()

        # 用用户当前持仓重新初始化（昨天日期）
        yesterday = date.today() - timedelta(days=1)
        user_holdings = compute_holdings()
        count = 0
        ta_buy_total = 0.0
        for h in user_holdings:
            shares = h.get('net_shares', 0)
            price = h.get('avg_cost', 0) or h.get('current_price', 0)
            if shares <= 0 or price <= 0:
                continue
            record = AiTradeRecord(
                trader='tradingagents',
                symbol=h['symbol'],
                action='buy',
                shares=shares,
                price=price,
                trade_date=yesterday,
                reason='重置：与用户持仓同步',
            )
            db_session.add(record)
            ta_buy_total += shares * price
            count += 1

        ta_starting_cash = round(user_cash + ta_buy_total, 2)
        row = db_session.query(UserSetting).filter_by(key='ta_starting_cash').first()
        if row:
            row.value = str(ta_starting_cash)
        else:
            db_session.add(UserSetting(key='ta_starting_cash', value=str(ta_starting_cash)))

        db_session.commit()
        logger.info(f"TA 重置: user_cash={user_cash:.2f}, ta_buy_total={ta_buy_total:.2f}, "
                     f"ta_starting_cash={ta_starting_cash:.2f}")
        return success_response(message=f'TA 账户已重置，同步 {count} 只持仓')
    except Exception as e:
        db_session.rollback()
        logger.error(f"reset_ta_holdings 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ta_trade_history', methods=['GET'])
def ta_trade_history():
    """获取 TradingAgents 交易历史记录"""
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from sqlalchemy import desc
        records = db_session.query(AiTradeRecord).filter(
            AiTradeRecord.trader == 'tradingagents',
            ~AiTradeRecord.reason.like('%初始化%'),
            ~AiTradeRecord.reason.like('%重置%'),
        ).order_by(desc(AiTradeRecord.trade_date), desc(AiTradeRecord.id)).all()
        return success_response(trades=[{
            'id': r.id,
            'symbol': r.symbol,
            'action': r.action,
            'shares': r.shares,
            'price': r.price,
            'trade_date': r.trade_date.isoformat() if r.trade_date else None,
            'reason': r.reason,
            'amount': round(r.shares * r.price, 2),
        } for r in records])
    except Exception as e:
        logger.error(f"ta_trade_history 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/scorer_weights', methods=['GET'])
def get_scorer_weights():
    """获取当前评分权重"""
    try:
        from app.services.stock_scorer import get_user_weights, DIMENSION_LABELS
        weights = get_user_weights()
        return success_response(
            weights=weights,
            labels=DIMENSION_LABELS,
        )
    except Exception as e:
        logger.error(f"get_scorer_weights 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/scorer_weights', methods=['PUT'])
def update_scorer_weights():
    """更新评分权重"""
    try:
        data = request.get_json()
        if not data or 'weights' not in data:
            return error_response('缺少 weights 字段', 400)

        from app.services.stock_scorer import save_user_weights
        saved = save_user_weights(data['weights'])
        return success_response(weights=saved)
    except Exception as e:
        logger.error(f"update_scorer_weights 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_trade/<int:trade_id>/discussion', methods=['GET'])
def ai_trade_discussion(trade_id: int):
    """获取 AI 交易讨论历史（交易详情 + 已有对话消息）"""
    try:
        from app.config.database import db_session
        from app.models.ai_trade_record import AiTradeRecord
        from app.models.conversation import Conversation

        trade = db_session.query(AiTradeRecord).get(trade_id)
        if not trade:
            return error_response('交易记录不存在', 404)

        trade_info = {
            'id': trade.id,
            'symbol': trade.symbol,
            'action': trade.action,
            'shares': trade.shares,
            'price': trade.price,
            'trade_date': trade.trade_date.isoformat() if trade.trade_date else None,
            'reason': trade.reason,
            'amount': round(trade.shares * trade.price, 2),
        }

        conv = db_session.query(Conversation).filter_by(ai_trade_id=trade_id).first()
        messages = []
        if conv:
            messages = [m.to_dict() for m in conv.messages]

        return success_response(trade=trade_info, messages=messages)
    except Exception as e:
        logger.error(f"ai_trade_discussion 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_trade/<int:trade_id>/chat', methods=['POST'])
def ai_trade_chat(trade_id: int):
    """AI 交易讨论流式聊天（SSE）"""
    data = request.get_json()
    if not data or not data.get('message'):
        return error_response('缺少 message 字段', 400)

    user_message = data['message'].strip()
    if not user_message:
        return error_response('消息不能为空', 400)

    def generate():
        yield from ai_agent_service.ai_trade_chat_stream(trade_id, user_message)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


# ============================================================
# 内在价值估算
# ============================================================

@bp.route('/api/agent/valuations', methods=['GET'])
def stock_valuations():
    """获取股票池所有股票的内在价值估算"""
    try:
        from app.services.valuation_service import valuate_all_stocks, get_valuation_params
        params = get_valuation_params()
        valuations = valuate_all_stocks(params)
        return success_response(valuations=valuations, params=params)
    except Exception as e:
        logger.error(f"stock_valuations 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/valuations/<symbol>', methods=['GET'])
def stock_valuation_detail(symbol: str):
    """获取单只股票的详细估值"""
    try:
        from app.config.database import db_session
        from app.models.stock import Stock
        from app.models.financial_data import FinancialData, ReportPeriod
        from app.services.valuation_service import valuate_stock, get_valuation_params
        from sqlalchemy import desc

        stock = db_session.query(Stock).filter_by(symbol=symbol.upper()).first()
        if not stock:
            return error_response(f'股票 {symbol} 不存在', 404)

        fins = db_session.query(FinancialData).filter_by(
            stock_id=stock.id, period=ReportPeriod.ANNUAL
        ).order_by(desc(FinancialData.fiscal_year)).limit(3).all()

        params = get_valuation_params()
        valuation = valuate_stock(stock, fins, params)
        return success_response(valuation=valuation, params=params)
    except Exception as e:
        logger.error(f"stock_valuation_detail 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/valuation_params', methods=['GET'])
def get_valuation_params_api():
    """获取当前估值参数"""
    try:
        from app.services.valuation_service import (
            get_valuation_params, DEFAULT_VALUATION_PARAMS, PARAM_LABELS, PARAM_VALIDATION
        )
        params = get_valuation_params()
        # Convert type objects to strings for JSON serialization
        validation = {
            k: {**v, 'type': v['type'].__name__}
            for k, v in PARAM_VALIDATION.items()
        }
        return success_response(
            params=params,
            defaults=DEFAULT_VALUATION_PARAMS,
            labels=PARAM_LABELS,
            validation=validation,
        )
    except Exception as e:
        logger.error(f"get_valuation_params 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/valuation_params', methods=['PUT'])
def update_valuation_params():
    """更新估值参数"""
    try:
        data = request.get_json()
        if not data or 'params' not in data:
            return error_response('缺少 params 字段', 400)

        from app.services.valuation_service import save_valuation_params
        saved = save_valuation_params(data['params'])
        return success_response(params=saved)
    except Exception as e:
        logger.error(f"update_valuation_params 错误: {e}")
        return error_response(str(e), 500)


# ============================================================
# AI 提供商
# ============================================================

@bp.route('/api/agent/ai_provider', methods=['GET'])
def get_ai_provider():
    """获取当前 AI 提供商设置"""
    try:
        from app.config.settings import get_ai_provider, get_anthropic_key, get_minimax_key
        provider = get_ai_provider()
        has_claude = bool(get_anthropic_key())
        has_minimax = bool(get_minimax_key())
        available = []
        if has_claude:
            available.append('claude')
        if has_minimax:
            available.append('minimax')
        return success_response(provider=provider, available=available)
    except Exception as e:
        logger.error(f"get_ai_provider 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/ai_provider', methods=['PUT'])
def set_ai_provider():
    """切换 AI 提供商"""
    try:
        data = request.get_json()
        provider = data.get('provider', '').strip().lower()
        if provider not in ('claude', 'minimax'):
            return error_response('provider 必须是 claude 或 minimax')

        from app.config.database import db_session
        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='ai_provider').first()
        if row:
            row.value = provider
        else:
            db_session.add(UserSetting(key='ai_provider', value=provider))
        db_session.commit()
        return success_response(provider=provider)
    except Exception as e:
        logger.error(f"set_ai_provider 错误: {e}")
        return error_response(str(e), 500)
