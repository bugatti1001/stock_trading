#!/usr/bin/env python3
"""
交互式系统探索工具
"""
import sqlite3
import sys

def print_menu():
    """显示主菜单"""
    print("\n" + "="*70)
    print(" " * 20 + "系统探索菜单")
    print("="*70)
    print("""
1. 📊 查看股票池
2. 🎯 查看筛选策略
3. ✓  查看投资框架
4. 📈 查看财务数据
5. 🔍 搜索股票
6. 📝 查看系统统计
7. 💡 帮助
0. 退出

""")

def view_stock_pool(conn):
    """查看股票池"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, name, sector, market_cap, pe_ratio, in_pool
        FROM stocks
        ORDER BY created_at DESC
    """)

    stocks = cursor.fetchall()

    print(f"\n{'='*80}")
    print(f"  股票池（共 {len(stocks)} 只）")
    print(f"{'='*80}\n")

    if not stocks:
        print("  股票池为空，请先添加股票\n")
        return

    print(f"{'代码':<8} | {'名称':<25} | {'行业':<15} | {'市值(B)':<10} | {'P/E':<6} | {'状态'}")
    print("-" * 80)

    for symbol, name, sector, mcap, pe, in_pool in stocks:
        status = "✓" if in_pool else "✗"
        name_short = (name[:22] + "...") if name and len(name) > 25 else (name or "N/A")
        sector_short = (sector[:12] + "...") if sector and len(sector) > 15 else (sector or "N/A")
        mcap_str = f"${mcap:.1f}" if mcap else "N/A"
        pe_str = f"{pe:.1f}" if pe else "N/A"

        print(f"{symbol:<8} | {name_short:<25} | {sector_short:<15} | {mcap_str:<10} | {pe_str:<6} | {status}")

def view_screening_strategies(conn):
    """查看筛选策略"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            id,
            name,
            description,
            min_market_cap,
            min_years_listed,
            min_consecutive_profitable_years,
            max_pe_ratio,
            min_roe
        FROM screening_criteria
        WHERE is_active = 1
        ORDER BY id
    """)

    strategies = cursor.fetchall()

    print(f"\n{'='*80}")
    print(f"  投资策略（共 {len(strategies)} 种）")
    print(f"{'='*80}\n")

    for idx, name, desc, mcap, years, profit_years, pe, roe in strategies:
        print(f"{idx}. {name}")
        print(f"   {desc}")
        print(f"   要求: ", end="")

        requirements = []
        if mcap:
            requirements.append(f"市值≥${mcap}B")
        if years:
            requirements.append(f"上市≥{years}年")
        if profit_years:
            requirements.append(f"连续盈利≥{profit_years}年")
        if pe:
            requirements.append(f"P/E≤{pe}")
        if roe:
            requirements.append(f"ROE≥{roe}%")

        print(" | ".join(requirements) if requirements else "详见完整配置")
        print()

def view_investment_framework(conn):
    """查看投资框架"""
    cursor = conn.cursor()

    # 按类别分组
    cursor.execute("""
        SELECT
            category,
            name,
            question,
            is_required
        FROM investment_framework
        WHERE is_active = 1
        ORDER BY order_index
    """)

    items = cursor.fetchall()

    print(f"\n{'='*80}")
    print(f"  投资决策框架（共 {len(items)} 项）")
    print(f"{'='*80}\n")

    current_category = None
    category_names = {
        'BUSINESS_QUALITY': '业务质量',
        'FINANCIAL_HEALTH': '财务健康',
        'VALUATION': '估值分析',
        'MANAGEMENT': '管理层评估',
        'COMPETITIVE_ADVANTAGE': '竞争优势',
        'RISK_ASSESSMENT': '风险评估',
        'MARKET_CONDITION': '市场状况'
    }

    for category, name, question, is_required in items:
        if category != current_category:
            current_category = category
            category_cn = category_names.get(category, category)
            print(f"\n【{category_cn}】\n")

        req_mark = "⭐ 必选" if is_required else "○ 可选"
        print(f"  {req_mark} {name}")
        print(f"     {question}")

def view_financial_data(conn):
    """查看财务数据"""
    cursor = conn.cursor()

    # 获取有财务数据的股票
    cursor.execute("""
        SELECT DISTINCT s.symbol, s.name
        FROM stocks s
        INNER JOIN financial_data f ON s.id = f.stock_id
    """)

    stocks_with_data = cursor.fetchall()

    if not stocks_with_data:
        print("\n暂无股票财务数据，请先使用 'python cli.py fetch-sec <SYMBOL>' 抓取数据\n")
        return

    print(f"\n以下股票有财务数据：\n")
    for i, (symbol, name) in enumerate(stocks_with_data, 1):
        print(f"{i}. {symbol} - {name}")

    print("\n输入股票代码查看详情（或按 Enter 返回）：", end=" ")
    symbol = input().strip().upper()

    if not symbol:
        return

    cursor.execute("""
        SELECT
            f.fiscal_year,
            f.revenue,
            f.net_income,
            f.profit_margin,
            f.roe,
            f.revenue_growth,
            f.earnings_growth
        FROM financial_data f
        INNER JOIN stocks s ON s.id = f.stock_id
        WHERE s.symbol = ?
        ORDER BY f.fiscal_year DESC
    """, (symbol,))

    data = cursor.fetchall()

    if not data:
        print(f"\n未找到 {symbol} 的财务数据\n")
        return

    print(f"\n{'='*90}")
    print(f"  {symbol} 历年财务数据")
    print(f"{'='*90}\n")

    print(f"{'年度':<6} | {'营收(亿美元)':<12} | {'净利(亿美元)':<12} | {'利润率':<8} | {'ROE':<6} | {'营收增长':<8} | {'盈利增长'}")
    print("-" * 90)

    for year, rev, income, margin, roe, rev_growth, earn_growth in data:
        rev_b = f"${rev/1e9:.2f}B" if rev else "N/A"
        income_b = f"${income/1e9:.2f}B" if income else "N/A"
        margin_str = f"{margin:.1f}%" if margin else "N/A"
        roe_str = f"{roe:.1f}%" if roe else "N/A"
        rev_g = f"{rev_growth:+.1f}%" if rev_growth else "N/A"
        earn_g = f"{earn_growth:+.1f}%" if earn_growth else "N/A"

        print(f"{year:<6} | {rev_b:<12} | {income_b:<12} | {margin_str:<8} | {roe_str:<6} | {rev_g:<8} | {earn_g}")

