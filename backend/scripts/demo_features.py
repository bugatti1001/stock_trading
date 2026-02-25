#!/usr/bin/env python3
"""
系统功能演示脚本
"""
import sqlite3
from datetime import datetime

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def demo_database_structure():
    """演示数据库结构"""
    print_header("1. 数据库结构")

    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    # 获取所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()

    print("系统包含以下数据表：\n")
    for i, (table_name,) in enumerate(tables, 1):
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"  {i}. {table_name:<25} - {count:>4} 条记录")

    conn.close()

def demo_stock_pool():
    """演示股票池"""
    print_header("2. 股票池管理")

    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    cursor.execute("""
        SELECT symbol, name, in_pool, created_at
        FROM stocks
        ORDER BY created_at DESC
    """)

    stocks = cursor.fetchall()

    print(f"当前股票池共有 {len(stocks)} 只股票：\n")
    print(f"{'代码':<8} | {'名称':<30} | {'状态':<8} | {'添加时间'}")
    print("-" * 70)

    for symbol, name, in_pool, created_at in stocks:
        status = "✓ 在池中" if in_pool else "✗ 已移除"
        date = created_at[:10] if created_at else "N/A"
        print(f"{symbol:<8} | {name[:30]:<30} | {status:<8} | {date}")

    conn.close()

def demo_screening_criteria():
    """演示筛选标准"""
    print_header("3. 投资策略/筛选标准")

    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            name,
            description,
            min_market_cap,
            min_years_listed,
            max_pe_ratio,
            min_roe
        FROM screening_criteria
        WHERE is_active = 1
        ORDER BY id
    """)

    criteria = cursor.fetchall()

    print(f"系统预置 {len(criteria)} 种投资策略：\n")

    for i, (name, desc, mcap, years, pe, roe) in enumerate(criteria, 1):
        print(f"{i}. {name}")
        print(f"   描述: {desc}")
        print(f"   核心要求:")
        if mcap:
            print(f"     - 市值 ≥ ${mcap}B")
        if years:
            print(f"     - 上市 ≥ {years}年")
        if pe:
            print(f"     - P/E ≤ {pe}")
        if roe:
            print(f"     - ROE ≥ {roe}%")
        print()

    conn.close()

def demo_investment_framework():
    """演示投资框架"""
    print_header("4. 投资决策框架")

    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            category,
            COUNT(*) as count
        FROM investment_framework
        WHERE is_active = 1
        GROUP BY category
        ORDER BY category
    """)

    categories = cursor.fetchall()

    print(f"投资框架包含 {sum(c[1] for c in categories)} 项检查点，分为 {len(categories)} 大类：\n")

    for category, count in categories:
        # 翻译类别名称
        category_cn = {
            'BUSINESS_QUALITY': '业务质量',
            'FINANCIAL_HEALTH': '财务健康',
            'VALUATION': '估值分析',
            'MANAGEMENT': '管理层评估',
            'COMPETITIVE_ADVANTAGE': '竞争优势',
            'RISK_ASSESSMENT': '风险评估',
            'MARKET_CONDITION': '市场状况'
        }.get(category, category)

        print(f"  {category_cn:<15} - {count} 项")

    # 显示几个示例
    print("\n关键检查点示例：\n")

    cursor.execute("""
        SELECT
            name,
            question,
            is_required
        FROM investment_framework
        WHERE is_active = 1
        ORDER BY order_index
        LIMIT 5
    """)

    items = cursor.fetchall()

    for name, question, required in items:
        req_mark = "⭐ 必选" if required else "○ 可选"
        print(f"  {req_mark} {name}")
        print(f"     问题: {question}")
        print()

    conn.close()

def demo_api_endpoints():
    """演示API端点"""
    print_header("5. API 端点总览")

    endpoints = [
        ("基础", [
            ("GET", "/health", "健康检查"),
        ]),
        ("股票管理", [
            ("GET", "/api/stocks", "获取股票池"),
            ("POST", "/api/stocks", "添加股票"),
            ("GET", "/api/stocks/<symbol>", "获取股票详情"),
            ("PUT", "/api/stocks/<symbol>", "更新股票"),
            ("DELETE", "/api/stocks/<symbol>", "删除股票"),
            ("POST", "/api/stocks/<symbol>/refresh", "刷新数据"),
            ("GET", "/api/stocks/<symbol>/financials", "获取财务数据"),
        ]),
        ("数据采集", [
            ("POST", "/api/data/sec/fetch/<symbol>", "抓取SEC数据"),
            ("POST", "/api/data/sec/batch-fetch", "批量抓取"),
            ("GET", "/api/data/sec/company-info/<symbol>", "SEC公司信息"),
            ("POST", "/api/data/update-all", "更新所有股票"),
        ]),
        ("筛选系统", [
            ("GET", "/api/screening/criteria", "获取筛选标准"),
            ("POST", "/api/screening/criteria", "创建筛选标准"),
            ("POST", "/api/screening/run", "运行筛选"),
        ]),
        ("新闻系统", [
            ("GET", "/api/news", "获取新闻"),
            ("GET", "/api/news/digest", "每日摘要"),
        ]),
    ]

    total = 0
    for category, eps in endpoints:
        print(f"\n{category}:")
        for method, path, desc in eps:
            print(f"  {method:<7} {path:<40} - {desc}")
            total += 1

    print(f"\n共 {total} 个API端点")

def demo_features_summary():
    """功能总结"""
    print_header("6. 系统能力总结")

    features = {
        "✅ 已实现": [
            "股票池管理（增删改查）",
            "Yahoo Finance 数据集成",
            "SEC EDGAR 官方数据抓取",
            "自动财务比率计算（5种）",
            "5种投资策略模板",
            "15项投资决策框架",
            "批量数据处理",
            "RESTful API（19个端点）",
            "CLI命令行工具",
            "数据库持久化存储",
        ],
        "🔜 第三阶段计划": [
            "新闻采集系统",
            "AI智能分析",
            "定时自动更新",
            "每日新闻摘要",
            "预警通知",
        ],
        "💡 未来可扩展": [
            "Electron桌面应用",
            "数据可视化图表",
            "移动端应用",
            "实时行情推送",
            "社交媒体情绪分析",
        ]
    }

    for category, items in features.items():
        print(f"\n{category}:")
        for item in items:
            print(f"  • {item}")

def main():
    """主函数"""
    print("\n" + "="*70)
    print(" " * 15 + "股票交易分析系统 - 功能演示")
    print("="*70)

    try:
        demo_database_structure()
        demo_stock_pool()
        demo_screening_criteria()
        demo_investment_framework()
        demo_api_endpoints()
        demo_features_summary()

        print("\n" + "="*70)
        print("  演示完成！系统已具备完整的数据管理和分析能力。")
        print("="*70 + "\n")

        print("📖 详细探索指南请查看: EXPLORATION_GUIDE.md")
        print("📝 第二阶段报告请查看: PHASE2_COMPLETE.md\n")

    except Exception as e:
        print(f"\n❌ 演示过程中出现错误: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
