#!/usr/bin/env python3
"""
测试智能数据源切换系统
"""
from app.services.data_source_manager import data_source_manager

print("="*60)
print("智能数据源切换系统测试")
print("="*60)

# 测试1: 获取股票信息
print("\n1. 测试获取股票基本信息...")
print("-" * 60)

data = data_source_manager.fetch_stock_info('AAPL')
if data:
    print(f"✅ 成功获取 AAPL 数据")
    print(f"   数据源: {data.get('data_source')}")
    print(f"   公司名: {data.get('name')}")
    print(f"   行业: {data.get('sector')}")
    print(f"   市值: ${data.get('market_cap')}B" if data.get('market_cap') else "   市值: N/A")
else:
    print("❌ 获取失败")

# 测试2: 查看数据源状态
print("\n2. 数据源状态")
print("-" * 60)

status = data_source_manager.get_data_source_status()
for source, info in status.items():
    status_icon = "✅" if info['available'] else "❌"
    print(f"{status_icon} {source}:")
    print(f"   可用: {info['available']}")
    print(f"   失败次数: {info['failures']}")
    if info['last_failure']:
        print(f"   最后失败: {info['last_failure']}")

print("\n" + "="*60)
print("测试完成！")
print("="*60)
