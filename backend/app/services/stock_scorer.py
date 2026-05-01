"""
Stock Scorer — 5 维度评分引擎
基于规则打分（可解释） + AI 补充分析

5 个维度及默认权重：
  估值 (valuation)        30%
  盈利质量 (earnings)     25%
  财务健康 (health)       20%
  护城河 (moat)           15%
  新闻情绪 (news)         10%
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

from app.config.database import db_session
from app.models.stock import Stock
from app.models.stock_news_analysis import StockNewsAnalysis
from app.models.user_setting import UserSetting
from app.models.financial_data import FinancialData, ReportPeriod
from app.services.kpi_calculator import compute_single_period_kpis, compute_multi_period_kpis
from app.services.stock_analysis_service import batch_load_recent_financials
from app.utils.cache import cache

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
#  默认权重
# ════════════════════════════════════════════════════════════════

DEFAULT_WEIGHTS: Dict[str, float] = {
    'valuation': 0.30,
    'earnings_quality': 0.25,
    'financial_health': 0.20,
    'moat': 0.15,
    'news_sentiment': 0.10,
}

DIMENSION_LABELS: Dict[str, str] = {
    'valuation': '估值',
    'earnings_quality': '盈利质量',
    'financial_health': '财务健康',
    'moat': '护城河',
    'news_sentiment': '新闻情绪',
}


# ════════════════════════════════════════════════════════════════
#  用户权重读写
# ════════════════════════════════════════════════════════════════

def get_user_weights() -> Dict[str, float]:
    """获取当前用户的评分权重，未设置则返回默认值"""
    try:
        row = db_session.query(UserSetting).filter_by(key='scorer_weights').first()
        if row and row.value:
            weights = json.loads(row.value)
            # 验证所有维度都在
            for k in DEFAULT_WEIGHTS:
                if k not in weights:
                    weights[k] = DEFAULT_WEIGHTS[k]
            return weights
    except Exception as e:
        logger.warning(f"读取评分权重失败: {e}")
    return dict(DEFAULT_WEIGHTS)


def save_user_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """
    保存用户评分权重
    自动归一化使权重之和为 1.0
    """
    # 只保留有效维度
    clean = {}
    for k in DEFAULT_WEIGHTS:
        clean[k] = float(weights.get(k, DEFAULT_WEIGHTS[k]))

    # 归一化
    total = sum(clean.values())
    if total > 0:
        clean = {k: round(v / total, 4) for k, v in clean.items()}

    row = db_session.query(UserSetting).filter_by(key='scorer_weights').first()
    if row:
        row.value = json.dumps(clean)
    else:
        db_session.add(UserSetting(key='scorer_weights', value=json.dumps(clean)))
    db_session.commit()
    return clean


# ════════════════════════════════════════════════════════════════
#  维度评分函数（每个 0-100）
# ════════════════════════════════════════════════════════════════

def _clamp(val: float) -> int:
    """Clamp score to 0-100"""
    return max(0, min(100, int(round(val))))


def _score_valuation(stock: Stock, latest_fd: Optional[FinancialData]) -> Dict[str, Any]:
    """估值评分：PE, PB, PS, 股息率"""
    sub_scores = []
    details = {}

    # PE 评分
    pe = stock.pe_ratio
    if pe is not None and pe > 0:
        if pe <= 10:
            s = 95
        elif pe <= 15:
            s = 85
        elif pe <= 20:
            s = 70
        elif pe <= 25:
            s = 55
        elif pe <= 30:
            s = 40
        elif pe <= 40:
            s = 25
        else:
            s = 10
        sub_scores.append(s)
        details['PE'] = f"{pe:.1f} → {s}分"
    elif pe is not None and pe < 0:
        # 亏损股 PE 为负
        sub_scores.append(20)
        details['PE'] = f"{pe:.1f}(亏损) → 20分"

    # PB 评分
    pb = stock.pb_ratio
    if pb is not None and pb > 0:
        if pb <= 1.0:
            s = 95
        elif pb <= 2.0:
            s = 80
        elif pb <= 3.0:
            s = 65
        elif pb <= 5.0:
            s = 45
        elif pb <= 8.0:
            s = 30
        else:
            s = 15
        sub_scores.append(s)
        details['PB'] = f"{pb:.2f} → {s}分"

    # PS 评分
    extra = stock.extra_data or {}
    ps = extra.get('ps_ratio')
    if ps is not None and ps > 0:
        if ps <= 1.0:
            s = 95
        elif ps <= 3.0:
            s = 80
        elif ps <= 5.0:
            s = 65
        elif ps <= 8.0:
            s = 45
        elif ps <= 15:
            s = 30
        else:
            s = 15
        sub_scores.append(s)
        details['PS'] = f"{ps:.1f} → {s}分"

    # 股息率评分
    dy = stock.dividend_yield
    if dy is not None and dy >= 0:
        dy_pct = dy * 100 if dy < 1 else dy  # 可能已经是百分比
        if dy_pct >= 4.0:
            s = 90
        elif dy_pct >= 3.0:
            s = 75
        elif dy_pct >= 2.0:
            s = 60
        elif dy_pct >= 1.0:
            s = 45
        elif dy_pct > 0:
            s = 30
        else:
            s = 20
        sub_scores.append(s)
        details['股息率'] = f"{dy_pct:.2f}% → {s}分"

    score = int(round(sum(sub_scores) / len(sub_scores))) if sub_scores else 50
    return {'score': _clamp(score), 'details': details}


def _score_earnings_quality(fins: List[FinancialData]) -> Dict[str, Any]:
    """盈利质量评分：连续盈利、利润率、现金流/利润比"""
    sub_scores = []
    details = {}

    if not fins:
        return {'score': 50, 'details': {'说明': '无财务数据'}}

    latest = fins[0]
    kpis = compute_single_period_kpis(latest)
    multi_kpis = compute_multi_period_kpis(fins)

    # 连续 3 年盈利
    if len(fins) >= 3:
        profitable = all(f.net_income is not None and f.net_income > 0 for f in fins[:3])
        s = 90 if profitable else 30
        sub_scores.append(s)
        details['连续3年盈利'] = f"{'是' if profitable else '否'} → {s}分"
    elif len(fins) >= 1 and latest.net_income is not None:
        s = 60 if latest.net_income > 0 else 20
        sub_scores.append(s)
        details['最新盈利'] = f"{'盈利' if latest.net_income > 0 else '亏损'} → {s}分"

    # 净利率
    nm = kpis.get('net_margin')
    if nm is not None:
        nm_pct = nm * 100
        if nm_pct >= 20:
            s = 90
        elif nm_pct >= 15:
            s = 80
        elif nm_pct >= 10:
            s = 65
        elif nm_pct >= 5:
            s = 50
        elif nm_pct >= 0:
            s = 30
        else:
            s = 10
        sub_scores.append(s)
        details['净利率'] = f"{nm_pct:.1f}% → {s}分"

    # 毛利率
    gm = kpis.get('gross_margin')
    if gm is not None:
        gm_pct = gm * 100
        if gm_pct >= 50:
            s = 90
        elif gm_pct >= 40:
            s = 80
        elif gm_pct >= 30:
            s = 65
        elif gm_pct >= 20:
            s = 50
        else:
            s = 30
        sub_scores.append(s)
        details['毛利率'] = f"{gm_pct:.1f}% → {s}分"

    # 现金流/利润比（3年）
    cfr = multi_kpis.get('cashflow_profit_ratio_3y')
    if cfr is not None:
        if cfr >= 1.2:
            s = 95
        elif cfr >= 1.0:
            s = 85
        elif cfr >= 0.8:
            s = 65
        elif cfr >= 0.5:
            s = 40
        else:
            s = 20
        sub_scores.append(s)
        details['现金流/利润(3Y)'] = f"{cfr:.2f} → {s}分"

    score = int(round(sum(sub_scores) / len(sub_scores))) if sub_scores else 50
    return {'score': _clamp(score), 'details': details}


def _score_financial_health(latest_fd: Optional[FinancialData]) -> Dict[str, Any]:
    """财务健康评分：净现金、负债率、流动性"""
    sub_scores = []
    details = {}

    if not latest_fd:
        return {'score': 50, 'details': {'说明': '无财务数据'}}

    kpis = compute_single_period_kpis(latest_fd)

    # 净现金
    net_cash = kpis.get('net_cash')
    if net_cash is not None:
        if net_cash > 0:
            s = 85
        elif net_cash > -1e9:
            s = 55
        else:
            s = 25
        sub_scores.append(s)
        nc_b = net_cash / 1e9
        details['净现金'] = f"{nc_b:.1f}B → {s}分"

    # 负债率 = (短期借款 + 长期借款) / 总资产
    stb = latest_fd.short_term_borrowings or 0
    ltb = latest_fd.long_term_borrowings or 0
    ta = latest_fd.total_assets
    if ta and ta > 0:
        debt_ratio = (stb + ltb) / ta * 100
        if debt_ratio <= 10:
            s = 95
        elif debt_ratio <= 20:
            s = 80
        elif debt_ratio <= 30:
            s = 65
        elif debt_ratio <= 40:
            s = 50
        elif debt_ratio <= 50:
            s = 35
        else:
            s = 15
        sub_scores.append(s)
        details['借款/总资产'] = f"{debt_ratio:.1f}% → {s}分"

    # 流动性：总资产 - 流动负债 > 0
    ta_cl = kpis.get('total_assets_minus_current_liab')
    if ta_cl is not None and ta is not None and ta > 0:
        ratio = ta_cl / ta * 100
        if ratio >= 70:
            s = 90
        elif ratio >= 50:
            s = 70
        elif ratio >= 30:
            s = 50
        else:
            s = 25
        sub_scores.append(s)
        details['(总资产-流动负债)/总资产'] = f"{ratio:.0f}% → {s}分"

    # 净资产为正
    eq = latest_fd.total_equity
    if eq is not None:
        s = 80 if eq > 0 else 15
        sub_scores.append(s)
        details['净资产'] = f"{'正' if eq > 0 else '负'} → {s}分"

    score = int(round(sum(sub_scores) / len(sub_scores))) if sub_scores else 50
    return {'score': _clamp(score), 'details': details}


def _score_moat(fins: List[FinancialData], latest_fd: Optional[FinancialData]) -> Dict[str, Any]:
    """护城河评分：毛利率趋势、市场份额"""
    sub_scores = []
    details = {}

    if not fins:
        return {'score': 50, 'details': {'说明': '无财务数据'}}

    # 毛利率水平（最新）
    latest_kpis = compute_single_period_kpis(fins[0])
    gm = latest_kpis.get('gross_margin')
    if gm is not None:
        gm_pct = gm * 100
        if gm_pct >= 60:
            s = 95
        elif gm_pct >= 50:
            s = 85
        elif gm_pct >= 40:
            s = 70
        elif gm_pct >= 30:
            s = 55
        elif gm_pct >= 20:
            s = 40
        else:
            s = 25
        sub_scores.append(s)
        details['毛利率水平'] = f"{gm_pct:.1f}% → {s}分"

    # 毛利率趋势（3年稳定或上升=好）
    if len(fins) >= 2:
        gms = []
        for f in fins[:3]:
            fk = compute_single_period_kpis(f)
            g = fk.get('gross_margin')
            if g is not None:
                gms.append(g)
        if len(gms) >= 2:
            trend = gms[0] - gms[-1]  # 最新 - 最早，正 = 上升
            if trend > 0.02:
                s = 90
                label = '上升'
            elif trend >= -0.02:
                s = 70
                label = '稳定'
            else:
                s = 35
                label = '下降'
            sub_scores.append(s)
            details['毛利率趋势'] = f"{label} → {s}分"

    # 市场份额（来自 extended_metrics）
    if latest_fd:
        ext = latest_fd.extended_metrics_dict or {}
        moat_ind = ext.get('moat_indicators') or {}
        ms = moat_ind.get('market_share_pct')
        if ms is not None:
            if ms >= 30:
                s = 95
            elif ms >= 20:
                s = 80
            elif ms >= 10:
                s = 65
            elif ms >= 5:
                s = 50
            else:
                s = 35
            sub_scores.append(s)
            details['市场份额'] = f"{ms}% → {s}分"

    score = int(round(sum(sub_scores) / len(sub_scores))) if sub_scores else 50
    return {'score': _clamp(score), 'details': details}


def _today_news_bounds_utc():
    utc_today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(utc_today, datetime.min.time(), tzinfo=timezone.utc)
    tomorrow_start = datetime.combine(
        utc_today + timedelta(days=1),
        datetime.min.time(), tzinfo=timezone.utc,
    )
    return today_start, tomorrow_start


def _load_today_news_analysis(symbols: List[str]) -> Dict[str, StockNewsAnalysis]:
    """Batch-load today's news analysis rows to avoid per-stock queries."""
    if not symbols:
        return {}

    today_start, tomorrow_start = _today_news_bounds_utc()
    rows = db_session.query(StockNewsAnalysis).filter(
        StockNewsAnalysis.symbol.in_(symbols),
        StockNewsAnalysis.analyzed_at >= today_start,
        StockNewsAnalysis.analyzed_at < tomorrow_start,
    ).all()

    analysis_map: Dict[str, StockNewsAnalysis] = {}
    for row in rows:
        analysis_map.setdefault(row.symbol, row)
    return analysis_map


