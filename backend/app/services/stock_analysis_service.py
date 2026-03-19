"""
Stock Analysis Service — v3 对齐 Raw 表
提供股票基本面分析的统一业务逻辑层
- 供 web_routes.py（模板渲染）和 ai_agent_service.py（AI prompt 构建）共用
- 所有衍生 KPI 通过 kpi_calculator 实时计算，不依赖已存字段
"""
import logging
from typing import List, Optional, Dict, Any

from sqlalchemy import desc

from app.config.database import db_session
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.services.kpi_calculator import (
    compute_single_period_kpis,
    compute_multi_period_kpis,
    compute_stock_dependent_kpis,
    format_kpi,
)
from app.utils.cache import cached, cache

logger = logging.getLogger(__name__)

# 数据来源中英文映射（模块级常量，各函数共用）
_SRC_LABELS = {
    'Xueqiu': '雪球', 'Yahoo Finance': 'Yahoo', 'Finnhub': 'Finnhub',
    'SEC EDGAR': 'SEC', 'SEC Report': 'SEC解析',
    'Manual Upload': '手动上传', 'AI Backfill': 'AI补全',
}


# ========== 格式化工具函数 ==========

def pct(val):
    """将小数转百分比显示（0.15 -> 15.0），已是百分比则原样返回"""
    if val is None:
        return None
    return val * 100 if abs(val) < 10 else val


def fmt_b(val):
    """格式化为 Billion 字符串"""
    if val is None:
        return None
    return round(val / 1e9, 2)


def fmt_val(val, unit='B'):
    """通用数值格式化：None→None, 否则除以 1e9"""
    if val is None:
        return None
    if unit == 'B':
        return round(val / 1e9, 2)
    return val


# ========== 基本面分析函数 ==========

def get_recent_financials(stock_id: int, limit: int = 3) -> List[FinancialData]:
    """获取股票最近的财务数据，优先 ANNUAL，fallback 全部 period"""
    fins = db_session.query(FinancialData).filter_by(
        stock_id=stock_id, period=ReportPeriod.ANNUAL
    ).order_by(desc(FinancialData.fiscal_year)).limit(limit).all()

    if not fins:
        fins = db_session.query(FinancialData).filter_by(
            stock_id=stock_id
        ).order_by(desc(FinancialData.fiscal_year), desc(FinancialData.report_date)).limit(limit).all()

    return fins


def check_profitable_3y(fins: List[FinancialData]) -> Optional[bool]:
    """检查是否连续 3 年盈利"""
    if len(fins) < 3:
        return None
    return all(f.net_income is not None and f.net_income > 0 for f in fins[:3])


def build_thresholds(stock: Stock, fins: List[FinancialData]) -> Dict[str, Any]:
    """基本门槛"""
    from datetime import date
    listed_years = None
    if stock.ipo_date:
        try:
            ipo = stock.ipo_date if isinstance(stock.ipo_date, date) else date.fromisoformat(str(stock.ipo_date)[:10])
            listed_years = round((date.today() - ipo).days / 365.25, 1)
        except Exception:
            pass
    return {
        'market_cap_ok': stock.market_cap is not None and stock.market_cap >= 10,
        'listed_years': listed_years,
        'listed_ok': listed_years is not None and listed_years >= 5 if listed_years is not None else None,
        'pe_ok': stock.pe_ratio is not None and 0 < stock.pe_ratio < 30,
        'profitable_3y': check_profitable_3y(fins),
    }


