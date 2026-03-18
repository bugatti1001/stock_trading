"""
Valuation Service — 内在价值估值引擎
5 种估值方法交叉验证，巴菲特式价值投资

方法：
  1. DCF 自由现金流折现 — 主估值方法
  2. EPV 盈利能力价值 — 零增长保守估值
  3. Graham 格雷厄姆公式 — 快速校验
  4. NAV 净资产价值 — 价值底线
  5. DDM 股息折现 — 仅限分红股
"""
import copy
import json
import logging
import math
from typing import Dict, List, Optional, Any

from app.config.database import db_session
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.models.user_setting import UserSetting
from app.services.kpi_calculator import compute_single_period_kpis, compute_multi_period_kpis
from app.services.stock_analysis_service import batch_load_recent_financials

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
#  默认估值参数
# ════════════════════════════════════════════════════════════════

DEFAULT_VALUATION_PARAMS: Dict[str, float] = {
    'discount_rate': 0.085,           # 折现率（8.5%：主流投行大盘股基准，参考GS/MS）
    'terminal_growth_rate': 0.03,     # 终端增长率（3%：科技驱动的长期GDP+）
    'growth_cap': 0.22,               # 增长率上限（22%：允许高成长公司合理估值）
    'bond_yield': 0.045,              # Graham公式企业债收益率（当前市场水平）
    'dcf_projection_years': 10,       # DCF预测年数
    'margin_of_safety': 0.25,         # 买入安全边际门槛
    'quality_score_min': 60,          # 最低质量评分
    'sell_overvaluation_pct': 0.20,   # 卖出高估门槛
    'ai_growth_premium': 0.03,        # AI赛道增长溢价（基础增长率已提高，溢价适度降低）
    'geo_risk_premium': 0.03,         # 中概股地缘政治风险溢价（加到折现率）
}

PARAM_LABELS: Dict[str, str] = {
    'discount_rate': '折现率',
    'terminal_growth_rate': '终端增长率',
    'growth_cap': '增长率上限',
    'bond_yield': '企业债收益率',
    'dcf_projection_years': 'DCF预测年数',
    'margin_of_safety': '买入安全边际',
    'quality_score_min': '最低质量评分',
    'sell_overvaluation_pct': '卖出高估门槛',
    'ai_growth_premium': 'AI赛道增长溢价',
    'geo_risk_premium': '中概股风险溢价',
}

PARAM_VALIDATION: Dict[str, Dict] = {
    'discount_rate':          {'min': 0.05, 'max': 0.15, 'type': float},
    'terminal_growth_rate':   {'min': 0.01, 'max': 0.05, 'type': float},
    'growth_cap':             {'min': 0.10, 'max': 0.35, 'type': float},
    'bond_yield':             {'min': 0.03, 'max': 0.08, 'type': float},
    'dcf_projection_years':   {'min': 5,    'max': 15,   'type': int},
    'margin_of_safety':       {'min': 0.10, 'max': 0.50, 'type': float},
    'quality_score_min':      {'min': 40,   'max': 80,   'type': int},
    'sell_overvaluation_pct': {'min': 0.10, 'max': 0.50, 'type': float},
    'ai_growth_premium':      {'min': 0.00, 'max': 0.15, 'type': float},
    'geo_risk_premium':       {'min': 0.00, 'max': 0.10, 'type': float},
}

# 公司类型对应的方法权重
METHOD_WEIGHTS: Dict[str, Dict[str, float]] = {
    'mature':      {'dcf': 0.35, 'epv': 0.25, 'graham': 0.20, 'nav': 0.10, 'ddm': 0.10},
    'growth':      {'dcf': 0.45, 'epv': 0.20, 'graham': 0.20, 'nav': 0.05, 'ddm': 0.10},
    'asset_heavy': {'dcf': 0.20, 'epv': 0.20, 'graham': 0.10, 'nav': 0.40, 'ddm': 0.10},
    'dividend':    {'dcf': 0.25, 'epv': 0.20, 'graham': 0.15, 'nav': 0.10, 'ddm': 0.30},
}

# ════════════════════════════════════════════════════════════════
#  股票特征标签 — 用于风险/增长溢价判定
# ════════════════════════════════════════════════════════════════

# 中概股 ADR：VIE架构 + 地缘政治风险 → 折现率加 geo_risk_premium
CHINA_ADR_SYMBOLS = frozenset({
    'BABA', 'PDD', 'JD', 'NIO', 'XPEV', 'LI', 'BIDU', 'BILI',
    'TCEHY', 'XIACY', 'TME', 'IQ', 'VNET', 'WB', 'ZH', 'MNSO',
    'EDU', 'TAL', 'HSAI', 'TCOM', 'FUTU', 'TIGR', 'YMM', 'DADA',
})

# AI 受益赛道（sector 或 industry 包含这些关键词的股票获得 ai_growth_premium）
AI_SECTOR_KEYWORDS = frozenset({
    'Semiconductors', 'Technology', 'Media',    # sector 级别
})
# 即使 sector 匹配，也需要排除明显不受 AI 驱动的个股
AI_EXCLUDE_SYMBOLS = frozenset({
    'NOK', 'NFLX',   # 通信设备、流媒体 — AI不是核心增长驱动
})
# 不在上述sector里，但确定受益AI的个股（跨行业AI受益者）
AI_INCLUDE_SYMBOLS = frozenset({
    'GEV', 'AMSC',   # 电气设备/能源 — AI数据中心电力需求
})

