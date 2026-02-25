#!/usr/bin/env python3
"""
用 SEC EDGAR 数据填充股票财务信息
解决 Yahoo Finance 限流导致数据缺失的问题
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.database import db_session, init_db
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.scrapers.sec_edgar_scraper import SECEdgarScraper
from datetime import datetime, date

# 实时价格和补充信息（手动维护，因为 Yahoo 限流）
STOCK_SUPPLEMENTS = {
    'AAPL': {
        'current_price': 227.5,
        'sector': 'Technology',
        'industry': 'Consumer Electronics',
        'exchange': 'NASDAQ',
        'website': 'https://www.apple.com',
        'description': 'Apple Inc. designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories worldwide.',
        'employees': 150000,
    },
    'MSFT': {
        'current_price': 415.0,
        'sector': 'Technology',
        'industry': 'Software—Infrastructure',
        'exchange': 'NASDAQ',
        'website': 'https://www.microsoft.com',
        'description': 'Microsoft Corporation develops and supports software, services, devices and solutions worldwide.',
        'employees': 221000,
    },
    'GOOGL': {
        'current_price': 197.5,
        'sector': 'Technology',
        'industry': 'Internet Content & Information',
        'exchange': 'NASDAQ',
        'website': 'https://www.abc.xyz',
        'description': 'Alphabet Inc. provides various products and platforms in the United States, Europe, the Middle East, Africa, the Asia-Pacific, Canada, and Latin America.',
        'employees': 181798,
    },
    'AMZN': {
        'current_price': 228.0,
        'sector': 'Consumer Cyclical',
        'industry': 'Internet Retail',
        'exchange': 'NASDAQ',
        'website': 'https://www.amazon.com',
        'description': 'Amazon.com, Inc. engages in the retail sale of consumer products, advertising, and subscriptions service through online and physical stores.',
        'employees': 1551000,
        'market_cap': 2400.0,
    },
    'NIO': {
        'current_price': 4.2,
        'sector': 'Consumer Cyclical',
        'industry': 'Auto Manufacturers',
        'exchange': 'NYSE',
        'website': 'https://www.nio.com',
        'description': 'NIO Inc. designs, develops, jointly manufactures, and sells smart electric vehicles in China.',
        'employees': 32000,
    },
    'TSLA': {
        'current_price': 350.0,
        'sector': 'Consumer Cyclical',
        'industry': 'Auto Manufacturers',
        'exchange': 'NASDAQ',
        'website': 'https://www.tesla.com',
        'description': 'Tesla, Inc. designs, develops, manufactures, leases, and sells electric vehicles, and energy generation and storage systems.',
        'employees': 127855,
        'market_cap': 1120.0,
    },
}

# SEC EDGAR 财务字段映射
SEC_METRICS = {
    'revenue': ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'],
    'net_income': ['NetIncomeLoss'],
    'operating_income': ['OperatingIncomeLoss'],
    'total_assets': ['Assets'],
    'total_equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
    'eps_basic': ['EarningsPerShareBasic'],
'total_liabilities': ['Liabilities', 'LiabilitiesAndStockholdersEquity'],
    'cash': ['CashAndCashEquivalentsAtCarryingValue', 'Cash'],
    'operating_cashflow': ['NetCashProvidedByUsedInOperatingActivities'],
    'rd_expense': ['ResearchAndDevelopmentExpense', 'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost'],
    'selling_expense': ['SellingGeneralAndAdministrativeExpense', 'GeneralAndAdministrativeExpense'],
    'gross_profit': ['GrossProfit'],
    'cost_of_revenue': ['CostOfRevenue', 'CostOfGoodsAndServicesSold'],
    'free_cash_flow': ['PaymentsToAcquirePropertyPlantAndEquipment'],  # We'll compute FCF = op_cf - capex
    'capex': ['PaymentsToAcquirePropertyPlantAndEquipment'],
    'current_assets': ['AssetsCurrent'],
    'current_liabilities': ['LiabilitiesCurrent'],
}


def get_annual_data(us_gaap, metric_names, years=5):
    """从 us-gaap 中提取某指标的近几年年度数据"""
    for name in metric_names:
        if name not in us_gaap:
            continue
        units = us_gaap[name].get('units', {})
        unit_key = 'USD' if 'USD' in units else ('shares' if 'shares' in units else None)
        if not unit_key:
            unit_key = list(units.keys())[0] if units else None
        if not unit_key:
            continue

        entries = units[unit_key]
        # 只取年报 (10-K)，按年份分组取最新
        annual_by_year = {}
        for e in entries:
            if e.get('form') == '10-K' and 'end' in e and 'val' in e:
                year = int(e['end'][:4])
                # 同年取最新一条
                if year not in annual_by_year or e['end'] > annual_by_year[year]['end']:
                    annual_by_year[year] = e

        if annual_by_year:
            # 返回近 years 年，按年份降序
            sorted_years = sorted(annual_by_year.keys(), reverse=True)[:years]
            return {y: annual_by_year[y] for y in sorted_years}

    return {}


def fill_stock_data():
    session = db_session
    scraper = SECEdgarScraper()
    if True:

        stocks = session.query(Stock).filter_by(is_active=True).all()
        print(f"找到 {len(stocks)} 只股票\n")

        # 处理 AMAZON → AMZN 的修正
        for stock in stocks:
            if stock.symbol == 'AMAZON':
                print(f"⚠️  修正错误代码: AMAZON → AMZN")
                stock.symbol = 'AMZN'
                stock.name = 'Amazon.com Inc.'
                session.commit()
                break

        # 重新查询（修正后）
        stocks = session.query(Stock).filter_by(is_active=True).all()

        for stock in stocks:
            symbol = stock.symbol
            print(f"\n{'='*50}")
            print(f"处理 {symbol} ({stock.name})")

            # 1. 补充基础信息
            if symbol in STOCK_SUPPLEMENTS:
                sup = STOCK_SUPPLEMENTS[symbol]
                if not stock.current_price and sup.get('current_price'):
                    stock.current_price = sup['current_price']
                if not stock.sector and sup.get('sector'):
                    stock.sector = sup['sector']
                if not stock.industry and sup.get('industry'):
                    stock.industry = sup['industry']
                if not stock.exchange and sup.get('exchange'):
                    stock.exchange = sup['exchange']
                if not stock.website and sup.get('website'):
                    stock.website = sup['website']
                if not stock.description and sup.get('description'):
                    stock.description = sup['description']
                if not stock.employees and sup.get('employees'):
                    stock.employees = sup['employees']
                if not stock.market_cap and sup.get('market_cap'):
                    stock.market_cap = sup['market_cap']
                print(f"  ✅ 基础信息已填充")

            # 2. 从 SEC EDGAR 获取财务数据
            try:
                cik = scraper.get_company_cik(symbol)
                if not cik:
                    print(f"  ❌ 未找到 {symbol} 的 CIK，跳过财务数据")
                    session.commit()
                    continue

                print(f"  CIK: {cik}")
                facts = scraper.get_company_facts(cik)
                if not facts:
                    print(f"  ❌ 无法获取 {symbol} 的财务数据")
                    session.commit()
                    continue

                us_gaap = facts.get('facts', {}).get('us-gaap', {})
                if not us_gaap:
                    print(f"  ❌ {symbol} 无 US-GAAP 数据")
                    session.commit()
                    continue

                # 提取各指标的年度数据
                metric_data = {}
                for field, names in SEC_METRICS.items():
                    annual = get_annual_data(us_gaap, names, years=5)
                    if annual:
                        metric_data[field] = annual

                if not metric_data:
                    print(f"  ❌ 无法提取财务指标")
                    session.commit()
                    continue

                # 确定有数据的年份（以 revenue 为准，否则用其他）
                ref_field = next(
                    (f for f in ['revenue', 'net_income', 'total_assets'] if f in metric_data),
                    None
                )
                if not ref_field:
                    print(f"  ❌ 无参考财务指标")
                    session.commit()
                    continue

                years_available = sorted(metric_data[ref_field].keys(), reverse=True)
                print(f"  可用年度: {years_available}")

                # 删除旧财务数据
                old_count = session.query(FinancialData).filter_by(stock_id=stock.id).count()
                if old_count:
                    session.query(FinancialData).filter_by(stock_id=stock.id).delete()
                    print(f"  删除旧数据 {old_count} 条")

                # 插入新财务数据
                inserted = 0
                for year in years_available:
                    def get_val(field, yr):
                        d = metric_data.get(field, {}).get(yr)
                        return d['val'] if d else None

                    revenue = get_val('revenue', year)
                    net_income = get_val('net_income', year)
                    operating_income = get_val('operating_income', year)
                    total_assets = get_val('total_assets', year)
                    total_equity = get_val('total_equity', year)
                    eps = get_val('eps_basic', year)
                    total_liabilities = get_val('total_liabilities', year)
                    cash = get_val('cash', year)
                    op_cashflow = get_val('operating_cashflow', year)
                    rd_expense = get_val('rd_expense', year)
                    selling_expense = get_val('selling_expense', year)
                    gross_profit = get_val('gross_profit', year)
                    cost_of_revenue = get_val('cost_of_revenue', year)
                    current_assets = get_val('current_assets', year)
                    current_liabilities = get_val('current_liabilities', year)
                    capex = get_val('capex', year)

                    # 计算衍生指标
                    profit_margin = (net_income / revenue) if revenue and net_income else None
                    roe = (net_income / total_equity) if total_equity and net_income and total_equity > 0 else None
                    roa = (net_income / total_assets) if total_assets and net_income and total_assets > 0 else None
                    debt_to_equity = (total_liabilities / total_equity) if total_liabilities and total_equity and total_equity > 0 else None
                    current_ratio = (current_assets / current_liabilities) if current_assets and current_liabilities and current_liabilities > 0 else None
                    # gross_profit 优先直接取，否则用 revenue - cost_of_revenue 计算
                    if not gross_profit and revenue and cost_of_revenue:
                        gross_profit = revenue - cost_of_revenue
                    # FCF = 营业现金流 - 资本支出
                    free_cash_flow = (op_cashflow - capex) if op_cashflow and capex else None

                    report_date_str = metric_data[ref_field][year]['end']
                    try:
                        report_date = date.fromisoformat(report_date_str)
                    except:
                        report_date = date(year, 12, 31)

                    fd = FinancialData(
                        stock_id=stock.id,
                        fiscal_year=year,
                        period=ReportPeriod.ANNUAL,
                        report_date=report_date,
                        revenue=int(revenue) if revenue else None,
                        gross_profit=int(gross_profit) if gross_profit else None,
                        cost_of_revenue=int(cost_of_revenue) if cost_of_revenue else None,
                        net_income=int(net_income) if net_income else None,
                        operating_income=int(operating_income) if operating_income else None,
                        total_assets=int(total_assets) if total_assets else None,
                        total_equity=int(total_equity) if total_equity else None,
                        total_liabilities=int(total_liabilities) if total_liabilities else None,
                        current_assets=int(current_assets) if current_assets else None,
                        current_liabilities=int(current_liabilities) if current_liabilities else None,
                        cash_and_equivalents=int(cash) if cash else None,
                        operating_cash_flow=int(op_cashflow) if op_cashflow else None,
                        free_cash_flow=int(free_cash_flow) if free_cash_flow else None,
                        eps_basic=float(eps) if eps else None,
                        profit_margin=float(profit_margin) if profit_margin else None,
                        roe=float(roe) if roe else None,
                        roa=float(roa) if roa else None,
                        debt_to_equity=float(debt_to_equity) if debt_to_equity else None,
                        current_ratio=float(current_ratio) if current_ratio else None,
                        rd_expense=int(rd_expense) if rd_expense else None,
                        selling_expense=int(selling_expense) if selling_expense else None,
                    )
                    session.add(fd)
                    inserted += 1
                    if revenue:
                        print(f"  {year}: Revenue=${revenue/1e9:.1f}B, NetIncome=${net_income/1e9:.1f}B" if net_income else f"  {year}: Revenue=${revenue/1e9:.1f}B")

                session.commit()
                print(f"  ✅ 插入 {inserted} 条财务数据")

                # 更新股票的关键比率（用最新年度）
                latest_year = years_available[0]
                def get_latest(field):
                    d = metric_data.get(field, {}).get(latest_year)
                    return d['val'] if d else None

                latest_revenue = get_latest('revenue')
                latest_net_income = get_latest('net_income')
                latest_equity = get_latest('total_equity')
                latest_eps = get_latest('eps_basic')
                latest_assets = get_latest('total_assets')

                if latest_eps:
                    stock.eps = float(latest_eps)
                if latest_equity and latest_net_income and latest_equity > 0:
                    stock.roe = float(latest_net_income / latest_equity)

                session.commit()

            except Exception as e:
                print(f"  ❌ 处理 {symbol} 时出错: {e}")
                import traceback
                traceback.print_exc()
                session.rollback()
                continue

        print(f"\n{'='*50}")
        print("✅ 数据填充完成！")

        # 最终汇总
        stocks = session.query(Stock).filter_by(is_active=True).all()
        print(f"\n{'Symbol':<8} {'Name':<30} {'Price':<10} {'Financials':<12}")
        print('-' * 65)
        for s in stocks:
            fin_count = session.query(FinancialData).filter_by(stock_id=s.id).count()
            price = f"${s.current_price}" if s.current_price else "缺失"
            print(f"{s.symbol:<8} {s.name:<30} {price:<10} {fin_count} 年")


if __name__ == '__main__':
    fill_stock_data()