def build_earnings_quality(fins: List[FinancialData], latest: Optional[FinancialData]) -> Dict[str, Any]:
    """盈利质量：3年 OCF、capex、扣非对比、现金流/利润比"""
    data = {
        'years': [],
        'multi_period_kpis': {},
    }

    # 多期 KPI
    if fins:
        multi_kpis = compute_multi_period_kpis(fins)
        data['multi_period_kpis'] = multi_kpis



    for f in fins:
        kpis = compute_single_period_kpis(f)
        # 数据来源信息
        src_label = _SRC_LABELS.get(f.data_source or '', f.data_source or '')
        ext = f.extended_metrics_dict or {}
        field_sources_raw = ext.get('field_sources', {})
        # 转换为中文标签
        field_sources = {k: _SRC_LABELS.get(v, v) for k, v in field_sources_raw.items()} if field_sources_raw else {}

        row = {
            'fd_id': f.id,
            'year': f.fiscal_year,
            'currency': f.currency or 'USD',
            'data_source': src_label,
            'field_sources': field_sources,
            'ocf': fmt_b(f.operating_cash_flow),
            'ocf_raw': f.operating_cash_flow,
            'net_income': fmt_b(f.net_income),
            'net_income_raw': f.net_income,
            'net_income_to_parent': fmt_b(f.net_income_to_parent),
            'net_income_to_parent_raw': f.net_income_to_parent,
            'capex': fmt_b(f.capital_expenditure),
            'capex_raw': f.capital_expenditure,
            'adj_ni': fmt_b(f.adjusted_net_income),
            'adj_ni_raw': f.adjusted_net_income,
            # 计算的 KPI
            'gross_margin': pct(kpis.get('gross_margin')),
            'operating_margin': pct(kpis.get('operating_margin')),
            'net_margin': pct(kpis.get('net_margin')),
            'parent_to_net_ratio': kpis.get('parent_to_net_ratio'),
        }
        data['years'].append(row)

    return data


def build_fin_health(latest: Optional[FinancialData]) -> Dict[str, Any]:
    """财务健康：借款情况、净现金、资产负债结构"""
    if not latest:
        return {
            'fd_id': None, 'cash': None, 'cash_raw': None,
            'stb': None, 'stb_raw': None, 'ltb': None, 'ltb_raw': None,
            'total_assets': None, 'total_assets_raw': None,
            'total_equity': None, 'total_equity_raw': None,
            'current_liabilities': None, 'current_liabilities_raw': None,
            'non_current_assets': None, 'non_current_assets_raw': None,
            'accounts_receivable': None, 'accounts_receivable_raw': None,
            'inventory': None, 'inventory_raw': None,
            'investments': None, 'investments_raw': None,
            'accounts_payable': None, 'accounts_payable_raw': None,
            'net_cash': None,
        }
    kpis = compute_single_period_kpis(latest)

    ext = latest.extended_metrics_dict or {}
    fs_raw = ext.get('field_sources', {})
    return {
        'fd_id': latest.id,
        'data_source': _SRC_LABELS.get(latest.data_source or '', latest.data_source or ''),
        'field_sources': {k: _SRC_LABELS.get(v, v) for k, v in fs_raw.items()} if fs_raw else {},
        'currency': latest.currency or 'USD',
        'cash': fmt_b(latest.cash_and_equivalents),
        'cash_raw': latest.cash_and_equivalents,
        'stb': fmt_b(latest.short_term_borrowings),
        'stb_raw': latest.short_term_borrowings,
        'ltb': fmt_b(latest.long_term_borrowings),
        'ltb_raw': latest.long_term_borrowings,
        'total_assets': fmt_b(latest.total_assets),
        'total_assets_raw': latest.total_assets,
        'total_equity': fmt_b(latest.total_equity),
        'total_equity_raw': latest.total_equity,
        'current_liabilities': fmt_b(latest.current_liabilities),
        'current_liabilities_raw': latest.current_liabilities,
        'non_current_assets': fmt_b(latest.non_current_assets),
        'non_current_assets_raw': latest.non_current_assets,
        'accounts_receivable': fmt_b(latest.accounts_receivable),
        'accounts_receivable_raw': latest.accounts_receivable,
        'inventory': fmt_b(latest.inventory),
        'inventory_raw': latest.inventory,
        'investments': fmt_b(latest.investments),
        'investments_raw': latest.investments,
        'accounts_payable': fmt_b(latest.accounts_payable),
        'accounts_payable_raw': latest.accounts_payable,
        'net_cash': kpis.get('net_cash'),
        'total_assets_minus_current_liab': kpis.get('total_assets_minus_current_liab'),
    }


