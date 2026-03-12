"""
Portfolio Service
从 TradeRecord 聚合计算当前持仓情况

持仓 = 所有 buy 的数量 - 所有 sell 的数量
平均成本 = 加权平均买入价格
"""
import logging
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import asc

from app.config.database import db_session
from app.models.trade_record import TradeRecord
from app.models.stock import Stock
from app.utils.cache import cache

logger = logging.getLogger(__name__)


def compute_holdings() -> List[Dict]:
    """
    从 TradeRecord 聚合计算当前持仓

    算法:
    - 按 symbol 分组，按日期顺序遍历
    - buy: 加权平均更新成本价
    - sell: 减少持仓（成本价不变）
    - 最终 net_shares > 0 的为当前持仓

    Returns:
        List of holding dicts sorted by market_value desc
    """
    cached_result = cache.get('portfolio_holdings')
    if cached_result is not None:
        return cached_result

    trades = db_session.query(TradeRecord).order_by(
        TradeRecord.symbol, asc(TradeRecord.trade_date), asc(TradeRecord.created_at)
    ).all()

    if not trades:
        return []

    # 按 symbol 分组聚合
    holdings_map: Dict[str, Dict] = {}

    for t in trades:
        symbol = t.symbol.upper()
        if symbol not in holdings_map:
            holdings_map[symbol] = {
                'symbol': symbol,
                'net_shares': 0.0,
                'avg_cost': 0.0,
                'total_cost': 0.0,  # 用于加权平均计算
                'first_buy_date': None,
                'last_trade_date': None,
                'trade_count': 0,
            }

        h = holdings_map[symbol]
        h['trade_count'] += 1
        h['last_trade_date'] = t.trade_date

        if t.action == 'buy':
            # 加权平均成本: new_avg = (old_shares * old_avg + new_shares * new_price) / total_shares
            new_total_cost = h['net_shares'] * h['avg_cost'] + t.quantity * t.price
            h['net_shares'] += t.quantity
            if h['net_shares'] > 0:
                h['avg_cost'] = new_total_cost / h['net_shares']
            if h['first_buy_date'] is None:
                h['first_buy_date'] = t.trade_date

        elif t.action == 'sell':
            h['net_shares'] -= t.quantity
            # 卖出不改变平均成本
            # 如果全部卖出后又买入，重置成本
            if h['net_shares'] <= 0:
                h['net_shares'] = 0.0
                h['avg_cost'] = 0.0
                h['first_buy_date'] = None  # 重置，下次买入时更新

    # 过滤出当前有持仓的股票
    active_holdings = []
    for symbol, h in holdings_map.items():
        if h['net_shares'] <= 0:
            continue

        # 查找当前价格
        stock = db_session.query(Stock).filter_by(symbol=symbol).first()
        current_price = stock.current_price if stock else None

        holding = {
            'symbol': symbol,
            'stock_name': stock.name if stock else symbol,
            'stock_id': stock.id if stock else None,
            'net_shares': round(h['net_shares'], 4),
            'avg_cost': round(h['avg_cost'], 4),
            'current_price': current_price,
            'market_value': round(current_price * h['net_shares'], 2) if current_price else None,
            'unrealized_pnl': round((current_price - h['avg_cost']) * h['net_shares'], 2) if current_price and h['avg_cost'] > 0 else None,
            'unrealized_pnl_pct': round((current_price - h['avg_cost']) / h['avg_cost'] * 100, 2) if current_price and h['avg_cost'] > 0 else None,
            'holding_days': (date.today() - h['first_buy_date']).days if h['first_buy_date'] else None,
            'first_buy_date': h['first_buy_date'].isoformat() if h['first_buy_date'] else None,
            'last_trade_date': h['last_trade_date'].isoformat() if h['last_trade_date'] else None,
            'trade_count': h['trade_count'],
        }
        active_holdings.append(holding)

    # 按市值排序（无市值的排最后）
    active_holdings.sort(key=lambda x: x['market_value'] or 0, reverse=True)

    cache.set('portfolio_holdings', active_holdings, ttl_seconds=60)
    return active_holdings


def get_holding_for_symbol(symbol: str) -> Optional[Dict]:
    """获取单只股票的持仓信息"""
    holdings = compute_holdings()
    symbol = symbol.upper()
    for h in holdings:
        if h['symbol'] == symbol:
            return h
    return None


def invalidate_portfolio_cache():
    """持仓缓存失效（交易变动后调用）"""
    cache.invalidate_prefix('portfolio_holdings')