_MISSING_NEWS_ANALYSIS = object()


def _score_news_sentiment(
    symbol: str,
    analysis: Any = _MISSING_NEWS_ANALYSIS,
) -> Dict[str, Any]:
    """新闻情绪评分：基于当天 StockNewsAnalysis"""
    if analysis is _MISSING_NEWS_ANALYSIS:
        today_start, tomorrow_start = _today_news_bounds_utc()
        analysis = db_session.query(StockNewsAnalysis).filter(
            StockNewsAnalysis.symbol == symbol,
            StockNewsAnalysis.analyzed_at >= today_start,
            StockNewsAnalysis.analyzed_at < tomorrow_start,
        ).first()

    if not analysis:
        return {'score': 50, 'details': {'说明': '无当日新闻分析'}}

    sentiment = analysis.sentiment
    sentiment_map = {'bullish': 85, 'neutral': 50, 'bearish': 15}
    score = sentiment_map.get(sentiment, 50)

    details = {
        '情绪': f"{sentiment} → {score}分",
        '摘要': (analysis.summary or '')[:80],
    }

    return {'score': _clamp(score), 'details': details}


# ════════════════════════════════════════════════════════════════
#  操作建议判定
# ════════════════════════════════════════════════════════════════

def _determine_action(total_score: float, holding: Optional[Dict]) -> Dict[str, str]:
    """
    根据总分和持仓状态生成操作建议（旧版，纯评分驱动）

    Returns:
        {'action': 'buy|add|hold|reduce|sell', 'action_label': '中文标签', 'action_emoji': emoji}
    """
    has_position = holding is not None and holding.get('net_shares', 0) > 0

    if total_score >= 80:
        if has_position:
            return {'action': 'add', 'action_label': '加仓', 'action_emoji': '🟢'}
        return {'action': 'buy', 'action_label': '买入', 'action_emoji': '🟢'}
    elif total_score >= 60:
        return {'action': 'hold', 'action_label': '持有', 'action_emoji': '⚪'}
    elif total_score >= 40:
        if has_position:
            return {'action': 'reduce', 'action_label': '减仓', 'action_emoji': '🟡'}
        return {'action': 'hold', 'action_label': '观望', 'action_emoji': '⚪'}
    else:
        if has_position:
            return {'action': 'sell', 'action_label': '卖出', 'action_emoji': '🔴'}
        return {'action': 'avoid', 'action_label': '回避', 'action_emoji': '🔴'}