def build_segments(latest: Optional[FinancialData]) -> Dict[str, Any]:
    """分业务拆解（来自 extended_metrics），附带数据来源"""

    if not latest:
        return {'segments': [], 'data_source': ''}
    ext = latest.extended_metrics_dict or {}
    segs = ext.get('business_segments', [])
    src_label = _SRC_LABELS.get(latest.data_source or '', latest.data_source or '')
    return {
        'segments': segs,
        'data_source': src_label,
    }


def build_moat(fins: List[FinancialData], latest: Optional[FinancialData]) -> Dict[str, Any]:
    """护城河：毛利率趋势、市场份额、留存率"""

    data = {
        'fd_id': latest.id if latest else None,
        'data_source': '',
        'gross_margins': [],
        'market_share': None,
        'retention_rate': None,
        'repeat_purchase': None,
    }
    for f in fins:
        kpis = compute_single_period_kpis(f)
        gm = pct(kpis.get('gross_margin'))
        src_label = _SRC_LABELS.get(f.data_source or '', f.data_source or '')
        data['gross_margins'].append({
            'year': f.fiscal_year,
            'gm': gm,
            'gm_raw': kpis.get('gross_margin'),
            'fd_id': f.id,
            'data_source': src_label,
        })
    if latest:
        data['data_source'] = _SRC_LABELS.get(latest.data_source or '', latest.data_source or '')
        ext = latest.extended_metrics_dict
        if ext:
            moat = ext.get('moat_indicators', {}) or {}
            data['market_share'] = moat.get('market_share_pct')
            data['retention_rate'] = moat.get('user_retention_rate')
            data['repeat_purchase'] = moat.get('repeat_purchase_rate')
    return data


def build_capital_alloc(fins: List[FinancialData]) -> Dict[str, Any]:
    """资本配置：每股分红、股本变化、并购、新业务投入"""

    data = {'years': []}
    for f in fins:
        ext = f.extended_metrics_dict or {}
        ca = (ext.get('capital_allocation', {}) or {})
        fs_raw = ext.get('field_sources', {})
        field_sources = {k: _SRC_LABELS.get(v, v) for k, v in fs_raw.items()} if fs_raw else {}
        src_label = _SRC_LABELS.get(f.data_source or '', f.data_source or '')
        row = {
            'fd_id': f.id,
            'year': f.fiscal_year,
            'currency': f.currency or 'USD',
            'data_source': src_label,
            'field_sources': field_sources,
            'dividends_per_share': f.dividends_per_share,
            'shares': f.shares_outstanding,
            'shares_raw': f.shares_outstanding,
            'ma': fmt_b(ca.get('ma_investments')),
            'ma_raw': ca.get('ma_investments'),
            'new_biz': fmt_b(ca.get('new_business_investment')),
            'new_biz_raw': ca.get('new_business_investment'),
        }
        data['years'].append(row)
    return data


