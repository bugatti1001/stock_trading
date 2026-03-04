"""
KPI 计算器 — 对齐 Excel Raw 表公式列
所有衍生 KPI 从 FinancialData 原始字段实时计算，不存入数据库。

三类 KPI：
1. 单期 KPI — 从单条 FinancialData 记录计算
2. 多期 KPI — 从多条记录（最近 3 年）计算
3. 依赖 Stock 的 KPI — 需要股价/市值等 Stock 模型数据
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
#  中英文 KPI 注册表
# ════════════════════════════════════════════════════════════════

KPI_REGISTRY: Dict[str, Dict[str, str]] = {
    # field_name: {cn: 中文名, en: English Name, col: Excel列}
    'nav_per_share':                  {'cn': '每股净资产',         'en': 'NAV per Share',                   'col': 'F'},
    'nav_growth_rate':                {'cn': '净资产增长率',       'en': 'NAV Growth Rate',                 'col': 'J'},
    'dividend_yield':                 {'cn': '股息率',             'en': 'Dividend Yield',                  'col': 'K'},
    'asset_return':                   {'cn': '资产收益',           'en': 'Asset Return',                    'col': 'L'},
    'adjusted_eps':                   {'cn': '扣非每股收益',       'en': 'Adjusted EPS',                    'col': 'M'},
    'asset_return_rate':              {'cn': '资产收益率',         'en': 'Asset Return Rate',               'col': 'N'},
    'parent_eps':                     {'cn': '归母每股收益',       'en': 'Parent EPS',                      'col': 'O'},
    'adjusted_pe':                    {'cn': '扣非PE',             'en': 'Adjusted PE',                     'col': 'P'},
    'parent_to_net_ratio':            {'cn': '归母/净利润',        'en': 'Parent-to-Net Ratio',             'col': 'V'},
    'total_assets_minus_current_liab':{'cn': '总资产-流动负债',    'en': 'Total Assets - Current Liab',     'col': 'AN'},
    'avg_capex_3y':                   {'cn': '三年平均资本开支',   'en': '3Y Avg CapEx',                    'col': 'AS'},
    'cashflow_profit_ratio_3y':       {'cn': '现金流/利润',        'en': '3Y Cashflow-to-Profit',           'col': 'AZ'},
    'avg_ocf_3y':                     {'cn': '平均现金流',         'en': '3Y Avg OCF',                      'col': 'BA'},
    'avg_fcf_3y':                     {'cn': '平均自由现金流',     'en': '3Y Avg FCF',                      'col': 'BB'},
    'net_cash':                       {'cn': '净现金',             'en': 'Net Cash',                        'col': 'BC'},
    'net_cash_to_capex':              {'cn': '净现金/资本开支',    'en': 'Net Cash to CapEx',               'col': 'BD'},
    'net_cash_to_market_cap':         {'cn': '净现金/市值',        'en': 'Net Cash to Market Cap',          'col': 'BE'},
    'pb_ratio':                       {'cn': 'PB',                 'en': 'Price-to-Book',                   'col': 'H'},
    # 利润表衍生
    'gross_margin':                   {'cn': '毛利率',             'en': 'Gross Margin',                    'col': ''},
    'operating_margin':               {'cn': '营业利润率',         'en': 'Operating Margin',                'col': ''},
    'net_margin':                     {'cn': '净利率',             'en': 'Net Margin',                      'col': ''},
}


# ════════════════════════════════════════════════════════════════
#  单期 KPI — 从单条 FinancialData 计算
# ════════════════════════════════════════════════════════════════

def compute_single_period_kpis(fd) -> Dict[str, Any]:
    """
    从单条 FinancialData 记录计算所有单期衍生 KPI。
    纯函数，不修改 fd 对象。

    对应 Excel Raw 表的公式列：F, V, M, O, AN, BC, 毛利率, 营业利润率, 净利率
    """
    kpis: Dict[str, Any] = {}

    # nav_per_share 每股净资产 = total_equity / shares_outstanding  (F列)
    if fd.total_equity is not None and fd.shares_outstanding and fd.shares_outstanding != 0:
        kpis['nav_per_share'] = fd.total_equity / fd.shares_outstanding

    # parent_to_net_ratio 归母/净利润 = net_income_to_parent / net_income  (V列)
    if fd.net_income_to_parent is not None and fd.net_income and fd.net_income != 0:
        kpis['parent_to_net_ratio'] = fd.net_income_to_parent / fd.net_income

    # adjusted_eps 扣非每股收益 = adjusted_net_income / shares_outstanding  (M列)
    if fd.adjusted_net_income is not None and fd.shares_outstanding and fd.shares_outstanding != 0:
        kpis['adjusted_eps'] = fd.adjusted_net_income / fd.shares_outstanding

    # parent_eps 归母每股收益 = net_income_to_parent / shares_outstanding  (O列)
    if fd.net_income_to_parent is not None and fd.shares_outstanding and fd.shares_outstanding != 0:
        kpis['parent_eps'] = fd.net_income_to_parent / fd.shares_outstanding

    # total_assets_minus_current_liab 总资产-流动负债  (AN列)
    if fd.total_assets is not None and fd.current_liabilities is not None:
        kpis['total_assets_minus_current_liab'] = fd.total_assets - fd.current_liabilities

    # net_cash 净现金 = 货币资金 + 金融投资(可变现) - 短期贷款 - 长期贷款  (BC列)
    cash = fd.cash_and_equivalents or 0
    inv = fd.investments or 0
    stb = fd.short_term_borrowings or 0
    ltb = fd.long_term_borrowings or 0
    # 只在至少有一个有效值时才计算
    if fd.cash_and_equivalents is not None or fd.investments is not None:
        kpis['net_cash'] = cash + inv - stb - ltb

    # gross_margin 毛利率 = (revenue - cost_of_revenue) / revenue
    if fd.revenue and fd.cost_of_revenue is not None and fd.revenue != 0:
        kpis['gross_margin'] = (fd.revenue - fd.cost_of_revenue) / fd.revenue

    # operating_margin 营业利润率 = operating_income / revenue
    if fd.operating_income is not None and fd.revenue and fd.revenue != 0:
        kpis['operating_margin'] = fd.operating_income / fd.revenue

    # net_margin 净利率 = net_income / revenue
    if fd.net_income is not None and fd.revenue and fd.revenue != 0:
        kpis['net_margin'] = fd.net_income / fd.revenue

    return kpis


# ════════════════════════════════════════════════════════════════
#  多期 KPI — 从多条记录（最近 N 年）计算
# ════════════════════════════════════════════════════════════════

def compute_multi_period_kpis(fins: List) -> Dict[str, Any]:
    """
    从多条 FinancialData 记录计算多期衍生 KPI。
    fins: 按 fiscal_year DESC 排序（最新在前），至少 1 条。

    对应 Excel Raw 表的公式列：J, AS, AZ, BA, BB, BD, L, N
    """
    kpis: Dict[str, Any] = {}
    if not fins:
        return kpis

    latest = fins[0]

    # ── 净资产增长率 (J列) — YoY ──
    if len(fins) >= 2:
        curr_eq = fins[0].total_equity
        prev_eq = fins[1].total_equity
        if curr_eq is not None and prev_eq is not None and prev_eq != 0:
            kpis['nav_growth_rate'] = (curr_eq - prev_eq) / prev_eq

    # ── 取最近 3 期用于 3 年聚合 ──
    last_3 = fins[:3]

    # avg_capex_3y 三年平均资本开支 (AS列)
    capex_vals = [f.capital_expenditure for f in last_3 if f.capital_expenditure is not None]
    if capex_vals:
        kpis['avg_capex_3y'] = sum(capex_vals) / len(capex_vals)

    # 三年经营现金流 (AT/AU/AV列 → sum & avg)
    ocf_vals = [f.operating_cash_flow for f in last_3 if f.operating_cash_flow is not None]
    if ocf_vals:
        kpis['sum_ocf_3y'] = sum(ocf_vals)
        kpis['avg_ocf_3y'] = sum(ocf_vals) / len(ocf_vals)  # BA列

    # 三年净利润 (AW/AX/AY列 → sum)
    ni_vals = [f.net_income for f in last_3 if f.net_income is not None]
    if ni_vals:
        kpis['sum_ni_3y'] = sum(ni_vals)

    # cashflow_profit_ratio_3y 现金流/利润 (AZ列) = sum_ocf_3y / sum_ni_3y
    if kpis.get('sum_ocf_3y') is not None and kpis.get('sum_ni_3y') and kpis['sum_ni_3y'] != 0:
        kpis['cashflow_profit_ratio_3y'] = kpis['sum_ocf_3y'] / kpis['sum_ni_3y']

    # avg_fcf_3y 平均自由现金流 (BB列) = avg_ocf_3y - avg_capex_3y
    if kpis.get('avg_ocf_3y') is not None and kpis.get('avg_capex_3y') is not None:
        kpis['avg_fcf_3y'] = kpis['avg_ocf_3y'] - abs(kpis['avg_capex_3y'])

    # net_cash_to_capex 净现金/资本开支 (BD列)
    single_kpis = compute_single_period_kpis(latest)
    net_cash = single_kpis.get('net_cash')
    if net_cash is not None and kpis.get('avg_capex_3y') and kpis['avg_capex_3y'] != 0:
        kpis['net_cash_to_capex'] = net_cash / abs(kpis['avg_capex_3y'])

    # ── 资产收益系列 (L, N列) — 需要 nav_growth_rate ──
    nav = latest.nav_per_share
    if nav is None:
        nav = single_kpis.get('nav_per_share')
    ngr = kpis.get('nav_growth_rate')

    # asset_return 资产收益 (L列) = dividends_per_share + nav_per_share × nav_growth_rate
    if latest.dividends_per_share is not None and nav is not None and ngr is not None:
        kpis['asset_return'] = latest.dividends_per_share + nav * ngr

    # asset_return_rate 资产收益率 (N列) = asset_return / nav_per_share
    if kpis.get('asset_return') is not None and nav and nav != 0:
        kpis['asset_return_rate'] = kpis['asset_return'] / nav

    return kpis


# ════════════════════════════════════════════════════════════════
#  依赖 Stock 模型的 KPI — 需要股价/市值
# ════════════════════════════════════════════════════════════════

def compute_stock_dependent_kpis(
    fd,
    stock_price: Optional[float] = None,
    market_cap: Optional[float] = None,
) -> Dict[str, Any]:
    """
    需要 Stock 表的股价/市值数据才能计算的 KPI。
    这些结果仅用于展示，不存入 FinancialData。

    对应 Excel Raw 表的公式列：H, K, P, BE
    """
    kpis: Dict[str, Any] = {}

    # nav_per_share (优先取 DB 存储值，否则计算)
    nav = fd.nav_per_share
    if nav is None and fd.total_equity and fd.shares_outstanding and fd.shares_outstanding != 0:
        nav = fd.total_equity / fd.shares_outstanding

    # pb_ratio PB (H列) = stock_price / nav_per_share
    if nav and nav != 0 and stock_price:
        kpis['pb_ratio'] = stock_price / nav

    # dividend_yield 股息率 (K列) = dividends_per_share / stock_price
    if fd.dividends_per_share is not None and stock_price and stock_price != 0:
        kpis['dividend_yield'] = fd.dividends_per_share / stock_price

    # adjusted_pe 扣非PE (P列) = market_cap / adjusted_net_income
    if market_cap and fd.adjusted_net_income and fd.adjusted_net_income != 0:
        kpis['adjusted_pe'] = market_cap / fd.adjusted_net_income

    # net_cash_to_market_cap 净现金/市值 (BE列)
    single_kpis = compute_single_period_kpis(fd)
    net_cash = single_kpis.get('net_cash')
    if net_cash is not None and market_cap and market_cap != 0:
        kpis['net_cash_to_market_cap'] = net_cash / market_cap

    return kpis


# ════════════════════════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════════════════════════

def backfill_nav_per_share(fd) -> None:
    """
    如果 AI 提取未直接返回 nav_per_share，则从 total_equity 和 shares_outstanding 计算并回写。
    这是唯一允许修改 fd 对象的函数，用于 post-extraction 阶段。
    """
    if fd.nav_per_share is None and fd.total_equity is not None and fd.shares_outstanding and fd.shares_outstanding != 0:
        fd.nav_per_share = fd.total_equity / fd.shares_outstanding


def format_kpi(key: str, value: Any, as_pct: bool = False) -> str:
    """格式化 KPI 值为显示字符串"""
    if value is None:
        return '—'
    info = KPI_REGISTRY.get(key, {})
    if as_pct or key in ('gross_margin', 'operating_margin', 'net_margin',
                          'nav_growth_rate', 'asset_return_rate',
                          'dividend_yield', 'contract_liability_change_pct'):
        return f"{value * 100:.1f}%"
    if key in ('pb_ratio', 'adjusted_pe', 'cashflow_profit_ratio_3y',
               'net_cash_to_capex', 'parent_to_net_ratio'):
        return f"{value:.2f}"
    return f"{value:.2f}"