# 轻资产行业 — 这些行业的"非流动资产"多为商誉和无形资产，
# 不属于传统重资产行业，NAV估值参考意义极低
LIGHT_ASSET_SECTORS = frozenset({
    'Technology', 'Media', 'Semiconductors',
    'Diversified Consumer Services',
})


def is_china_adr(stock: Stock) -> bool:
    """判断是否为中概股 ADR（需加地缘政治风险溢价）"""
    if stock.symbol in CHINA_ADR_SYMBOLS:
        return True
    # 港股 / A股市场
    if stock.market in ('HK', 'CN'):
        return True
    return False


def is_ai_beneficiary(stock: Stock) -> bool:
    """判断是否为 AI 赛道受益股（可获得增长溢价）"""
    if stock.symbol in AI_EXCLUDE_SYMBOLS:
        return False
    if stock.symbol in AI_INCLUDE_SYMBOLS:
        return True
    sector = stock.sector or ''
    return sector in AI_SECTOR_KEYWORDS


# ════════════════════════════════════════════════════════════════
#  数据预处理 — 货币转换 & 股本校正
# ════════════════════════════════════════════════════════════════

# 雪球数据源的中概股财报数据实际是人民币(CNY)但 currency 字段标记为 USD
# 需要自动检测并转换为 USD 以保证估值准确
CNY_USD_RATE = 7.2   # 人民币/美元汇率（近似值，用于估值级别的转换足够）

# 需要进行 CNY→USD 转换的财务字段
_FINANCIAL_FIELDS_TO_CONVERT = (
    'revenue', 'cost_of_revenue', 'operating_income', 'net_income',
    'net_income_to_parent', 'adjusted_net_income',
    'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
    'cash_and_equivalents', 'accounts_receivable', 'inventory',
    'investments', 'accounts_payable',
    'short_term_borrowings', 'long_term_borrowings',
    'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
    'operating_cash_flow', 'capital_expenditure',
)


def _needs_cny_conversion(stock: Stock, fins: List[FinancialData]) -> bool:
    """
    判断中概股 ADR 的财务数据是否需要 CNY→USD 转换。
    雪球数据源的中概股财报以人民币报告，但 currency 字段标为 USD。

    检测逻辑：
    1. 必须是已知的中概股 ADR
    2. 数据源为雪球 (Xueqiu)
    3. nav_per_share 与股价的比值异常高（表明 nav_per_share 也是人民币）
    """
    if not is_china_adr(stock):
        return False
    if not fins:
        return False
    # 检查数据源
    latest = fins[0]
    if latest.data_source and latest.data_source.lower() == 'xueqiu':
        return True
    # 兜底检测：如果 nav_per_share 远高于股价（人民币 vs 美元）
    if (latest.nav_per_share and stock.current_price and stock.current_price > 0
            and latest.total_equity and latest.total_equity > 0):
        # 对于正常的美元数据，nav_per_share / price 通常 < 5
        # 对于 CNY 数据，比值会 ≈ 7 倍偏高
        bv_ratio = latest.nav_per_share / stock.current_price
        if bv_ratio > 3.0:
            return True
    return False


def _fix_shares_outstanding(fin: FinancialData) -> Optional[float]:
    """
    校正股本数。当 shares_outstanding 与 equity/nav_per_share 不一致时，
    使用 nav_per_share 反推正确的股本数。

    仅在差异非常显著（>5x）时才校正，避免因 nav_per_share 计算口径差异
    导致的误校正（如股票拆分、不同会计期间等）。

    雪球部分中概股（如 HSAI）的 shares_outstanding 存在单位错误（100x 偏高）。
    """
    shares = fin.shares_outstanding
    nav_ps = fin.nav_per_share
    equity = fin.total_equity

    if not shares or shares <= 0:
        return None
    if not nav_ps or nav_ps <= 0 or not equity or equity <= 0:
        return shares

    calc_shares = equity / nav_ps
    ratio = shares / calc_shares

    # 比值在 0.2-5x 范围内视为正常（允许拆分、会计差异等）
    if 0.2 <= ratio <= 5.0:
        return shares

    # 比值严重异常（通常为 10x, 100x），使用反推值
    logger.info(f"股本校正: DB={shares:,.0f} 反推={calc_shares:,.0f} 比值={ratio:.1f}x")
    return calc_shares


def _compute_annualization_factor(
    stock: Stock,
    fin: FinancialData,
) -> Optional[float]:
    """
    通过对比 Stock 模型的 EPS（来自行情数据，较准确的 TTM 值）和
    财报计算的 EPS，检测财务数据是否为非年度（季度/半年度）数据。

    仅当 quote_eps > calc_eps（财报数据偏低）时才返回向上校正因子。
    如果 quote_eps ≤ calc_eps（数据已充足），不做校正。

    原理：行情 EPS 是 TTM（最近12个月），如果财报 EPS 显著低于
    行情 EPS，说明财报只覆盖了部分年度，需要向上年化。
    """
    quote_eps = stock.eps
    shares = fin.shares_outstanding

    if not quote_eps or not shares or shares <= 0 or not fin.net_income:
        return None

    calc_eps = fin.net_income / shares

    # 只有两者均为正数才能校准（亏损公司无法比较）
    if calc_eps <= 0 or quote_eps <= 0:
        return None

    ratio = quote_eps / calc_eps

    # 比值 ≤ 1.4 表示数据基本完整，无需向上校正
    if ratio <= 1.4:
        return None

    # 比值在 1.4-4.0 范围内，合理的年化校正（半年→年、单季→年）
    if ratio > 4.0:
        return None  # 差异太大，可能是数据源差异而非周期问题

    return ratio