def enrich_stock_for_display(stock: Stock, preloaded_fins: List[FinancialData] = None) -> Stock:
    """
    为模板渲染丰富股票对象：附加 6 大类基本面指标属性

    Args:
        stock: Stock object
        preloaded_fins: Optional pre-loaded financials to avoid N+1 queries.
                        If None, will query DB for this stock.
    """
    from app.utils.market_utils import get_currency_sign

    fins = preloaded_fins if preloaded_fins is not None else get_recent_financials(stock.id, limit=3)
    latest = fins[0] if fins else None
    extra = stock.extra_data or {}

    stock._ps_ratio = extra.get('ps_ratio')
    stock._beta = extra.get('beta')
    stock._thresholds = build_thresholds(stock, fins)
    stock._earnings_quality = build_earnings_quality(fins, latest)
    stock._fin_health = build_fin_health(latest)
    stock._segments = build_segments(latest)
    stock._moat = build_moat(fins, latest)
    stock._capital_alloc = build_capital_alloc(fins)

    # 计算 KPI 用于快速展示
    if latest:
        kpis = compute_single_period_kpis(latest)
        stock._revenue = latest.revenue
        stock._gross_margin_display = pct(kpis.get('gross_margin'))
        stock._net_margin_display = pct(kpis.get('net_margin'))
        stock._latest_fd_id = latest.id
        stock._currency = latest.currency or getattr(stock, 'currency', None) or 'USD'
    else:
        stock._revenue = None
        stock._gross_margin_display = None
        stock._net_margin_display = None
        stock._latest_fd_id = None
        stock._currency = getattr(stock, 'currency', None) or 'USD'

    stock._currency_sign = get_currency_sign(stock._currency)

    # 数据来源标签
    primary_src = extra.get('data_source', '')
    stock._data_source_label = _SRC_LABELS.get(primary_src, primary_src)
    stock._field_sources = extra.get('field_sources') or {}

    # 从 field_sources 中提取所有贡献数据源，用于主表 "来源" 列展示
    all_sources_raw = set()
    if primary_src:
        all_sources_raw.add(primary_src)
    for _src in stock._field_sources.values():
        if _src:
            all_sources_raw.add(_src)
    # 检查所有 financial_data 记录的来源
    for f in fins:
        if f.data_source:
            all_sources_raw.add(f.data_source)
        f_ext = f.extended_metrics_dict or {}
        for _src in (f_ext.get('field_sources') or {}).values():
            if _src:
                all_sources_raw.add(_src)
    stock._all_source_labels = [_SRC_LABELS.get(s, s) for s in sorted(all_sources_raw)]

    return stock


