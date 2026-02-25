#!/usr/bin/env python3
"""
添加演示财务数据以测试筛选功能
"""
from app.config.database import db_session, init_db
from app.models.stock import Stock
from app.models.financial_data import FinancialData
from datetime import datetime

def add_demo_financial_data():
    """为现有股票添加演示财务数据"""

    # 获取所有股票
    stocks = db_session.query(Stock).filter_by(in_pool=True).all()

    if not stocks:
        print("❌ 股票池为空，请先添加股票")
        return

    # 演示数据 - 符合不同筛选策略的股票
    demo_data = {
        'AAPL': {
            'name': 'Apple Inc.',
            'sector': 'Technology',
            'market_cap': 2800.0,  # $2.8T
            'pe_ratio': 28.5,
            'pb_ratio': 42.0,
            'dividend_yield': 0.005,  # 0.5%
            'financials': {
                'revenue': 394_328_000_000,
                'net_income': 96_995_000_000,
                'total_assets': 352_755_000_000,
                'total_equity': 62_146_000_000,
                'current_assets': 135_405_000_000,
                'current_liabilities': 145_308_000_000,
                'profit_margin': 24.6,
                'roe': 156.1,  # 非常高的ROE
                'current_ratio': 0.93,
                'debt_to_equity': 1.97
            }
        },
        'MSFT': {
            'name': 'Microsoft Corporation',
            'sector': 'Technology',
            'market_cap': 2700.0,
            'pe_ratio': 35.2,
            'pb_ratio': 11.5,
            'dividend_yield': 0.008,  # 0.8%
            'financials': {
                'revenue': 211_915_000_000,
                'net_income': 72_361_000_000,
                'total_assets': 411_976_000_000,
                'total_equity': 238_268_000_000,
                'current_assets': 184_257_000_000,
                'current_liabilities': 95_082_000_000,
                'profit_margin': 34.1,
                'roe': 30.4,
                'current_ratio': 1.94,
                'debt_to_equity': 0.33
            }
        },
        'GOOGL': {
            'name': 'Alphabet Inc.',
            'sector': 'Technology',
            'market_cap': 1800.0,
            'pe_ratio': 24.3,
            'pb_ratio': 6.2,
            'dividend_yield': 0.0,  # 不分红
            'financials': {
                'revenue': 307_394_000_000,
                'net_income': 73_795_000_000,
                'total_assets': 402_392_000_000,
                'total_equity': 283_477_000_000,
                'current_assets': 155_088_000_000,
                'current_liabilities': 70_969_000_000,
                'profit_margin': 24.0,
                'roe': 26.0,
                'current_ratio': 2.18,
                'debt_to_equity': 0.14
            }
        },
        'NIO': {
            'name': 'NIO Inc.',
            'sector': 'Consumer Cyclical',
            'market_cap': 8.5,
            'pe_ratio': None,  # 亏损公司
            'pb_ratio': 1.8,
            'dividend_yield': 0.0,
            'financials': {
                'revenue': 49_269_000_000,  # CNY
                'net_income': -2_112_000_000,  # 亏损
                'total_assets': 72_388_000_000,
                'total_equity': 41_589_000_000,
                'current_assets': 55_236_000_000,
                'current_liabilities': 22_031_000_000,
                'profit_margin': -4.3,
                'roe': -5.1,
                'current_ratio': 2.51,
                'debt_to_equity': 0.43
            }
        }
    }

    updated_count = 0

    for stock in stocks:
        if stock.symbol not in demo_data:
            print(f"⏭️  跳过 {stock.symbol} (无演示数据)")
            continue

        data = demo_data[stock.symbol]

        # 更新股票基本信息
        stock.name = data['name']
        stock.sector = data['sector']
        stock.market_cap = data['market_cap']
        stock.pe_ratio = data['pe_ratio']
        stock.pb_ratio = data['pb_ratio']
        stock.dividend_yield = data['dividend_yield']

        # 删除旧的财务数据
        db_session.query(FinancialData).filter_by(stock_id=stock.id).delete()

        # 添加近3年的财务数据
        for year_offset in range(3):
            fiscal_year = 2023 - year_offset
            fin = data['financials']

            # 模拟历史数据增长
            growth_factor = 1.0 + (0.1 * year_offset)  # 每年增长10%

            financial = FinancialData(
                stock_id=stock.id,
                fiscal_year=fiscal_year,
                period='FY',
                report_date=datetime(fiscal_year, 12, 31),
                revenue=int(fin['revenue'] / growth_factor) if fin['revenue'] else None,
                net_income=int(fin['net_income'] / growth_factor) if fin['net_income'] else None,
                total_assets=int(fin['total_assets'] / growth_factor) if fin['total_assets'] else None,
                total_equity=int(fin['total_equity'] / growth_factor) if fin['total_equity'] else None,
                current_assets=int(fin['current_assets'] / growth_factor) if fin['current_assets'] else None,
                current_liabilities=int(fin['current_liabilities'] / growth_factor) if fin['current_liabilities'] else None,
                profit_margin=fin['profit_margin'],
                roe=fin['roe'],
                current_ratio=fin['current_ratio'],
                debt_to_equity=fin['debt_to_equity'],
                revenue_growth=10.0 if year_offset == 0 else None,  # 最新年度
                earnings_growth=12.0 if year_offset == 0 else None
            )
            db_session.add(financial)

        updated_count += 1
        print(f"✅ {stock.symbol} - 已更新基本信息和3年财务数据")

    db_session.commit()
    print(f"\n🎉 完成！共更新 {updated_count} 只股票")
    print("\n📊 数据统计:")
    print(f"  - 股票总数: {db_session.query(Stock).filter_by(in_pool=True).count()}")
    print(f"  - 财务记录数: {db_session.query(FinancialData).count()}")
    print("\n💡 现在可以访问 http://localhost:5002/screening 测试筛选功能了！")

if __name__ == '__main__':
    add_demo_financial_data()
