"""
TradingAgents 框架集成服务。
封装 TradingAgents 多智能体系统，为股票池中的每只股票运行分析并生成交易建议。
"""
import logging
import os
import sys
from copy import deepcopy
from datetime import date as date_type
from typing import Dict

from app.config.database import db_session

logger = logging.getLogger(__name__)

# Auto-detect TradingAgents path: env var > /opt (server) > local dev
TRADINGAGENTS_PATH = os.getenv('TRADINGAGENTS_PATH',
    '/opt/TradingAgents' if os.path.isdir('/opt/TradingAgents')
    else '/Users/hongyuanyuan/Documents/claude_projects/TradingAgents'
)

# Ensure TradingAgents is importable
if os.path.isdir(TRADINGAGENTS_PATH) and TRADINGAGENTS_PATH not in sys.path:
    sys.path.insert(0, TRADINGAGENTS_PATH)
TRADER_NAME = 'tradingagents'
MAX_POSITIONS = 5


def _get_ta_config() -> dict:
    """
    Build TradingAgents config using the user's current AI provider/key.
    Uses lazy imports to avoid circular dependencies.
    """
    import sys
    if TRADINGAGENTS_PATH not in sys.path:
        sys.path.insert(0, TRADINGAGENTS_PATH)

    from tradingagents.default_config import DEFAULT_CONFIG
    from app.config.settings import (
        get_ai_provider, get_anthropic_key, get_minimax_key,
        AI_MODEL, MINIMAX_DEFAULT_MODEL, MINIMAX_BASE_URL,
    )

    config = deepcopy(DEFAULT_CONFIG)

    # Redirect cache/results to writable directories (important for Docker read-only mounts)
    writable_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'ta_cache')
    os.makedirs(writable_dir, exist_ok=True)
    config['project_dir'] = writable_dir
    config['data_cache_dir'] = os.path.join(writable_dir, 'data_cache')
    config['results_dir'] = os.path.join(writable_dir, 'results')
    os.makedirs(config['data_cache_dir'], exist_ok=True)
    os.makedirs(config['results_dir'], exist_ok=True)

    provider = get_ai_provider()

    if provider == 'claude':
        api_key = get_anthropic_key()
        config['llm_provider'] = 'anthropic'
        config['deep_think_llm'] = AI_MODEL
        config['quick_think_llm'] = AI_MODEL
        config['backend_url'] = None
        config['api_key'] = api_key
    elif provider == 'minimax':
        api_key = get_minimax_key()
        config['llm_provider'] = 'openai'
        config['deep_think_llm'] = MINIMAX_DEFAULT_MODEL
        config['quick_think_llm'] = MINIMAX_DEFAULT_MODEL
        config['backend_url'] = MINIMAX_BASE_URL
        config['api_key'] = api_key
    else:
        # fallback to claude
        api_key = get_anthropic_key()
        config['llm_provider'] = 'anthropic'
        config['deep_think_llm'] = AI_MODEL
        config['quick_think_llm'] = AI_MODEL
        config['backend_url'] = None
        config['api_key'] = api_key

    return config


def run_ta_for_stock(symbol: str) -> dict:
    """
    Run TradingAgents for a single stock.
    Returns {'action': 'buy'/'sell'/'hold', 'reason': '...'}
    """
    import os
    import sys
    if TRADINGAGENTS_PATH not in sys.path:
        sys.path.insert(0, TRADINGAGENTS_PATH)

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = _get_ta_config()
    api_key = config.pop('api_key', None)

    today_str = date_type.today().strftime('%Y-%m-%d')

    logger.info(f"[TradingAgents] 开始分析 {symbol} ...")

    try:
        # Set API key in environment before constructing the graph,
        # because the LLM client factories read from env when no explicit key is passed.
        env_key_name = None
        old_env_val = None

        if config['llm_provider'] == 'anthropic' and api_key:
            env_key_name = 'ANTHROPIC_API_KEY'
        elif config['llm_provider'] == 'openai' and api_key:
            env_key_name = 'OPENAI_API_KEY'

        if env_key_name and api_key:
            old_env_val = os.environ.get(env_key_name)
            os.environ[env_key_name] = api_key

        try:
            ta_graph = TradingAgentsGraph(config=config)
            final_state, decision = ta_graph.propagate(symbol, today_str)
        finally:
            # Restore env
            if env_key_name is not None:
                if old_env_val is None:
                    os.environ.pop(env_key_name, None)
                else:
                    os.environ[env_key_name] = old_env_val

        # decision is the processed signal: "BUY", "SELL", or "HOLD"
        action = decision.strip().upper() if decision else 'HOLD'
        if action not in ('BUY', 'SELL', 'HOLD'):
            action = 'HOLD'

        # Extract reason from the final trade decision text
        reason = final_state.get('final_trade_decision', '') or ''
        # Truncate to a reasonable length
        if len(reason) > 500:
            reason = reason[:500] + '...'

        logger.info(f"[TradingAgents] {symbol} 决策: {action}")
        return {'action': action.lower(), 'reason': reason}

    except Exception as e:
        logger.error(f"[TradingAgents] {symbol} 分析失败: {e}", exc_info=True)
        return {'action': 'hold', 'reason': f'TradingAgents 分析失败: {str(e)[:200]}'}