def build_stock_text_summary(stock: Stock, fins: List[FinancialData]) -> str:
    """
    构建单只股票的文本摘要（供 AI prompt 注入）
    全部使用 kpi_calculator 实时计算
    """
    from app.utils.market_utils import get_currency_sign

    latest = fins[0] if fins else None
    extra = stock.extra_data or {}
    currency = latest.currency if latest and latest.currency else (getattr(stock, 'currency', None) or 'USD')
    cs = get_currency_sign(currency)

    # 概览
    header = f"## {stock.symbol} ({stock.name or 'N/A'}) -- {stock.sector or 'N/A'}"
    overview = []
    if stock.current_price:
        overview.append(f"现价:{cs}{stock.current_price:.2f}")
    if stock.market_cap:
        overview.append(f"市值:{cs}{stock.market_cap:.1f}B")
    if stock.pe_ratio:
        overview.append(f"PE:{stock.pe_ratio:.1f}")
    if stock.pb_ratio:
        overview.append(f"PB:{stock.pb_ratio:.2f}")
    if extra.get('ps_ratio'):
        overview.append(f"PS:{extra['ps_ratio']:.1f}")
    if extra.get('beta'):
        overview.append(f"Beta:{extra['beta']:.2f}")
    if stock.dividend_yield is not None:
        overview.append(f"股息率:{stock.dividend_yield*100:.2f}%")

    lines = [header]
    if overview:
        lines.append("行情: " + " | ".join(overview))

    if not latest:
        lines.append("（暂无财报数据）")
        return "\n".join(lines)

    currency = latest.currency or 'USD'
    lines.append(f"货币单位: {currency}")

    # 1. 基本门槛
    threshold_parts = []
    threshold_parts.append(f"市值>100亿:{'✓' if stock.market_cap and stock.market_cap >= 10 else '✗' if stock.market_cap else '?'}")
    threshold_parts.append(f"PE<30:{'✓' if stock.pe_ratio and 0 < stock.pe_ratio < 30 else '✗' if stock.pe_ratio else '?'}")
    profitable_3y = all(f.net_income and f.net_income > 0 for f in fins[:3]) if len(fins) >= 3 else None
    threshold_parts.append(f"连续3年盈利:{'✓' if profitable_3y else '✗' if profitable_3y is False else '?'}")
    lines.append("基本门槛: " + " | ".join(threshold_parts))

    # 2. 盈利质量
    eq_lines = []
    for f in fins:
        kpis = compute_single_period_kpis(f)
        parts = [f"FY{f.fiscal_year}"]
        if f.operating_cash_flow is not None:
            parts.append(f"OCF:{f.operating_cash_flow/1e9:.2f}B")
        if f.net_income is not None:
            parts.append(f"净利:{f.net_income/1e9:.2f}B")
        if f.net_income_to_parent is not None:
            parts.append(f"归母:{f.net_income_to_parent/1e9:.2f}B")
        if f.capital_expenditure is not None:
            parts.append(f"Capex:{f.capital_expenditure/1e9:.2f}B")
        if f.adjusted_net_income is not None:
            parts.append(f"扣非净利:{f.adjusted_net_income/1e9:.2f}B")
        gm = kpis.get('gross_margin')
        if gm is not None:
            parts.append(f"毛利率:{gm*100:.1f}%")
        om = kpis.get('operating_margin')
        if om is not None:
            parts.append(f"营业利润率:{om*100:.1f}%")
        nm = kpis.get('net_margin')
        if nm is not None:
            parts.append(f"净利率:{nm*100:.1f}%")
        if len(parts) > 1:
            eq_lines.append("  " + " | ".join(parts))

    # 多期 KPI
    multi_kpis = compute_multi_period_kpis(fins)
    if multi_kpis.get('cashflow_profit_ratio_3y') is not None:
        eq_lines.insert(0, f"  3年现金流/利润比={multi_kpis['cashflow_profit_ratio_3y']:.2f}")
    if multi_kpis.get('avg_fcf_3y') is not None:
        eq_lines.insert(0, f"  3年平均FCF={multi_kpis['avg_fcf_3y']/1e9:.2f}B")
    if multi_kpis.get('avg_ocf_3y') is not None:
        eq_lines.insert(0, f"  3年平均OCF={multi_kpis['avg_ocf_3y']/1e9:.2f}B")
    if multi_kpis.get('avg_capex_3y') is not None:
        eq_lines.insert(0, f"  3年平均Capex={multi_kpis['avg_capex_3y']/1e9:.2f}B")

    if eq_lines:
        lines.append("盈利质量:")
        lines.extend(eq_lines)

    # 3. 财务健康
    fh_parts = []
    latest_kpis = compute_single_period_kpis(latest)
    if latest.revenue:
        fh_parts.append(f"营收:{latest.revenue/1e9:.2f}B")
    net_cash = latest_kpis.get('net_cash')
    if net_cash is not None:
        fh_parts.append(f"净现金:{net_cash/1e9:.2f}B")
    if latest.cash_and_equivalents is not None:
        fh_parts.append(f"货币资金:{latest.cash_and_equivalents/1e9:.2f}B")
    if latest.investments is not None:
        fh_parts.append(f"金融投资:{latest.investments/1e9:.2f}B")
    if latest.short_term_borrowings is not None:
        fh_parts.append(f"短期借款:{latest.short_term_borrowings/1e9:.2f}B")
    if latest.long_term_borrowings is not None:
        fh_parts.append(f"长期借款:{latest.long_term_borrowings/1e9:.2f}B")
    if latest.accounts_receivable is not None:
        fh_parts.append(f"应收:{latest.accounts_receivable/1e9:.2f}B")
    if latest.inventory is not None:
        fh_parts.append(f"库存:{latest.inventory/1e9:.2f}B")
    if latest.accounts_payable is not None:
        fh_parts.append(f"应付:{latest.accounts_payable/1e9:.2f}B")
    if latest.total_assets is not None:
        fh_parts.append(f"总资产:{latest.total_assets/1e9:.2f}B")
    if latest.total_equity is not None:
        fh_parts.append(f"净资产:{latest.total_equity/1e9:.2f}B")
    if latest.current_liabilities is not None:
        fh_parts.append(f"流动负债:{latest.current_liabilities/1e9:.2f}B")
    ta_cl = latest_kpis.get('total_assets_minus_current_liab')
    if ta_cl is not None:
        fh_parts.append(f"总资产-流动负债:{ta_cl/1e9:.2f}B")
    if fh_parts:
        lines.append(f"财务健康 (FY{latest.fiscal_year} {latest.period.value}): " + " | ".join(fh_parts))

    # 4. 分业务拆解
    ext = latest.extended_metrics_dict
    if ext:
        segs = ext.get('business_segments') or []
        if segs:
            lines.append("分业务拆解:")
            for seg in segs:
                seg_parts = [f"{seg.get('name', '?')}"]
                if seg.get('revenue') is not None:
                    seg_parts.append(f"收入:{seg['revenue']}")
                if seg.get('operating_income') is not None:
                    seg_parts.append(f"利润:{seg['operating_income']}")
                if seg.get('margin_pct') is not None:
                    seg_parts.append(f"利润率:{seg['margin_pct']}%")
                lines.append("  " + " | ".join(seg_parts))

    # 5. 护城河
    moat_parts = []
    gm_trend = []
    for f in fins:
        f_kpis = compute_single_period_kpis(f)
        gm = f_kpis.get('gross_margin')
        if gm is not None:
            gm_trend.append(f"FY{f.fiscal_year}:{gm*100:.1f}%")
    if gm_trend:
        moat_parts.append(f"毛利率趋势[{', '.join(gm_trend)}]")
    if ext:
        moat_ind = ext.get('moat_indicators') or {}
        if moat_ind.get('market_share_pct') is not None:
            moat_parts.append(f"市场份额:{moat_ind['market_share_pct']}%")
        if moat_ind.get('user_retention_rate') is not None:
            moat_parts.append(f"用户留存:{moat_ind['user_retention_rate']}%")
        if moat_ind.get('repeat_purchase_rate') is not None:
            moat_parts.append(f"复购率:{moat_ind['repeat_purchase_rate']}%")
    if moat_parts:
        lines.append("护城河: " + " | ".join(moat_parts))

    # 6. 资本配置
    ca_lines = []
    for f in fins:
        ca_parts = [f"FY{f.fiscal_year}"]
        if f.dividends_per_share is not None:
            ca_parts.append(f"每股分红:{f.dividends_per_share}")
        if f.shares_outstanding is not None:
            ca_parts.append(f"总股本:{f.shares_outstanding}亿")
        f_ext = f.extended_metrics_dict
        if f_ext:
            ca = f_ext.get('capital_allocation') or {}
            if ca.get('ma_investments') is not None:
                ca_parts.append(f"并购:{ca['ma_investments']}")
            if ca.get('new_business_investment') is not None:
                ca_parts.append(f"新业务:{ca['new_business_investment']}")
        if len(ca_parts) > 1:
            ca_lines.append("  " + " | ".join(ca_parts))
    if ca_lines:
        lines.append("资本配置:")
        lines.extend(ca_lines)

    # 7. 多期衍生 KPI 摘要
    kpi_lines = []
    if multi_kpis.get('nav_growth_rate') is not None:
        kpi_lines.append(f"净资产增长率:{multi_kpis['nav_growth_rate']*100:.1f}%")
    if multi_kpis.get('asset_return') is not None:
        kpi_lines.append(f"资产收益:{multi_kpis['asset_return']:.2f}")
    if multi_kpis.get('asset_return_rate') is not None:
        kpi_lines.append(f"资产收益率:{multi_kpis['asset_return_rate']*100:.1f}%")
    if multi_kpis.get('net_cash_to_capex') is not None:
        kpi_lines.append(f"净现金/资本开支:{multi_kpis['net_cash_to_capex']:.2f}")
    # Stock-dependent KPIs
    stock_kpis = compute_stock_dependent_kpis(latest, stock.current_price, stock.market_cap)
    if stock_kpis.get('adjusted_pe') is not None:
        kpi_lines.append(f"扣非PE:{stock_kpis['adjusted_pe']:.1f}")
    if stock_kpis.get('net_cash_to_market_cap') is not None:
        kpi_lines.append(f"净现金/市值:{stock_kpis['net_cash_to_market_cap']*100:.1f}%")
    if kpi_lines:
        lines.append("衍生KPI: " + " | ".join(kpi_lines))

    # 注：内在价值估算模块仍在迭代中，暂不纳入 AI 对话上下文
    # 避免不成熟的估值数据误导 AI 决策

    return "\n".join(lines)


