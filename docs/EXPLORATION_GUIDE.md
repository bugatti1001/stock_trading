# 🔍 系统功能深度探索指南

本指南将带你深入了解股票分析系统的所有功能。

## 📋 目录

1. [数据库结构探索](#数据库结构探索)
2. [股票池管理](#股票池管理)
3. [SEC数据抓取](#sec数据抓取)
4. [筛选标准详解](#筛选标准详解)
5. [投资框架使用](#投资框架使用)
6. [API完整测试](#api完整测试)
7. [数据分析示例](#数据分析示例)

---

## 1. 数据库结构探索

### 查看数据库表

```bash
cd backend
source venv/bin/activate

# 使用 SQLite 命令行工具
sqlite3 ../data/stock_trading.db

# 在 SQLite 中执行：
.tables                    # 查看所有表
.schema stocks            # 查看 stocks 表结构
.schema financial_data    # 查看财务数据表结构
.schema screening_criteria # 查看筛选标准表
.schema investment_framework # 查看投资框架表

# 查询示例
SELECT name, description FROM screening_criteria;
SELECT name, category, question FROM investment_framework;

.quit
```

### 表结构说明

#### stocks（股票主表）
- 21个字段：基础信息、财务指标、状态标记
- 支持软删除（in_pool 字段）
- extra_data 字段存储扩展信息

#### financial_data（财务数据表）
- 损益表：revenue, net_income, operating_income
- 资产负债表：total_assets, total_liabilities, total_equity
- 现金流：operating_cash_flow, free_cash_flow
- 财务比率：profit_margin, roe, roa, current_ratio
- 增长指标：revenue_growth, earnings_growth

#### screening_criteria（筛选标准）
- 20+个可配置参数
- JSON字段支持行业/板块过滤
- 完全可自定义

#### investment_framework（投资框架）
- 分类管理（7大类别）
- 优先级排序（order_index）
- 必选/可选标记（is_required）

---

## 2. 股票池管理

### 基础操作测试

```bash
# 确保服务器运行中
python run.py

# 在另一个终端：
source venv/bin/activate

# 1. 添加多只股票
python cli.py add AAPL "Apple Inc."
python cli.py add MSFT "Microsoft Corporation"
python cli.py add GOOGL "Alphabet Inc."
python cli.py add TSLA "Tesla Inc."
python cli.py add NVDA "NVIDIA Corporation"

# 2. 查看股票池
python cli.py list

# 3. 查看单个股票详情
python cli.py get AAPL

# 4. 更新股票（使用 curl）
curl -X PUT http://localhost:5001/api/stocks/AAPL \
  -H "Content-Type: application/json" \
  -d '{
    "notes": "重点关注：iPhone销量、服务业务增长、AI布局",
    "market_cap": 3000.0
  }'

# 5. 软删除（从池中移除但保留数据）
python cli.py remove TSLA

# 6. 查看所有股票（包括已移除的）
curl "http://localhost:5001/api/stocks?in_pool=false"
```

### 批量操作

```bash
# 批量添加科技股
for symbol in AMZN META NFLX AMD INTC; do
  python cli.py add $symbol
  sleep 2
done
```

---

## 3. SEC数据抓取

### 单个股票数据抓取

```bash
# 抓取 Apple 的SEC数据（最近3年）
python cli.py fetch-sec AAPL

# 查看返回的数据结构
# 包含：
# - filings_saved: 保存的年报数量
# - financial_data_saved: 保存的财务数据条数
# - consecutive_profitable_years: 连续盈利年数
# - errors: 错误信息（如果有）
```

### 获取公司SEC信息

```bash
# 获取 Microsoft 的 CIK 和最近文件
curl http://localhost:5001/api/data/sec/company-info/MSFT

# 返回示例：
# {
#   "success": true,
#   "symbol": "MSFT",
#   "cik": "0000789019",
#   "recent_filings": [
#     {
#       "form_type": "10-K",
#       "filing_date": "2023-07-27",
#       "accession_number": "0000789019-23-000067",
#       "document_url": "https://..."
#     }
#   ]
# }
```

### 批量抓取

```bash
# 方法1：使用API
curl -X POST http://localhost:5001/api/data/sec/batch-fetch \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["AAPL", "MSFT", "GOOGL"],
    "years": 3
  }'

# 方法2：更新池中所有股票
curl -X POST "http://localhost:5001/api/data/update-all?years=3"
```

### 查看抓取的财务数据

```bash
# 查看 AAPL 的历史财务数据
curl http://localhost:5001/api/stocks/AAPL/financials

# 使用 SQLite 直接查询
sqlite3 ../data/stock_trading.db "
SELECT
  fiscal_year,
  revenue,
  net_income,
  profit_margin,
  roe
FROM financial_data
WHERE stock_id = (SELECT id FROM stocks WHERE symbol = 'AAPL')
ORDER BY fiscal_year DESC;
"
```

---

## 4. 筛选标准详解

### 查看所有筛选策略

```bash
# 使用 CLI
python cli.py criteria

# 使用 API（格式化输出）
curl http://localhost:5001/api/screening/criteria | python -m json.tool
```

### 5种策略对比

#### 策略1：价值投资（巴菲特风格）
```json
{
  "name": "Value Investing (Buffett Style)",
  "description": "寻找稳定盈利、估值合理的优质公司",
  "重点指标": {
    "市值": "≥ 100亿美元",
    "上市年限": "≥ 5年",
    "连续盈利": "≥ 3年",
    "ROE": "≥ 15%",
    "P/E": "≤ 25",
    "债务比": "≤ 0.5"
  },
  "适合": "长期稳健投资者"
}
```

#### 策略2：成长投资
```json
{
  "name": "Growth Investing",
  "description": "寻找高成长性公司",
  "重点指标": {
    "市值": "≥ 10亿美元",
    "营收增长": "≥ 15%",
    "盈利增长": "≥ 20%",
    "P/E": "可接受 ≤ 50"
  },
  "行业偏好": ["科技", "医疗健康", "消费周期"],
  "适合": "风险偏好较高、追求高回报的投资者"
}
```

#### 策略3：股息投资
```json
{
  "name": "Dividend Investing",
  "description": "寻找高股息、稳定分红的公司",
  "重点指标": {
    "市值": "≥ 50亿美元",
    "上市年限": "≥ 10年",
    "连续盈利": "≥ 5年",
    "股息率": "≥ 3%"
  },
  "行业偏好": ["公用事业", "必需消费品", "医疗健康"],
  "适合": "追求稳定现金流、低风险的投资者"
}
```

#### 策略4：GARP（合理价格的成长股）
```json
{
  "name": "Quality at Reasonable Price (GARP)",
  "description": "在合理价格买入优质成长股",
  "重点指标": {
    "营收增长": "≥ 10%",
    "盈利增长": "≥ 12%",
    "ROE": "≥ 15%",
    "P/E": "≤ 30"
  },
  "特点": "平衡成长与价值",
  "适合": "追求稳健成长的投资者"
}
```

#### 策略5：保守投资
```json
{
  "name": "Conservative (Safety First)",
  "description": "保守投资策略，注重安全性和稳定性",
  "重点指标": {
    "市值": "≥ 100亿美元",
    "上市年限": "≥ 10年",
    "连续盈利": "≥ 7年",
    "ROE": "≥ 18%",
    "债务比": "≤ 0.3",
    "流动比率": "≥ 2.0"
  },
  "适合": "极端保守的投资者"
}
```

### 运行筛选

```bash
# 使用价值投资策略筛选
python cli.py screen "Value Investing (Buffett Style)"

# 使用成长投资策略
python cli.py screen "Growth Investing"

# 使用API
curl -X POST http://localhost:5001/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Value Investing (Buffett Style)"}'
```

### 创建自定义筛选标准

```bash
# 使用 API 创建自定义策略
curl -X POST http://localhost:5001/api/screening/criteria \
  -H "Content-Type: application/json" \
  -d '{
    "name": "我的AI科技股策略",
    "description": "专注AI领域的科技股",
    "is_active": true,
    "min_market_cap": 50.0,
    "min_years_listed": 3,
    "min_revenue_growth": 20.0,
    "max_pe_ratio": 40.0,
    "included_sectors": ["Technology"],
    "custom_criteria": {
      "ai_related": true,
      "cloud_computing": true
    }
  }'
```

---

## 5. 投资框架使用

### 查看所有检查点

```bash
# 使用 SQLite 查看
sqlite3 ../data/stock_trading.db "
SELECT
  order_index,
  category,
  name,
  question,
  is_required
FROM investment_framework
ORDER BY order_index;
"
```

### 投资框架分类

#### 业务质量（3项）
1. **护城河分析** ⭐ 必选
   - 问题：公司是否具有可持续的竞争优势？
   - 指引：检查品牌价值、网络效应、成本优势、专利/技术、转换成本

2. **商业模式清晰度** ⭐ 必选
   - 问题：我是否完全理解这家公司如何赚钱？
   - 指引：如果不能用简单语言解释，不要投资

3. **行业前景** ⭐ 必选
   - 问题：公司所在行业是否有长期增长潜力？
   - 指引：避开夕阳产业

#### 财务健康（3项）
4. **盈利能力** ⭐ 必选
5. **现金流健康** ⭐ 必选
6. **债务水平** ⭐ 必选

#### 估值（2项）
7. **估值合理性** ⭐ 必选
8. **增长与估值匹配** ⭐ 必选

#### 管理层（2项）
9. **管理层诚信** ⭐ 必选
10. **资本配置能力** - 可选

#### 风险评估（3项）
12. **监管风险** ⭐ 必选
13. **技术变革风险** ⭐ 必选
14. **集中度风险** - 可选

### 实际使用场景

```bash
# 为某只股票创建框架检查记录
# （这个功能在第三阶段会更完善）

# 当前可以通过 SQLite 手动创建
sqlite3 ../data/stock_trading.db "
INSERT INTO framework_check_results
  (stock_id, framework_id, passed, answer, notes, created_at, updated_at)
VALUES
  (1, 1, 1, '是的，Apple有强大的品牌和生态系统',
   'iOS生态系统形成了高转换成本',
   datetime('now'), datetime('now'));
"
```

---

## 6. API完整测试

### 健康检查
```bash
curl http://localhost:5001/health
```

### 股票管理API
```bash
# 获取所有股票
curl http://localhost:5001/api/stocks

# 添加股票
curl -X POST http://localhost:5001/api/stocks \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AMD", "name": "Advanced Micro Devices"}'

# 获取单个股票
curl http://localhost:5001/api/stocks/AMD

# 更新股票
curl -X PUT http://localhost:5001/api/stocks/AMD \
  -H "Content-Type: application/json" \
  -d '{"notes": "AI芯片竞争者", "sector": "Technology"}'

# 刷新Yahoo Finance数据
curl -X POST http://localhost:5001/api/stocks/AMD/refresh

# 获取财务数据
curl http://localhost:5001/api/stocks/AMD/financials

# 删除（软删除）
curl -X DELETE http://localhost:5001/api/stocks/AMD
```

### 数据采集API
```bash
# 获取SEC公司信息
curl http://localhost:5001/api/data/sec/company-info/NVDA

# 抓取SEC数据
curl -X POST "http://localhost:5001/api/data/sec/fetch/NVDA?years=5"

# 批量抓取
curl -X POST http://localhost:5001/api/data/sec/batch-fetch \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["NVDA", "AMD", "INTC"], "years": 3}'
```

### 筛选API
```bash
# 获取所有筛选标准
curl http://localhost:5001/api/screening/criteria

# 运行筛选
curl -X POST http://localhost:5001/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Growth Investing"}'
```

### 新闻API（预留）
```bash
# 获取新闻（目前返回空，第三阶段实现）
curl http://localhost:5001/api/news

# 获取每日摘要
curl http://localhost:5001/api/news/digest
```

---

## 7. 数据分析示例

### 分析单只股票的财务趋势

```bash
# 创建分析脚本
cat > analyze_stock.py << 'EOF'
import sqlite3
import json

def analyze_stock(symbol):
    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    # 获取股票ID
    cursor.execute("SELECT id, name FROM stocks WHERE symbol = ?", (symbol,))
    result = cursor.fetchone()
    if not result:
        print(f"股票 {symbol} 不存在")
        return

    stock_id, name = result
    print(f"\n{'='*60}")
    print(f"  {symbol} - {name} 财务分析")
    print(f"{'='*60}\n")

    # 获取财务数据
    cursor.execute("""
        SELECT
            fiscal_year,
            revenue,
            net_income,
            profit_margin,
            roe,
            revenue_growth,
            earnings_growth
        FROM financial_data
        WHERE stock_id = ?
        ORDER BY fiscal_year DESC
    """, (stock_id,))

    financials = cursor.fetchall()

    if not financials:
        print("暂无财务数据")
        return

    print("年度  |  营收(亿)  |  净利(亿)  | 利润率 |  ROE  | 营收增长 | 盈利增长")
    print("-" * 80)

    for row in financials:
        year, rev, income, margin, roe, rev_growth, earn_growth = row
        rev_b = rev / 1e9 if rev else 0
        income_b = income / 1e9 if income else 0

        print(f"{year}  | ${rev_b:8.2f}B | ${income_b:7.2f}B | {margin:5.1f}% | {roe:5.1f}% | "
              f"{rev_growth:6.1f}% | {earn_growth:7.1f}%")

    conn.close()

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    analyze_stock(symbol)
EOF

python analyze_stock.py AAPL
```

### 对比多只股票

```bash
cat > compare_stocks.py << 'EOF'
import sqlite3

def compare_stocks(symbols):
    conn = sqlite3.connect('../data/stock_trading.db')
    cursor = conn.cursor()

    print(f"\n{'='*80}")
    print("  多股票对比分析")
    print(f"{'='*80}\n")

    print(f"{'股票':<8} | {'市值(亿)':<10} | {'P/E':<6} | {'P/B':<6} | {'股息率':<7} | {'ROE':<6}")
    print("-" * 80)

    for symbol in symbols:
        cursor.execute("""
            SELECT
                symbol,
                name,
                market_cap,
                pe_ratio,
                pb_ratio,
                dividend_yield
            FROM stocks
            WHERE symbol = ?
        """, (symbol,))

        result = cursor.fetchone()
        if not result:
            continue

        sym, name, mcap, pe, pb, div = result

        # 获取最新ROE
        cursor.execute("""
            SELECT roe
            FROM financial_data
            WHERE stock_id = (SELECT id FROM stocks WHERE symbol = ?)
            ORDER BY fiscal_year DESC
            LIMIT 1
        """, (symbol,))

        roe_result = cursor.fetchone()
        roe = roe_result[0] if roe_result else None

        print(f"{sym:<8} | ${mcap or 0:>8.1f}B | {pe or 0:>5.1f} | {pb or 0:>5.2f} | "
              f"{(div or 0)*100:>5.2f}% | {roe or 0:>5.1f}%")

    conn.close()

if __name__ == "__main__":
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]
    compare_stocks(symbols)
EOF

python compare_stocks.py
```

### 筛选结果分析

```bash
# 运行筛选并保存结果
curl -X POST http://localhost:5001/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Value Investing (Buffett Style)"}' \
  > screening_results.json

# 分析结果
python -c "
import json
with open('screening_results.json') as f:
    data = json.load(f)
    print(f'\n筛选策略: {data[\"criteria\"]}')
    print(f'匹配数量: {data[\"matches\"]}')
    print(f'\n符合条件的股票:')
    for stock in data.get('data', []):
        print(f'  - {stock[\"symbol\"]}: 评分 {stock.get(\"screening_score\", 0):.1f}')
"
```

---

## 🎯 实战演练任务

### 任务1：建立科技股池
```bash
# 1. 添加FAANG + 芯片股
python cli.py add META "Meta Platforms"
python cli.py add AMZN "Amazon"
python cli.py add NFLX "Netflix"
python cli.py add NVDA "NVIDIA"
python cli.py add AMD "AMD"

# 2. 抓取SEC数据
# （注意：SEC有速率限制，建议间隔执行）
```

### 任务2：价值投资筛选
```bash
# 1. 运行价值投资筛选
python cli.py screen "Value Investing (Buffett Style)"

# 2. 分析结果
# 3. 针对符合条件的股票深入研究
```

### 任务3：创建自定义策略
```bash
# 根据你的投资偏好，创建一个自定义筛选标准
# 提示：考虑你的风险承受能力、投资期限、行业偏好
```

---

## 📊 监控和调试

### 查看日志
```bash
# 实时查看服务器日志
tail -f ../logs/server.log

# 查看应用日志
tail -f ../logs/app.log
```

### 数据库备份
```bash
# 备份数据库
cp ../data/stock_trading.db ../data/stock_trading_backup_$(date +%Y%m%d).db

# 恢复数据库
# cp ../data/stock_trading_backup_YYYYMMDD.db ../data/stock_trading.db
```

### 性能监控
```bash
# 查看数据库大小
du -h ../data/stock_trading.db

# 统计表记录数
sqlite3 ../data/stock_trading.db "
SELECT
  'stocks' as table_name, COUNT(*) as count FROM stocks
UNION ALL
SELECT 'financial_data', COUNT(*) FROM financial_data
UNION ALL
SELECT 'annual_reports', COUNT(*) FROM annual_reports
UNION ALL
SELECT 'screening_criteria', COUNT(*) FROM screening_criteria
UNION ALL
SELECT 'investment_framework', COUNT(*) FROM investment_framework;
"
```

---

## 🚀 下一步

完成探索后，你可以：

1. **进入第三阶段** - 实现新闻采集和AI分析
2. **优化当前功能** - 添加更多筛选策略、完善投资框架
3. **开发桌面UI** - 使用Electron创建可视化界面
4. **数据可视化** - 添加图表展示财务趋势

准备好了就告诉我！🎊