def _determine_action_v2(
    quality_score: float,
    margin_of_safety: Optional[Dict],
    holding: Optional[Dict],
    valuation_params: Optional[Dict] = None,
) -> Dict[str, str]:
    """
    质量+估值双维度决策（新版）

    决策矩阵：
      好公司+好价格 → 买入
      好公司+贵了 → 观望
      差公司+任何价格 → 卖出/回避
      严重高估 → 减仓
    """
    from app.services.valuation_service import DEFAULT_VALUATION_PARAMS

    params = valuation_params or DEFAULT_VALUATION_PARAMS
    has_position = holding is not None and holding.get('net_shares', 0) > 0
    mos_pct = (margin_of_safety or {}).get('pct')
    intrinsic_value = (margin_of_safety or {}).get('pct') is not None

    quality_min = params.get('quality_score_min', 60)
    mos_threshold = params.get('margin_of_safety', 0.25) * 100
    sell_overval = params.get('sell_overvaluation_pct', 0.20) * 100

    # 无法估值时回退到旧逻辑
    if mos_pct is None:
        return _determine_action(quality_score, holding)

    # 差公司：质量评分过低
    if quality_score < 40:
        if has_position:
            return {'action': 'sell', 'action_label': '卖出(质量不足)', 'action_emoji': '🔴'}
        return {'action': 'avoid', 'action_label': '回避', 'action_emoji': '🔴'}

    # 好公司+好价格：高质量 + 深度低估
    if quality_score >= 75 and mos_pct >= mos_threshold:
        if has_position:
            return {'action': 'add', 'action_label': '加仓', 'action_emoji': '🟢'}
        return {'action': 'buy', 'action_label': '买入', 'action_emoji': '🟢'}

    # 合格公司+有安全边际
    if quality_score >= quality_min and mos_pct >= mos_threshold * 0.6:
        if has_position:
            return {'action': 'add', 'action_label': '小幅加仓', 'action_emoji': '🟢'}
        return {'action': 'buy', 'action_label': '建仓', 'action_emoji': '🟢'}

    # 严重高估：持仓减仓，非持仓回避
    if mos_pct < -sell_overval:
        if has_position:
            return {'action': 'reduce', 'action_label': '减仓(估值偏高)', 'action_emoji': '🟡'}
        return {'action': 'avoid', 'action_label': '观望(估值偏高)', 'action_emoji': '⚪'}

    # 其他情况：持有/观望
    if has_position:
        return {'action': 'hold', 'action_label': '持有', 'action_emoji': '⚪'}
    return {'action': 'hold', 'action_label': '观望', 'action_emoji': '⚪'}