def compute_ta_holdings() -> Dict[str, Dict]:
    """
    Compute TradingAgents holdings from AiTradeRecord where trader='tradingagents'.
    Returns { "AAPL": {"shares": 50, "avg_cost": 150.5}, ... }
    """
    from app.models.ai_trade_record import AiTradeRecord

    records = (
        db_session.query(AiTradeRecord)
        .filter_by(trader=TRADER_NAME)
        .order_by(AiTradeRecord.trade_date)
        .all()
    )
    holdings: Dict[str, Dict] = {}

    for r in records:
        h = holdings.setdefault(r.symbol, {'shares': 0, 'total_cost': 0.0})
        if r.action == 'buy':
            h['total_cost'] += r.shares * r.price
            h['shares'] += r.shares
        elif r.action == 'sell':
            if h['shares'] > 0:
                avg = h['total_cost'] / h['shares']
                sold = min(r.shares, h['shares'])
                h['total_cost'] -= avg * sold
                h['shares'] -= sold

    return {
        sym: {
            'shares': h['shares'],
            'avg_cost': round(h['total_cost'] / h['shares'], 4) if h['shares'] > 0 else 0,
        }
        for sym, h in holdings.items()
        if h['shares'] > 0
    }


def compute_ta_cash() -> float:
    """
    Compute TradingAgents cash balance.
    Uses 'ta_starting_cash' (fallback to 'total_capital') minus buys plus sells.
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.user_setting import UserSetting

    # Determine starting cash
    try:
        row = db_session.query(UserSetting).filter_by(key='ta_starting_cash').first()
        if row:
            starting_cash = float(row.value)
        else:
            row = db_session.query(UserSetting).filter_by(key='total_capital').first()
            starting_cash = float(row.value) if row else 0
    except Exception:
        starting_cash = 0

    if starting_cash <= 0:
        return 0.0

    records = (
        db_session.query(AiTradeRecord)
        .filter_by(trader=TRADER_NAME)
        .order_by(AiTradeRecord.trade_date)
        .all()
    )
    cash = starting_cash
    for r in records:
        if r.action == 'buy':
            cash -= r.shares * r.price
        elif r.action == 'sell':
            cash += r.shares * r.price
    return round(cash, 2)


def generate_ta_trades() -> dict:
    """
    Run TradingAgents for all in-pool stocks, determine position sizing,
    save records to AiTradeRecord with trader='tradingagents'.
    Returns { "AAPL": {"action": "buy", "shares": 10, "reason": "..."}, ... }
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.stock import Stock
    from app.models.user_setting import UserSetting

    today = date_type.today()

    # 1. Check if already executed today
    today_records = (
        db_session.query(AiTradeRecord)
        .filter(
            AiTradeRecord.trader == TRADER_NAME,
            AiTradeRecord.trade_date == today,
            ~AiTradeRecord.reason.like('%初始化%'),
            ~AiTradeRecord.reason.like('%重置%'),
        )
        .all()
    )
    if today_records:
        logger.info("[TradingAgents] 今日已执行过，返回已有记录")
        return {
            r.symbol: {'action': r.action, 'shares': r.shares, 'reason': r.reason or ''}
            for r in today_records if r.action in ('buy', 'sell')
        }

    # 2. Get all in-pool stock symbols
    stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
    if not stocks:
        logger.warning("[TradingAgents] 股票池为空，跳过")
        return {}

    symbols = [s.symbol for s in stocks]
    logger.info(f"[TradingAgents] 股票池: {symbols}")

    # Build price map from current stock prices
    price_map = {}
    for s in stocks:
        if s.current_price and s.current_price > 0:
            price_map[s.symbol] = s.current_price

    # 3. Get ta_holdings and ta_cash
    ta_holdings = compute_ta_holdings()
    ta_cash = compute_ta_cash()
    if ta_cash < 0:
        ta_cash = 0

    logger.info(f"[TradingAgents] 可用现金: ${ta_cash:,.2f}, 当前持仓: {list(ta_holdings.keys())}")

    # 4. For each symbol, call run_ta_for_stock()
    decisions = {}
    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[TradingAgents] 分析进度 {i}/{len(symbols)}: {symbol}")
        decisions[symbol] = run_ta_for_stock(symbol)

    # 5. Position sizing
    current_position_count = len(ta_holdings)
    remaining_slots = max(0, MAX_POSITIONS - current_position_count)

    # Collect buy candidates and sell candidates
    buy_candidates = []
    sell_candidates = []
    for symbol, dec in decisions.items():
        if dec['action'] == 'buy' and symbol not in ta_holdings:
            buy_candidates.append(symbol)
        elif dec['action'] == 'sell' and symbol in ta_holdings:
            sell_candidates.append(symbol)

    # Allocate equal weight for buys
    buy_slots = min(len(buy_candidates), remaining_slots)
    cash_per_slot = (ta_cash / buy_slots) if buy_slots > 0 else 0

    valid_trades = {}
    available_cash = ta_cash

    # Process sells first (frees up cash and slots)
    for symbol in sell_candidates:
        h = ta_holdings.get(symbol, {})
        shares = h.get('shares', 0)
        price = price_map.get(symbol, 0)
        if shares <= 0 or price <= 0:
            continue
        record = AiTradeRecord(
            trader=TRADER_NAME,
            symbol=symbol,
            action='sell',
            shares=shares,
            price=price,
            trade_date=today,
            reason=decisions[symbol].get('reason', '')[:500],
        )
        db_session.add(record)
        available_cash += shares * price
        valid_trades[symbol] = {
            'action': 'sell',
            'shares': shares,
            'reason': decisions[symbol].get('reason', ''),
        }
        logger.info(f"[TradingAgents] SELL {symbol}: {shares} 股 @ ${price:.2f}")

    # Recalculate slots after sells
    remaining_slots_after_sell = remaining_slots + len(sell_candidates)
    buy_slots = min(len(buy_candidates), remaining_slots_after_sell)
    if buy_slots > 0:
        cash_per_slot = available_cash / buy_slots

    # Process buys
    buys_done = 0
    for symbol in buy_candidates:
        if buys_done >= buy_slots:
            break
        price = price_map.get(symbol, 0)
        if price <= 0:
            logger.warning(f"[TradingAgents] {symbol} 无价格数据，跳过买入")
            continue
        # Calculate shares: allocate cash_per_slot, buy whole shares
        max_shares = int(cash_per_slot / price) if price > 0 else 0
        if max_shares <= 0:
            logger.warning(f"[TradingAgents] {symbol} 现金不足买入1股，跳过")
            continue
        # Validate against available cash
        cost = max_shares * price
        if cost > available_cash:
            max_shares = int(available_cash / price)
            cost = max_shares * price
        if max_shares <= 0:
            continue

        record = AiTradeRecord(
            trader=TRADER_NAME,
            symbol=symbol,
            action='buy',
            shares=max_shares,
            price=price,
            trade_date=today,
            reason=decisions[symbol].get('reason', '')[:500],
        )
        db_session.add(record)
        available_cash -= cost
        buys_done += 1
        valid_trades[symbol] = {
            'action': 'buy',
            'shares': max_shares,
            'reason': decisions[symbol].get('reason', ''),
        }
        logger.info(f"[TradingAgents] BUY {symbol}: {max_shares} 股 @ ${price:.2f}")

    # 8. If no trades, save _HOLD record
    if not valid_trades:
        db_session.add(AiTradeRecord(
            trader=TRADER_NAME,
            symbol='_HOLD',
            action='hold',
            shares=0,
            price=0,
            trade_date=today,
            reason='TradingAgents 今日无操作：所有股票建议持有或条件不满足',
        ))
        logger.info("[TradingAgents] 今日无交易")

    try:
        db_session.commit()
        logger.info(f"[TradingAgents] 保存 {len(valid_trades)} 条交易记录")
    except Exception as e:
        db_session.rollback()
        logger.error(f"[TradingAgents] 保存交易记录失败: {e}")
        return {}

    return valid_trades