def _preprocess_financials(
    stock: Stock,
    fins: List[FinancialData],
) -> tuple:
    """
    预处理财务数据，修正已知的数据质量问题：
    1. 中概股 ADR 的 CNY→USD 货币转换
    2. 股本数异常校正
    3. 非年度数据自动年化（通过 EPS 比值检测）

    Returns:
        (processed_fins, data_warnings) — 处理后的数据列表 + 警告信息
    """
    warnings = []

    if not fins:
        return fins, warnings

    need_cny = _needs_cny_conversion(stock, fins)

    processed = []
    for fin in fins:
        # 浅拷贝，避免修改原始 ORM 对象
        pf = copy.copy(fin)

        # ── Step 1: CNY → USD 转换 ──
        if need_cny:
            for field in _FINANCIAL_FIELDS_TO_CONVERT:
                val = getattr(pf, field, None)
                if val is not None:
                    setattr(pf, field, val / CNY_USD_RATE)
            # nav_per_share 也是人民币
            if pf.nav_per_share is not None:
                pf.nav_per_share = pf.nav_per_share / CNY_USD_RATE
            # dividends_per_share 也需要转换
            if pf.dividends_per_share is not None and pf.dividends_per_share > 0:
                pf.dividends_per_share = pf.dividends_per_share / CNY_USD_RATE

        # ── Step 2: 股本校正（在货币转换之后） ──
        corrected_shares = _fix_shares_outstanding(pf)
        if corrected_shares and pf.shares_outstanding:
            ratio = pf.shares_outstanding / corrected_shares
            if ratio > 5.0 or ratio < 0.2:
                pf.shares_outstanding = corrected_shares
                # 仅在首次出现时记录警告（避免多期重复）
                warn_msg = f'股本已校正({ratio:.0f}x偏差)'
                if warn_msg not in warnings:
                    warnings.append(warn_msg)

        processed.append(pf)

    if need_cny:
        warnings.append(f'财报CNY→USD(÷{CNY_USD_RATE})')
        logger.info(f"{stock.symbol}: 应用 CNY→USD 转换 (÷{CNY_USD_RATE})")

    # ── Step 3: EPS自校准 — 检测非年度数据并年化 ──
    # 仅对最新一期做检测，校正因子应用到所有期次
    if processed:
        ann_factor = _compute_annualization_factor(stock, processed[0])
        if ann_factor is not None:
            warnings.append(f'数据年化(×{ann_factor:.2f})')
            logger.info(f"{stock.symbol}: 应用年化校正 ×{ann_factor:.2f} "
                        f"(quote_eps={stock.eps}, calc_eps={processed[0].net_income/(processed[0].shares_outstanding or 1):.2f})")
            for pf in processed:
                for field in _FINANCIAL_FIELDS_TO_CONVERT:
                    val = getattr(pf, field, None)
                    if val is not None:
                        setattr(pf, field, val * ann_factor)
                # nav_per_share 和 dividends_per_share 不需要年化
                # （它们是"每股值"而非"总量"，年化不影响）
                # 但 dividends_per_share 可能也需要年化（如果是季度分红）
                # 保守起见先不动

    return processed, warnings


# ════════════════════════════════════════════════════════════════
#  参数管理
# ════════════════════════════════════════════════════════════════

def get_valuation_params() -> Dict[str, Any]:
    """获取当前用户的估值参数，未设置则返回默认值"""
    try:
        row = db_session.query(UserSetting).filter_by(key='valuation_params').first()
        if row and row.value:
            params = json.loads(row.value)
            for k, v in DEFAULT_VALUATION_PARAMS.items():
                if k not in params:
                    params[k] = v
            return params
    except Exception as e:
        logger.warning(f"读取估值参数失败: {e}")
    return dict(DEFAULT_VALUATION_PARAMS)


def save_valuation_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """保存并验证估值参数"""
    validated = {}
    for k, default_val in DEFAULT_VALUATION_PARAMS.items():
        val = params.get(k, default_val)
        rules = PARAM_VALIDATION.get(k, {})
        try:
            val = rules.get('type', float)(val)
        except (ValueError, TypeError):
            val = default_val
        val = max(rules.get('min', val), min(rules.get('max', val), val))
        validated[k] = val

    row = db_session.query(UserSetting).filter_by(key='valuation_params').first()
    if row:
        row.value = json.dumps(validated)
    else:
        db_session.add(UserSetting(key='valuation_params', value=json.dumps(validated)))
    db_session.commit()
    return validated


# ════════════════════════════════════════════════════════════════
#  增长率估算
# ════════════════════════════════════════════════════════════════