# ════════════════════════════════════════════════════════════════
#  核心评分函数
# ════════════════════════════════════════════════════════════════

def score_stock(
    stock: Stock,
    fins: List[FinancialData],
    holding: Optional[Dict],
    weights: Dict[str, float],
    news_analysis: Any = _MISSING_NEWS_ANALYSIS,
) -> Dict[str, Any]:
    """
    对单只股票进行 5 维度评分

    Returns:
        {
            'symbol', 'stock_name', 'total_score',
            'dimensions': {dim_key: {score, weight, details}},
            'action', 'action_label', 'action_emoji',
            'holding': {持仓信息},
        }
    """
    latest_fd = fins[0] if fins else None

    dim_results = {
        'valuation': _score_valuation(stock, latest_fd),
        'earnings_quality': _score_earnings_quality(fins),
        'financial_health': _score_financial_health(latest_fd),
        'moat': _score_moat(fins, latest_fd),
        'news_sentiment': _score_news_sentiment(stock.symbol, news_analysis),
    }

    # 加权计算总分
    total = 0.0
    for dim_key, result in dim_results.items():
        w = weights.get(dim_key, DEFAULT_WEIGHTS.get(dim_key, 0))
        result['weight'] = w
        result['label'] = DIMENSION_LABELS.get(dim_key, dim_key)
        total += result['score'] * w

    total_score = _clamp(total)
    action_info = _determine_action(total_score, holding)

    return {
        'symbol': stock.symbol,
        'stock_name': stock.name or stock.symbol,
        'total_score': total_score,
        'dimensions': dim_results,
        **action_info,
        'holding': holding,
        'current_price': stock.current_price,
        'pe_ratio': stock.pe_ratio,
        'market_cap': stock.market_cap,
    }