def batch_load_recent_financials(stock_ids: List[int], limit: int = 3) -> Dict[int, List[FinancialData]]:
    """Batch-load recent financials for multiple stocks to avoid N+1 queries.

    Returns a dict mapping stock_id -> list of FinancialData (up to `limit`, newest first).
    """
    if not stock_ids:
        return {}

    # Try ANNUAL first
    all_fins = db_session.query(FinancialData).filter(
        FinancialData.stock_id.in_(stock_ids),
        FinancialData.period == ReportPeriod.ANNUAL
    ).order_by(desc(FinancialData.fiscal_year)).all()

    # Group by stock_id
    fins_by_stock: Dict[int, List[FinancialData]] = {}
    for f in all_fins:
        fins_by_stock.setdefault(f.stock_id, []).append(f)

    # For stocks with no ANNUAL data, fallback to any period
    missing_ids = [sid for sid in stock_ids if sid not in fins_by_stock]
    if missing_ids:
        fallback_fins = db_session.query(FinancialData).filter(
            FinancialData.stock_id.in_(missing_ids)
        ).order_by(desc(FinancialData.fiscal_year), desc(FinancialData.report_date)).all()

        for f in fallback_fins:
            fins_by_stock.setdefault(f.stock_id, []).append(f)

    # Trim to limit per stock
    for sid in fins_by_stock:
        fins_by_stock[sid] = fins_by_stock[sid][:limit]

    return fins_by_stock