def search_stock(conn):
    """搜索股票"""
    print("\n输入股票代码或名称搜索：", end=" ")
    query = input().strip()

    if not query:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            symbol,
            name,
            sector,
            industry,
            market_cap,
            pe_ratio,
            pb_ratio,
            dividend_yield,
            notes
        FROM stocks
        WHERE symbol LIKE ? OR name LIKE ?
    """, (f"%{query}%", f"%{query}%"))

    results = cursor.fetchall()

    if not results:
        print(f"\n未找到匹配 '{query}' 的股票\n")
        return

    print(f"\n找到 {len(results)} 只股票：\n")

    for symbol, name, sector, industry, mcap, pe, pb, div, notes in results:
        print(f"代码: {symbol}")
        print(f"名称: {name}")
        if sector:
            print(f"行业: {sector} / {industry or 'N/A'}")
        if mcap:
            print(f"市值: ${mcap}B")
        if pe:
            print(f"估值: P/E={pe:.1f}, P/B={pb:.2f}" if pb else f"P/E={pe:.1f}")
        if div:
            print(f"股息率: {div*100:.2f}%")
        if notes:
            print(f"备注: {notes}")
        print()

def view_statistics(conn):
    """查看系统统计"""
    cursor = conn.cursor()

    print(f"\n{'='*80}")
    print(f"  系统统计信息")
    print(f"{'='*80}\n")

    # 股票统计
    cursor.execute("SELECT COUNT(*) FROM stocks WHERE in_pool = 1")
    active_stocks = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM stocks")
    total_stocks = cursor.fetchone()[0]

    print(f"股票池：{active_stocks} 只在池中，共 {total_stocks} 只历史记录")

    # 财务数据统计
    cursor.execute("SELECT COUNT(*) FROM financial_data")
    financial_records = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT stock_id) FROM financial_data")
    stocks_with_financials = cursor.fetchone()[0]

    print(f"财务数据：{financial_records} 条记录，覆盖 {stocks_with_financials} 只股票")

    # 年报统计
    cursor.execute("SELECT COUNT(*) FROM annual_reports")
    reports = cursor.fetchone()[0]

    print(f"年报记录：{reports} 份")

    # 筛选标准
    cursor.execute("SELECT COUNT(*) FROM screening_criteria WHERE is_active = 1")
    active_criteria = cursor.fetchone()[0]

    print(f"筛选策略：{active_criteria} 个活跃策略")

    # 投资框架
    cursor.execute("SELECT COUNT(*) FROM investment_framework WHERE is_active = 1")
    framework_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM investment_framework WHERE is_active = 1 AND is_required = 1")
    required_items = cursor.fetchone()[0]

    print(f"投资框架：{framework_items} 项检查点（其中 {required_items} 项必选）")

    # 数据库大小
    cursor.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
    db_size = cursor.fetchone()[0]

    print(f"\n数据库大小：{db_size / 1024 / 1024:.2f} MB")

def show_help():
    """显示帮助"""
    print(f"\n{'='*80}")
    print("  帮助信息")
    print(f"{'='*80}\n")

    print("""
此工具用于交互式探索股票分析系统的数据和功能。

主要功能：
  1. 查看股票池 - 显示所有已添加的股票
  2. 查看筛选策略 - 显示5种预置投资策略
  3. 查看投资框架 - 显示15项投资决策检查点
  4. 查看财务数据 - 查看股票的历史财务数据
  5. 搜索股票 - 按代码或名称搜索股票
  6. 查看统计 - 显示系统整体统计信息

提示：
  - 使用 'python cli.py' 进行股票管理操作
  - 使用 'python run.py' 启动API服务器
  - 查看 EXPLORATION_GUIDE.md 获取详细使用指南
    """)

def main():
    """主函数"""
    db_path = '../data/stock_trading.db'

    try:
        conn = sqlite3.connect(db_path)

        while True:
            print_menu()
            choice = input("请选择操作 (0-7): ").strip()

            if choice == '0':
                print("\n再见！👋\n")
                break
            elif choice == '1':
                view_stock_pool(conn)
            elif choice == '2':
                view_screening_strategies(conn)
            elif choice == '3':
                view_investment_framework(conn)
            elif choice == '4':
                view_financial_data(conn)
            elif choice == '5':
                search_stock(conn)
            elif choice == '6':
                view_statistics(conn)
            elif choice == '7':
                show_help()
            else:
                print("\n❌ 无效选择，请输入 0-7\n")

            input("\n按 Enter 继续...")

    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