# ════════════════════════════════════════════════════════════════
#  批量评分 + AI 补充
# ════════════════════════════════════════════════════════════════

def score_all_stocks(weights: Optional[Dict[str, float]] = None) -> List[Dict]:
    """
    对股票池所有股票进行评分并排序

    Returns:
        List of scored stock dicts, sorted by total_score desc
    """
    if weights is None:
        weights = get_user_weights()

    # 加载股票池
    stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
    if not stocks:
        return []

    # 批量加载财务数据
    stock_ids = [s.id for s in stocks]
    fins_map = batch_load_recent_financials(stock_ids, limit=3)

    # 加载持仓
    from app.services.portfolio_service import compute_holdings
    holdings = compute_holdings()
    holdings_map = {h['symbol']: h for h in holdings}
    news_map = _load_today_news_analysis([s.symbol for s in stocks])

    # 逐只评分
    results = []
    for stock in stocks:
        fins = fins_map.get(stock.id, [])
        holding = holdings_map.get(stock.symbol)
        scored = score_stock(stock, fins, holding, weights, news_map.get(stock.symbol))
        results.append(scored)

    # 有持仓的排前面，同组内按总分排序
    results.sort(key=lambda x: (x.get('holding') is not None, x['total_score']), reverse=True)
    return results


def score_and_valuate_all_stocks(
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict]:
    """
    评分 + 估值一体化：对股票池所有股票进行质量评分和内在价值估值
    返回包含估值数据的评分列表，使用 _determine_action_v2 双维度决策
    """
    if weights is None:
        weights = get_user_weights()

    stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
    if not stocks:
        return []

    stock_ids = [s.id for s in stocks]
    fins_map = batch_load_recent_financials(stock_ids, limit=3)

    from app.services.portfolio_service import compute_holdings
    holdings = compute_holdings()
    holdings_map = {h['symbol']: h for h in holdings}
    news_map = _load_today_news_analysis([s.symbol for s in stocks])

    from app.services.valuation_service import valuate_stock, get_valuation_params
    valuation_params = get_valuation_params()

    results = []
    for stock in stocks:
        fins = fins_map.get(stock.id, [])
        holding = holdings_map.get(stock.symbol)

        # 质量评分
        scored = score_stock(stock, fins, holding, weights, news_map.get(stock.symbol))

        # 内在价值估值
        try:
            valuation = valuate_stock(stock, fins, valuation_params)
        except Exception as e:
            logger.warning(f"估值 {stock.symbol} 失败: {e}")
            valuation = None

        if valuation:
            scored['valuation'] = valuation
            scored['intrinsic_value'] = valuation['composite'].get('intrinsic_value')
            scored['margin_of_safety'] = valuation['margin_of_safety']
            scored['company_type'] = valuation.get('company_type', 'mature')
            scored['company_type_label'] = valuation.get('company_type_label', '成熟型')
            scored['tags'] = valuation.get('tags', [])

            # 用双维度决策覆盖旧的纯评分决策
            action_v2 = _determine_action_v2(
                scored['total_score'],
                valuation['margin_of_safety'],
                holding,
                valuation_params,
            )
            scored['action'] = action_v2['action']
            scored['action_label'] = action_v2['action_label']
            scored['action_emoji'] = action_v2['action_emoji']
        else:
            scored['valuation'] = None
            scored['intrinsic_value'] = None
            scored['margin_of_safety'] = {'pct': None, 'signal': 'unknown',
                                           'signal_label': '无法估值', 'signal_emoji': '⚫'}
            scored['company_type'] = 'mature'
            scored['company_type_label'] = '成熟型'

        results.append(scored)

    results.sort(key=lambda x: (x.get('holding') is not None, x['total_score']), reverse=True)
    return results