def _estimate_growth_rate(fins: List[FinancialData], params: Dict) -> Dict[str, Any]:
    """
    从历史财务数据估算增长率
    取收入CAGR和FCF增长率的较低值，并设上限
    """
    growth_cap = params.get('growth_cap', 0.15)
    result = {
        'revenue_cagr': None,
        'earnings_cagr': None,
        'fcf_growth': None,
        'used_rate': 0.0,
        'method': 'default',
    }

    if not fins or len(fins) < 2:
        return result

    # 收入CAGR：最新 vs 最早
    newest = fins[0]
    oldest = fins[-1]
    n_years = len(fins) - 1
    if n_years <= 0:
        return result

    if (newest.revenue and oldest.revenue
            and newest.revenue > 0 and oldest.revenue > 0):
        try:
            rev_cagr = (newest.revenue / oldest.revenue) ** (1 / n_years) - 1
            result['revenue_cagr'] = round(rev_cagr, 4)
        except (ValueError, ZeroDivisionError):
            pass

    # 净利润CAGR
    if (newest.net_income and oldest.net_income
            and newest.net_income > 0 and oldest.net_income > 0):
        try:
            ni_cagr = (newest.net_income / oldest.net_income) ** (1 / n_years) - 1
            result['earnings_cagr'] = round(ni_cagr, 4)
        except (ValueError, ZeroDivisionError):
            pass

    # FCF增长率
    if len(fins) >= 2:
        multi_kpis = compute_multi_period_kpis(fins)
        avg_fcf = multi_kpis.get('avg_fcf_3y')
        if avg_fcf and avg_fcf > 0 and oldest.operating_cash_flow is not None:
            old_capex = abs(oldest.capital_expenditure or 0)
            old_fcf = (oldest.operating_cash_flow or 0) - old_capex
            if old_fcf > 0:
                try:
                    fcf_growth = (avg_fcf / old_fcf) ** (1 / n_years) - 1
                    result['fcf_growth'] = round(fcf_growth, 4)
                except (ValueError, ZeroDivisionError):
                    pass

    # ── 选取增长率：排除FCF异常值后取中位数 ──
    # 当公司处于重投资期（AI基础设施、产能扩张等），
    # 高capex导致FCF增长率远低于收入/利润增长，不应拖累估值
    rev_earn = [v for v in [result['revenue_cagr'], result['earnings_cagr']]
                if v is not None and v > 0]
    fcf_g = result.get('fcf_growth')

    if (fcf_g is not None and fcf_g > 0 and len(rev_earn) >= 1
            and fcf_g < min(rev_earn) * 0.3 and fcf_g < 0.08):
        # FCF增长率是极端异常值（不到收入/利润增长的30%且<8%）
        # 通常意味着重capex投资期，排除后取中位数
        candidates = rev_earn
        result['fcf_excluded'] = True
    else:
        candidates = [v for v in [result['revenue_cagr'], result['earnings_cagr'], fcf_g]
                      if v is not None and v > 0]

    if candidates:
        candidates.sort()
        chosen = candidates[len(candidates) // 2]  # 中位数
        chosen = min(chosen, growth_cap)  # 上限
        result['used_rate'] = round(chosen, 4)
        result['method'] = 'historical_median'
    else:
        # 无正增长数据，使用0（零增长）
        result['used_rate'] = 0.0
        result['method'] = 'zero_growth'

    return result


# ════════════════════════════════════════════════════════════════
#  公司类型判定
# ════════════════════════════════════════════════════════════════

def _classify_company(stock: Stock, fins: List[FinancialData], growth_info: Dict) -> str:
    """判定公司类型：growth / asset_heavy / dividend / mature"""
    # 成长型：收入CAGR > 12%（之前15%阈值过高，GOOG 14.5%被误分为成熟型）
    rev_cagr = growth_info.get('revenue_cagr')
    if rev_cagr is not None and rev_cagr > 0.12:
        return 'growth'

    # 高分红型：股息率 > 2%（数据库存储已是百分比形式，如2.5表示2.5%）
    dy = stock.dividend_yield
    if dy is not None and dy > 2.0:
        return 'dividend'

    # 重资产型：非流动资产 > 60% 总资产
    # 注意：科技/互联网/半导体公司的"非流动资产"多为商誉和无形资产，
    # 不属于传统重资产行业（能源、公用事业、矿业等），应排除
    if fins:
        latest = fins[0]
        if (latest.non_current_assets is not None
                and latest.total_assets is not None
                and latest.total_assets > 0):
            if latest.non_current_assets / latest.total_assets > 0.6:
                sector = stock.sector or ''
                if sector not in LIGHT_ASSET_SECTORS:
                    return 'asset_heavy'

    return 'mature'


# ════════════════════════════════════════════════════════════════
#  估值方法 1: DCF 自由现金流折现
# ════════════════════════════════════════════════════════════════

def _dcf_valuation(
    stock: Stock,
    fins: List[FinancialData],
    params: Dict,
    growth_info: Dict,
) -> Optional[Dict[str, Any]]:
    """
    DCF估值：基于平均FCF，两阶段增长模型
    Stage 1 (前半): 按估算增长率
    Stage 2 (后半): 增长率减半
    Terminal: 永续增长
    """
    if not fins:
        return None

    multi_kpis = compute_multi_period_kpis(fins)
    avg_fcf = multi_kpis.get('avg_fcf_3y')
    if avg_fcf is None or avg_fcf <= 0:
        return None

    latest = fins[0]
    shares = latest.shares_outstanding
    if not shares or shares <= 0:
        return None

    single_kpis = compute_single_period_kpis(latest)
    net_cash = single_kpis.get('net_cash', 0) or 0

    discount_rate = params.get('discount_rate', 0.10)
    terminal_growth = params.get('terminal_growth_rate', 0.025)
    projection_years = int(params.get('dcf_projection_years', 10))
    growth_rate = growth_info.get('used_rate', 0.0)

    # 确保终端增长率 < 折现率
    terminal_growth = min(terminal_growth, discount_rate - 0.01)

    # 两阶段：前半按growth_rate，后半按growth_rate/2
    half = projection_years // 2
    pv_fcfs = 0.0
    fcf = avg_fcf

    for i in range(1, projection_years + 1):
        if i <= half:
            fcf *= (1 + growth_rate)
        else:
            fcf *= (1 + growth_rate / 2)
        pv_fcfs += fcf / ((1 + discount_rate) ** i)

    # 终值
    terminal_value = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / ((1 + discount_rate) ** projection_years)

    intrinsic_total = pv_fcfs + pv_terminal + net_cash
    intrinsic_per_share = intrinsic_total / shares

    if intrinsic_per_share <= 0:
        return None

    return {
        'intrinsic_value': round(intrinsic_per_share, 2),
        'method': 'DCF',
        'method_cn': 'DCF自由现金流折现',
        'assumptions': {
            'base_fcf': round(avg_fcf, 0),
            'growth_rate': round(growth_rate * 100, 1),
            'discount_rate': round(discount_rate * 100, 1),
            'terminal_growth': round(terminal_growth * 100, 1),
            'projection_years': projection_years,
            'net_cash': round(net_cash, 0),
        },
        'confidence': 'high' if len(fins) >= 3 and growth_rate > 0 else 'medium',
        'warnings': [],
    }


# ════════════════════════════════════════════════════════════════
#  估值方法 2: EPV 盈利能力价值
# ════════════════════════════════════════════════════════════════

def _epv_valuation(
    stock: Stock,
    fins: List[FinancialData],
    params: Dict,
) -> Optional[Dict[str, Any]]:
    """
    EPV估值：假设零增长，当前盈利能力的资本化价值
    EPV = 标准化净利润 / 折现率 + 净现金
    """
    if not fins:
        return None

    # 3年平均净利润
    ni_vals = [f.net_income for f in fins[:3] if f.net_income is not None]
    if not ni_vals:
        return None

    avg_ni = sum(ni_vals) / len(ni_vals)
    if avg_ni <= 0:
        return None

    latest = fins[0]
    shares = latest.shares_outstanding
    if not shares or shares <= 0:
        return None

    single_kpis = compute_single_period_kpis(latest)
    net_cash = single_kpis.get('net_cash', 0) or 0

    discount_rate = params.get('discount_rate', 0.10)
    epv_total = avg_ni / discount_rate + net_cash
    epv_per_share = epv_total / shares

    if epv_per_share <= 0:
        return None

    return {
        'intrinsic_value': round(epv_per_share, 2),
        'method': 'EPV',
        'method_cn': 'EPV盈利能力价值',
        'assumptions': {
            'avg_net_income_3y': round(avg_ni, 0),
            'discount_rate': round(discount_rate * 100, 1),
            'net_cash': round(net_cash, 0),
        },
        'confidence': 'high' if len(ni_vals) >= 3 else 'medium',
        'warnings': [],
    }


# ════════════════════════════════════════════════════════════════
#  估值方法 3: Graham 公式
# ════════════════════════════════════════════════════════════════

def _graham_valuation(
    stock: Stock,
    fins: List[FinancialData],
    params: Dict,
    growth_info: Dict,
) -> Optional[Dict[str, Any]]:
    """
    Graham修正公式：V = EPS × (8.5 + 2g) × 4.4 / Y
    """
    if not fins:
        return None

    latest = fins[0]
    shares = latest.shares_outstanding
    if not shares or shares <= 0:
        return None

    ni = latest.net_income
    if ni is None or ni <= 0:
        return None

    eps = ni / shares
    if eps <= 0:
        return None

    growth_pct = growth_info.get('used_rate', 0.0) * 100  # 百分比形式
    growth_pct = min(growth_pct, params.get('growth_cap', 0.15) * 100)
    bond_yield = params.get('bond_yield', 0.05) * 100  # 百分比

    if bond_yield <= 0:
        return None

    intrinsic = eps * (8.5 + 2 * growth_pct) * 4.4 / bond_yield

    if intrinsic <= 0:
        return None

    return {
        'intrinsic_value': round(intrinsic, 2),
        'method': 'Graham',
        'method_cn': 'Graham公式',
        'assumptions': {
            'eps': round(eps, 2),
            'growth_rate_pct': round(growth_pct, 1),
            'bond_yield_pct': round(bond_yield, 1),
        },
        'confidence': 'medium',
        'warnings': [],
    }


# ════════════════════════════════════════════════════════════════
#  估值方法 4: NAV 净资产价值
# ════════════════════════════════════════════════════════════════

def _nav_valuation(
    stock: Stock,
    fins: List[FinancialData],
    params: Dict,
) -> Optional[Dict[str, Any]]:
    """
    净资产估值：账面价值 + 调整NAV
    """
    if not fins:
        return None

    latest = fins[0]
    shares = latest.shares_outstanding
    if not shares or shares <= 0:
        return None

    eq = latest.total_equity
    if eq is None or eq <= 0:
        return None

    book_value_ps = eq / shares

    # 调整NAV：对资产打折
    cash = latest.cash_and_equivalents or 0
    investments = latest.investments or 0
    receivables = latest.accounts_receivable or 0
    inventory = latest.inventory or 0
    nca = latest.non_current_assets or 0
    cl = latest.current_liabilities or 0
    stb = latest.short_term_borrowings or 0
    ltb = latest.long_term_borrowings or 0

    total_liab = cl + ltb  # 流动负债包含短期借款，加上长期借款

    adjusted_assets = (
        cash * 1.0
        + investments * 0.9
        + receivables * 0.8
        + inventory * 0.6
        + nca * 0.5
    )
    adjusted_nav = adjusted_assets - total_liab
    adjusted_nav_ps = adjusted_nav / shares if adjusted_nav > 0 else 0

    # 取较高值作为NAV估值
    nav_value = max(book_value_ps, adjusted_nav_ps)

    if nav_value <= 0:
        return None

    return {
        'intrinsic_value': round(nav_value, 2),
        'method': 'NAV',
        'method_cn': '净资产价值',
        'assumptions': {
            'book_value_per_share': round(book_value_ps, 2),
            'adjusted_nav_per_share': round(adjusted_nav_ps, 2),
        },
        'confidence': 'medium',
        'warnings': ['轻资产公司NAV参考意义有限'] if nca < (latest.total_assets or 1) * 0.3 else [],
    }


# ════════════════════════════════════════════════════════════════
#  估值方法 5: DDM 股息折现
# ════════════════════════════════════════════════════════════════

def _ddm_valuation(
    stock: Stock,
    fins: List[FinancialData],
    params: Dict,
    growth_info: Dict,
) -> Optional[Dict[str, Any]]:
    """
    两阶段股息折现模型
    Phase 1 (5年): 按增长率增长
    Phase 2 (永续): 按终端增长率
    """
    if not fins:
        return None

    latest = fins[0]
    d0 = latest.dividends_per_share
    if d0 is None or d0 <= 0:
        return None

    discount_rate = params.get('discount_rate', 0.10)
    terminal_growth = params.get('terminal_growth_rate', 0.025)
    terminal_growth = min(terminal_growth, discount_rate - 0.01)

    g1 = min(growth_info.get('used_rate', 0.0), params.get('growth_cap', 0.15))

    # Phase 1: 5年
    pv_phase1 = 0.0
    div = d0
    for i in range(1, 6):
        div *= (1 + g1)
        pv_phase1 += div / ((1 + discount_rate) ** i)

    # Phase 2: 永续
    div_6 = div * (1 + terminal_growth)
    terminal_value = div_6 / (discount_rate - terminal_growth)
    pv_phase2 = terminal_value / ((1 + discount_rate) ** 5)

    intrinsic = pv_phase1 + pv_phase2

    if intrinsic <= 0:
        return None

    return {
        'intrinsic_value': round(intrinsic, 2),
        'method': 'DDM',
        'method_cn': '股息折现模型',
        'assumptions': {
            'current_dividend': round(d0, 2),
            'phase1_growth': round(g1 * 100, 1),
            'terminal_growth': round(terminal_growth * 100, 1),
            'discount_rate': round(discount_rate * 100, 1),
        },
        'confidence': 'medium' if g1 > 0 else 'low',
        'warnings': [],
    }


# ════════════════════════════════════════════════════════════════
#  综合估值
# ════════════════════════════════════════════════════════════════

def _adjust_weights_for_characteristics(
    company_type: str,
    stock: Stock,
) -> Dict[str, float]:
    """
    基于股票具体特征动态调整方法权重。

    原则：
    - 轻资产科技公司：NAV（净资产）参考意义极低，价值在IP/品牌/算法
    - 低分红公司：DDM（股息折现）参考意义极低
    - 高成长科技公司：EPV（零增长假设）严重低估实际价值
    - 无效方法的多余权重按 60:40 分配给 DCF 和 Graham
    """
    weights = dict(METHOD_WEIGHTS.get(company_type, METHOD_WEIGHTS['mature']))

    sector = stock.sector or ''
    is_tech = sector in LIGHT_ASSET_SECTORS or is_ai_beneficiary(stock)
    div_yield = stock.dividend_yield or 0

    redistributed = 0.0

    # 轻资产科技公司：NAV缺乏参考价值（价值在IP/品牌/算法，不在固定资产）
    if is_tech and weights.get('nav', 0) > 0.05:
        excess = weights['nav'] - 0.05
        weights['nav'] = 0.05
        redistributed += excess

    # 低分红公司：DDM缺乏参考价值（股息太少，折现结果无意义）
    if div_yield < 1.0 and weights.get('ddm', 0) > 0.05:
        excess = weights['ddm'] - 0.05
        weights['ddm'] = 0.05
        redistributed += excess

    # 科技成长股：EPV（零增长假设）严重低估实际价值
    if is_tech and company_type == 'growth' and weights.get('epv', 0) > 0.10:
        excess = weights['epv'] - 0.10
        weights['epv'] = 0.10
        redistributed += excess

    # 重新分配多余权重给DCF和Graham（60:40）
    if redistributed > 0:
        weights['dcf'] = weights.get('dcf', 0) + redistributed * 0.6
        weights['graham'] = weights.get('graham', 0) + redistributed * 0.4

    return weights


def _compute_composite_value(
    method_results: Dict[str, Optional[Dict]],
    company_type: str,
    weight_overrides: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    加权平均有效估值方法的结果
    无效方法的权重按比例重新分配
    """
    base_weights = weight_overrides if weight_overrides else METHOD_WEIGHTS.get(company_type, METHOD_WEIGHTS['mature'])

    # 筛选有效方法
    valid = {k: v for k, v in method_results.items() if v is not None}
    if not valid:
        return {
            'intrinsic_value': None,
            'method_count': 0,
            'range_low': None,
            'range_high': None,
            'weights_used': {},
        }

    # 重新分配权重
    total_weight = sum(base_weights.get(k, 0) for k in valid)
    if total_weight <= 0:
        # 均分
        adjusted_weights = {k: 1.0 / len(valid) for k in valid}
    else:
        adjusted_weights = {
            k: base_weights.get(k, 0) / total_weight
            for k in valid
        }

    weighted_sum = sum(
        valid[k]['intrinsic_value'] * adjusted_weights[k]
        for k in valid
    )
    values = [v['intrinsic_value'] for v in valid.values()]

    return {
        'intrinsic_value': round(weighted_sum, 2),
        'method_count': len(valid),
        'range_low': round(min(values), 2),
        'range_high': round(max(values), 2),
        'weights_used': {k: round(w, 3) for k, w in adjusted_weights.items()},
    }


def compute_margin_of_safety(
    current_price: Optional[float],
    intrinsic_value: Optional[float],
) -> Dict[str, Any]:
    """
    计算安全边际
    正数 = 低估（好事），负数 = 高估
    """
    if not current_price or not intrinsic_value or intrinsic_value <= 0:
        return {
            'pct': None,
            'signal': 'unknown',
            'signal_label': '无法估值',
            'signal_emoji': '⚫',
            'is_undervalued': False,
        }

    mos_pct = (intrinsic_value - current_price) / intrinsic_value * 100

    return {
        'pct': round(mos_pct, 1),
        'signal': _mos_signal(mos_pct),
        'signal_label': _mos_label(mos_pct),
        'signal_emoji': _mos_emoji(mos_pct),
        'is_undervalued': mos_pct > 0,
    }


def _mos_signal(pct: float) -> str:
    if pct >= 25:
        return 'strong_buy'
    elif pct >= 10:
        return 'buy'
    elif pct >= -10:
        return 'fair'
    elif pct >= -20:
        return 'overvalued'
    else:
        return 'very_overvalued'


def _mos_label(pct: float) -> str:
    if pct >= 25:
        return '显著低估'
    elif pct >= 10:
        return '低估'
    elif pct >= -10:
        return '合理'
    elif pct >= -20:
        return '偏高'
    else:
        return '显著高估'


def _mos_emoji(pct: float) -> str:
    if pct >= 25:
        return '🟢'
    elif pct >= 10:
        return '🟢'
    elif pct >= -10:
        return '⚪'
    elif pct >= -20:
        return '🟡'
    else:
        return '🔴'


# ════════════════════════════════════════════════════════════════
#  单只股票估值
# ════════════════════════════════════════════════════════════════

def valuate_stock(
    stock: Stock,
    fins: List[FinancialData],
    params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    对单只股票执行全部估值方法，返回综合结果

    Returns:
        {
            symbol, stock_name, current_price,
            methods: {dcf: {...}, epv: {...}, ...},
            composite: {intrinsic_value, method_count, range_low, range_high},
            margin_of_safety: {pct, signal, signal_label, ...},
            growth_estimate: {...},
            company_type, company_type_label,
        }
    """
    if params is None:
        params = get_valuation_params()

    # ── 数据预处理：货币转换 & 股本校正 ──
    processed_fins, data_warnings = _preprocess_financials(stock, fins)
    fins = processed_fins  # 后续全部使用处理后的数据

    # ── 风险/增长溢价调整 ──
    stock_is_ai = is_ai_beneficiary(stock)
    stock_is_china = is_china_adr(stock)
    adjusted_params = dict(params)   # 浅拷贝，每只股票独立调整

    # AI赛道：增长率加成（反映AI革命对未来增长的提振）
    ai_premium_applied = 0.0
    if stock_is_ai:
        ai_premium_applied = params.get('ai_growth_premium', 0.05)
        # 增长率上限也相应提升，避免增长溢价被cap住
        adjusted_params['growth_cap'] = params.get('growth_cap', 0.15) + ai_premium_applied

    # 中概股：折现率加成（反映VIE、退市、监管等地缘风险）
    geo_premium_applied = 0.0
    if stock_is_china:
        geo_premium_applied = params.get('geo_risk_premium', 0.03)
        adjusted_params['discount_rate'] = params.get('discount_rate', 0.10) + geo_premium_applied

    growth_info = _estimate_growth_rate(fins, adjusted_params)

    # AI赛道：在历史增长率基础上叠加AI溢价
    if stock_is_ai and ai_premium_applied > 0:
        boosted = growth_info['used_rate'] + ai_premium_applied
        boosted = min(boosted, adjusted_params['growth_cap'])
        growth_info['used_rate'] = round(boosted, 4)
        growth_info['ai_premium'] = round(ai_premium_applied, 4)
        growth_info['method'] = growth_info.get('method', 'default') + '+ai'

    company_type = _classify_company(stock, fins, growth_info)

    method_results = {
        'dcf': _dcf_valuation(stock, fins, adjusted_params, growth_info),
        'epv': _epv_valuation(stock, fins, adjusted_params),
        'graham': _graham_valuation(stock, fins, adjusted_params, growth_info),
        'nav': _nav_valuation(stock, fins, adjusted_params),
        'ddm': _ddm_valuation(stock, fins, adjusted_params, growth_info),
    }

    # 动态调整方法权重（科技公司降低NAV/DDM/EPV权重）
    adjusted_weights = _adjust_weights_for_characteristics(company_type, stock)
    composite = _compute_composite_value(method_results, company_type, adjusted_weights)
    mos = compute_margin_of_safety(stock.current_price, composite.get('intrinsic_value'))

    type_labels = {
        'mature': '成熟型',
        'growth': '成长型',
        'asset_heavy': '重资产型',
        'dividend': '高分红型',
    }

    # 构建标签列表
    tags = []
    if stock_is_ai:
        tags.append(f'AI赛道(增长+{ai_premium_applied:.0%})')
    if stock_is_china:
        tags.append(f'中概股(折现+{geo_premium_applied:.0%})')

    return {
        'symbol': stock.symbol,
        'stock_name': stock.name or stock.symbol,
        'current_price': stock.current_price,
        'methods': method_results,
        'composite': composite,
        'margin_of_safety': mos,
        'growth_estimate': growth_info,
        'company_type': company_type,
        'company_type_label': type_labels.get(company_type, '成熟型'),
        'tags': tags,
        'is_ai_sector': stock_is_ai,
        'is_china_adr': stock_is_china,
        'data_warnings': data_warnings,
    }


# ════════════════════════════════════════════════════════════════
#  批量估值
# ════════════════════════════════════════════════════════════════

def valuate_all_stocks(params: Optional[Dict] = None) -> List[Dict]:
    """
    对股票池所有股票进行估值
    复用 batch_load_recent_financials 避免 N+1 查询
    """
    if params is None:
        params = get_valuation_params()

    stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
    if not stocks:
        return []

    stock_ids = [s.id for s in stocks]
    fins_map = batch_load_recent_financials(stock_ids, limit=3)

    results = []
    for stock in stocks:
        fins = fins_map.get(stock.id, [])
        try:
            val = valuate_stock(stock, fins, params)
            results.append(val)
        except Exception as e:
            logger.warning(f"估值 {stock.symbol} 失败: {e}")
            results.append({
                'symbol': stock.symbol,
                'stock_name': stock.name or stock.symbol,
                'current_price': stock.current_price,
                'methods': {},
                'composite': {'intrinsic_value': None, 'method_count': 0,
                              'range_low': None, 'range_high': None, 'weights_used': {}},
                'margin_of_safety': compute_margin_of_safety(None, None),
                'growth_estimate': {},
                'company_type': 'mature',
                'company_type_label': '成熟型',
            })

    # 按安全边际排序（高→低，None 排最后）
    results.sort(
        key=lambda x: (
            x['margin_of_safety']['pct'] is not None,
            x['margin_of_safety']['pct'] or -999,
        ),
        reverse=True,
    )
    return results


# ════════════════════════════════════════════════════════════════
#  文本摘要（供AI prompt使用）
# ════════════════════════════════════════════════════════════════

def build_valuation_summary(stock: Stock, fins: List[FinancialData], params: Optional[Dict] = None) -> str:
    """生成单只股票的估值文本摘要，用于注入AI prompt"""
    val = valuate_stock(stock, fins, params)
    composite = val['composite']
    mos = val['margin_of_safety']

    if composite['intrinsic_value'] is None:
        return f"{stock.symbol}: 数据不足，无法估值"

    methods_text = []
    for key, result in val['methods'].items():
        if result:
            methods_text.append(f"{result['method_cn']}=${result['intrinsic_value']:.0f}")

    return (
        f"{stock.symbol}: 内在价值${composite['intrinsic_value']:.0f}"
        f"({', '.join(methods_text)}), "
        f"现价${stock.current_price or 0:.0f}, "
        f"安全边际{mos['pct']:+.0f}%({mos['signal_label']}), "
        f"类型={val['company_type_label']}"
    )


def build_valuation_summary_all(params: Optional[Dict] = None) -> str:
    """生成全部股票的估值摘要"""
    valuations = valuate_all_stocks(params)
    lines = []
    for val in valuations:
        composite = val['composite']
        mos = val['margin_of_safety']
        if composite['intrinsic_value'] is not None:
            lines.append(
                f"{val['symbol']}: 内在价值${composite['intrinsic_value']:.0f}, "
                f"现价${val['current_price'] or 0:.0f}, "
                f"安全边际{mos['pct']:+.0f}%({mos['signal_label']})"
            )
        else:
            lines.append(f"{val['symbol']}: 数据不足，无法估值")
    return '\n'.join(lines)