def build_stocks_summary(symbols: Optional[List[str]] = None) -> str:
    """
    构建股票池所有股票的文本摘要（供 AI prompt 注入）。
    区分用户实际持仓和仅关注（未持仓）的股票。
    结果缓存 5 分钟，避免每次对话都重复查询。
    """
    cache_key = f"stocks_summary:{','.join(sorted(symbols)) if symbols else 'all'}"
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        query = db_session.query(Stock).filter_by(in_pool=True, is_active=True)
        if symbols:
            query = query.filter(Stock.symbol.in_([s.upper() for s in symbols]))
        stocks = query.all()

        if not stocks:
            return "（暂无股票池数据）"

        # 获取用户实际持仓
        from app.services.portfolio_service import compute_holdings
        user_holdings = {h['symbol']: h for h in compute_holdings()}

        # Batch load all financials in 1-2 queries instead of N
        stock_ids = [s.id for s in stocks]
        fins_map = batch_load_recent_financials(stock_ids, limit=3)

        held_blocks = []
        watch_blocks = []
        for stock in stocks:
            fins = fins_map.get(stock.id, [])
            block = build_stock_text_summary(stock, fins)
            h = user_holdings.get(stock.symbol)
            if h:
                holding_line = (f"用户持仓: {h['net_shares']}股, "
                                f"均价${h['avg_cost']:.2f}")
                if h.get('unrealized_pnl_pct') is not None:
                    holding_line += f", 盈亏{h['unrealized_pnl_pct']:+.1f}%"
                block += f"\n{holding_line}"
                held_blocks.append(block)
            else:
                watch_blocks.append(block)

        parts = []
        if held_blocks:
            parts.append("=== 用户实际持仓 ===\n\n" + "\n\n".join(held_blocks))
        if watch_blocks:
            parts.append("=== 关注但未持仓 ===\n\n" + "\n\n".join(watch_blocks))

        result = "\n\n".join(parts)
        cache.set(cache_key, result, ttl_seconds=300)
        return result
    except Exception as e:
        logger.error(f"构建股票摘要失败: {e}")
        return "（获取股票数据时出错）"


def invalidate_stock_cache():
    """Invalidate all stock summary caches (call after stock data changes)."""
    cache.invalidate_prefix("stocks_summary:")