def generate_ai_recommendations(scored_stocks: List[Dict]) -> str:
    """
    基于规则评分结果，调用 AI 生成自然语言补充分析

    只取评分最高的 4 只和最低的 4 只，请求 AI 给出简要理由
    """
    from app.config.settings import AI_TRADE_MAX_TOKENS
    from app.utils.ai_helpers import build_principles_summary

    if not scored_stocks:
        return ''

    # 取 Top 4 和 Bottom 4
    top = scored_stocks[:4]
    bottom = scored_stocks[-4:] if len(scored_stocks) > 4 else []

    # 计算估值数据
    try:
        from app.services.valuation_service import valuate_all_stocks
        _val_map = {v['symbol']: v for v in valuate_all_stocks()}
    except Exception:
        _val_map = {}

    def _stock_summary(s: Dict) -> str:
        dims = s.get('dimensions', {})
        dim_text = ', '.join(
            f"{d['label']}:{d['score']}" for d in dims.values()
        )
        # 估值信息
        val = _val_map.get(s['symbol'])
        val_text = ''
        if val:
            iv = val.get('composite', {}).get('intrinsic_value')
            mos_pct = val.get('margin_of_safety', {}).get('pct')
            if iv and mos_pct is not None:
                val_text = f", 内在价值${iv:.0f}(MoS{mos_pct:+.0f}%)"
        holding_text = ''
        if s.get('holding'):
            h = s['holding']
            holding_text = f"持仓{h.get('net_shares', 0)}股, 盈亏{h.get('unrealized_pnl_pct', 0):.1f}%"
        return f"{s['symbol']}({s.get('stock_name','')}): 总分{s['total_score']}, [{dim_text}]{val_text}, {holding_text}"

    top_text = '\n'.join(_stock_summary(s) for s in top)
    bottom_text = '\n'.join(_stock_summary(s) for s in bottom) if bottom else '（不足4只）'

    principles = build_principles_summary()

    prompt = f"""你是一名基本面投资助手。以下是根据规则评分系统选出的股票评分结果。

【评分最高 Top 4 — 建议关注/买入】
{top_text}

【评分最低 Bottom 4 — 建议警惕/卖出】
{bottom_text}

【用户投资原则】
{principles}

请：
1. 对每只股票用 1 句话（20字内）给出操作理由
2. 用 1 句话总结当前组合整体情况
3. 如果某只股票的操作与用户投资原则矛盾，指出来

返回 JSON 格式：
{{"stock_reasons": {{"AAPL": "估值合理，现金流充裕", ...}}, "portfolio_summary": "一句话总结"}}"""

    try:
        from app.services.ai_client import create_message
        return create_message(
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=AI_TRADE_MAX_TOKENS,
        )
    except Exception as e:
        logger.error(f"generate_ai_recommendations 失败: {e}")
        return ''


def _init_ai_holdings_from_user():
    """
    如果 AI 没有任何交易记录，用用户当前持仓初始化 AI 持仓。
    只执行一次（首次调用时）。
    同时保存 ai_starting_cash，使 AI 起始资产 == 用户真实资产。
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.user_setting import UserSetting
    from app.services.portfolio_service import compute_holdings, compute_user_cash
    from datetime import date as date_type, timedelta

    existing = db_session.query(AiTradeRecord).filter_by(trader='scorer').first()
    if existing:
        return  # 已有记录，不需要初始化

    user_holdings = compute_holdings()
    if not user_holdings:
        return

    # 用昨天的日期作为基准，不影响今天的 AI 交易决策
    yesterday = date_type.today() - timedelta(days=1)
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
            reason='初始化：与用户持仓同步',
            trader='scorer',
        )
        db_session.add(record)
        ai_buy_total += shares * price

    # 保存 ai_starting_cash = user_cash + ai_buy_total
    # 使 AI 现金余额 == 用户现金余额（包含用户已实现盈亏）
    user_cash = compute_user_cash()
    ai_starting_cash = round(user_cash + ai_buy_total, 2)
    row = db_session.query(UserSetting).filter_by(key='ai_starting_cash').first()
    if row:
        row.value = str(ai_starting_cash)
    else:
        db_session.add(UserSetting(key='ai_starting_cash', value=str(ai_starting_cash)))

    db_session.commit()
    logger.info(f"[AI Trades] 从用户持仓初始化, ai_starting_cash={ai_starting_cash:.2f}")


def compute_ai_holdings() -> Dict[str, Dict]:
    """
    从 ai_trade_records 聚合计算 AI 的累计持仓和现金余额。
    首次调用时自动从用户持仓初始化。
    返回 { "AAPL": {"shares": 50, "avg_cost": 150.5}, ... }
    同时在返回的 dict 上附加 _cash 属性（通过 compute_ai_cash() 获取）。
    """
    from app.models.ai_trade_record import AiTradeRecord

    # 首次自动初始化
    _init_ai_holdings_from_user()

    records = db_session.query(AiTradeRecord).filter(AiTradeRecord.trader == 'scorer').order_by(AiTradeRecord.trade_date).all()
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

    # 只返回有持仓的
    return {
        sym: {'shares': h['shares'], 'avg_cost': round(h['total_cost'] / h['shares'], 4) if h['shares'] > 0 else 0}
        for sym, h in holdings.items() if h['shares'] > 0
    }


def compute_ai_cash() -> float:
    """
    计算 AI 模拟账户的独立现金余额。
    优先使用 ai_starting_cash（重置时保存，保证起点与用户一致），
    否则 fallback 到 total_capital。
    """
    from app.models.ai_trade_record import AiTradeRecord
    from app.models.user_setting import UserSetting

    # 优先用 ai_starting_cash（重置时保存，包含用户已实现盈亏）
    try:
        row = db_session.query(UserSetting).filter_by(key='ai_starting_cash').first()
        if row:
            starting_cash = float(row.value)
        else:
            row = db_session.query(UserSetting).filter_by(key='total_capital').first()
            starting_cash = float(row.value) if row else 0
    except Exception:
        starting_cash = 0

    if starting_cash <= 0:
        return 0.0

    records = db_session.query(AiTradeRecord).filter(AiTradeRecord.trader == 'scorer').order_by(AiTradeRecord.trade_date).all()
    cash = starting_cash
    for r in records:
        if r.action == 'buy':
            cash -= r.shares * r.price
        elif r.action == 'sell':
            cash += r.shares * r.price
    return round(cash, 2)


def generate_ai_trades(scored_stocks: List[Dict]) -> Dict:
    """
    AI 根据可用现金和新闻，给出每只股票的具体买卖数量建议。
    执行后将交易保存到 ai_trade_records 表。
    返回 { "AAPL": {"action": "buy", "shares": 10, "reason": "..."}, ... }
    """
    from app.config.settings import AI_TRADE_MAX_TOKENS
    from app.services.news_analysis_service import build_news_analysis_summary
    from app.models.user_setting import UserSetting
    from app.models.ai_trade_record import AiTradeRecord
    from datetime import date as date_type

    if not scored_stocks:
        return {}

    # 检查今天是否已执行过 AI 交易（排除初始化/重置记录）
    today = date_type.today()
    today_records = db_session.query(AiTradeRecord).filter(
        AiTradeRecord.trader == 'scorer',
        AiTradeRecord.trade_date == today,
        ~AiTradeRecord.reason.like('%初始化%'),
        ~AiTradeRecord.reason.like('%重置%'),
    ).all()
    if today_records:
        return {
            r.symbol: {'action': r.action, 'shares': r.shares, 'reason': r.reason or ''}
            for r in today_records if r.action in ('buy', 'sell')
        }

    # 获取总资金
    try:
        row = db_session.query(UserSetting).filter_by(key='total_capital').first()
        total_capital = float(row.value) if row else 0
    except Exception:
        total_capital = 0

    if total_capital <= 0:
        return {}

    # AI 当前持仓
    ai_holdings = compute_ai_holdings()

    # 计算 AI 持仓总价值和可用现金
    # 用股票的当前价格来算
    price_map = {}
    missing_price_symbols = []
    for s in scored_stocks:
        h = s.get('holding')
        if h and h.get('current_price'):
            price_map[s['symbol']] = h['current_price']
        else:
            missing_price_symbols.append(s['symbol'])
    if missing_price_symbols:
        try:
            for stock in db_session.query(Stock).filter(Stock.symbol.in_(missing_price_symbols)).all():
                if stock.current_price:
                    price_map[stock.symbol] = stock.current_price
        except Exception:
            pass

    ai_portfolio_value = sum(
        ai_holdings.get(sym, {}).get('shares', 0) * price_map.get(sym, 0)
        for sym in ai_holdings
    )
    ai_available_cash = compute_ai_cash()
    if ai_available_cash < 0:
        ai_available_cash = 0

    # 计算估值数据，供 _stock_line() 和交易规则使用
    try:
        from app.services.valuation_service import valuate_all_stocks
        val_map = {v['symbol']: v for v in valuate_all_stocks()}
    except Exception as e:
        logger.warning(f"generate_ai_trades 估值计算失败(不影响交易): {e}")
        val_map = {}

    def _stock_line(s: Dict) -> str:
        price = price_map.get(s['symbol'], 0)
        # 估值信息
        val = val_map.get(s['symbol'])
        if val:
            iv = val.get('composite', {}).get('intrinsic_value')
            mos = val.get('margin_of_safety', {})
            mos_pct = mos.get('pct')
            mos_label = mos.get('signal_label', '')
            if iv and mos_pct is not None:
                val_info = f", 内在价值${iv:.0f}(MoS{mos_pct:+.0f}%{mos_label})"
            else:
                val_info = ", 内在价值:N/A"
        else:
            val_info = ", 内在价值:N/A"
        # AI持仓信息
        ai_h = ai_holdings.get(s['symbol'], {})
        ai_shares = ai_h.get('shares', 0)
        ai_cost = ai_h.get('avg_cost', 0)
        if ai_shares > 0 and ai_cost > 0 and price > 0:
            pnl_pct = (price - ai_cost) / ai_cost * 100
            holding_info = f", AI持仓{ai_shares}股(成本${ai_cost:.2f}, 盈亏{pnl_pct:+.1f}%)"
        elif ai_shares > 0:
            holding_info = f", AI持仓{ai_shares}股"
        else:
            holding_info = ", AI未持仓(不可卖出)"
        return f"{s['symbol']}({s.get('stock_name','')}): 质量评分{s['total_score']}, 现价${price}{val_info}{holding_info}"

    stocks_text = '\n'.join(_stock_line(s) for s in scored_stocks)
    from app.utils.ai_helpers import build_principles_summary
    news = build_news_analysis_summary()
    principles = build_principles_summary()

    prompt = f"""你是一名严格遵守投资纪律的价值投资基金经理，管理一个模拟投资组合。
你崇尚巴菲特式的耐心投资——大部分时间持仓不动，只在确信机会出现或风险暴露时才果断行动。
如果今天没有必须交易的理由，最好的操作就是什么都不做。

【核心投资原则 — 必须严格遵守，任何交易不得违反】
{principles}

以上是你的最高行为准则。每一笔交易必须完全符合这些原则，不符合的交易宁可不做。
如果某只股票不满足任何一条硬性要求（如PE上限、连续盈利年限、市值门槛等），绝对不能买入。

【AI 模拟账户信息】
总资金: ${total_capital:,.0f}
AI持仓总价值: ${ai_portfolio_value:,.0f}
AI可用现金: ${ai_available_cash:,.0f}

【股票池评分及AI当前持仓】
{stocks_text}

【近期新闻分析】
{news}

【交易纪律】
1. 投资原则是最优先的决策依据，任何交易不得违反任何一条原则
2. 保持克制，不要频繁交易。只在有充分理由时才操作，大多数时候应该"不操作"
3. 单次交易金额不超过总资金的10%，避免过度集中
4. 买入必须同时满足：(a) 符合所有投资原则 (b) 质量评分≥60 (c) 有新闻或数据支撑
5. 内在价值和安全边际(MoS)仅作参考，不作为硬性买卖条件（该估值模型仍在验证中，可能不准确）
6. 卖出条件：(a) 持仓违反投资原则应清仓 (b) 基本面恶化有具体证据 (c) 达到止损条件
7. 没有充分理由时返回空交易 {{"trades": {{}}}}，不操作是完全合理的
8. 买入不能超过AI可用现金，卖出不能超过AI当前持仓
9. shares 为正整数
10. reason（30-50字）必须引用具体的原则条款、评分数据或新闻

只返回 JSON，格式：
{{"trades": {{"AAPL": {{"action": "buy", "shares": 10, "reason": "质量75分，符合原则X，新品推动增长预期"}}, "TSLA": {{"action": "sell", "shares": 5, "reason": "违反原则Y（PE>40且增速<30%），获利了结"}}}}}}"""

    try:
        from app.services.ai_client import create_message
        raw = create_message(
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=AI_TRADE_MAX_TOKENS,
        )
        from app.utils.ai_helpers import parse_ai_json_response
        result = parse_ai_json_response(raw)
        if not isinstance(result, dict):
            return {}
        trades = result.get('trades', result)

        # 保存 AI 交易记录到数据库
        # 校验并保存交易记录
        valid_trades = {}
        for symbol, trade in trades.items():
            action = trade.get('action', '')
            raw_shares = trade.get('shares', 0)
            try:
                shares = float(raw_shares)
            except (ValueError, TypeError):
                continue
            if action not in ('buy', 'sell') or shares <= 0:
                continue
            price = price_map.get(symbol, 0)
            if price <= 0:
                continue
            # 硬性校验：卖出不能超过AI实际持仓
            if action == 'sell':
                ai_h = ai_holdings.get(symbol, {})
                ai_shares = ai_h.get('shares', 0)
                if ai_shares <= 0:
                    logger.warning(f"[AI Trades] 拒绝卖出 {symbol}：AI未持仓")
                    continue
                if shares >= ai_shares:
                    # 清仓：卖出全部持仓（含小数股）
                    shares = ai_shares
            # 硬性校验：买入不能超过可用现金
            if action == 'buy':
                max_shares = int(ai_available_cash / price) if price > 0 else 0
                if shares > max_shares:
                    logger.warning(f"[AI Trades] {symbol} 买入 {shares} 股超过现金限制，截断为 {max_shares}")
                    shares = max_shares
                if shares <= 0:
                    continue
                ai_available_cash -= shares * price  # 扣减可用现金，防止后续买入超额
            valid_trades[symbol] = {'action': action, 'shares': shares, 'reason': trade.get('reason', '')}
            record = AiTradeRecord(
                symbol=symbol,
                action=action,
                shares=shares,
                price=price,
                trade_date=today,
                reason=trade.get('reason', ''),
                trader='scorer',
            )
            db_session.add(record)

        if not valid_trades:
            # 所有交易都被校验拒绝或AI本身返回空 — 记录hold
            db_session.add(AiTradeRecord(
                symbol='_HOLD',
                action='hold',
                shares=0,
                price=0,
                trade_date=today,
                reason='今日无操作：严格遵守投资原则，无符合条件的交易机会',
                trader='scorer',
            ))
            logger.info("[AI Trades] AI 决定今日不交易")

        db_session.commit()
        if valid_trades:
            logger.info(f"[AI Trades] 保存 {len(valid_trades)} 条 AI 交易记录")

        return trades
    except Exception as e:
        db_session.rollback()
        logger.error(f"generate_ai_trades 失败: {e}")
        return {}
