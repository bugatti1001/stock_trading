# ⚡ 快速参考卡片

## 🚀 启动系统

```bash
cd /Users/hongyuany/Documents/claude_folder/stock_trading/backend
source venv/bin/activate
python run.py
```

服务器地址: http://localhost:5001

---

## 📝 常用命令

### 数据库管理
```bash
python run.py init-db    # 初始化数据库
python run.py seed       # 添加默认数据
python run.py drop-db    # 删除所有数据（危险！）
```

### CLI 工具
```bash
# 健康检查
python cli.py health

# 股票管理
python cli.py add AAPL "Apple Inc."
python cli.py list
python cli.py get AAPL
python cli.py remove AAPL

# 数据采集
python cli.py fetch-sec AAPL

# 筛选
python cli.py criteria
python cli.py screen "Value Investing (Buffett Style)"
```

### 功能演示
```bash
python demo_features.py    # 完整功能演示
```

---

## 🔧 常用 API 调用

### 股票管理
```bash
# 获取所有股票
curl http://localhost:5001/api/stocks

# 添加股票
curl -X POST http://localhost:5001/api/stocks \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TSLA", "name": "Tesla Inc."}'

# 获取详情
curl http://localhost:5001/api/stocks/AAPL

# 更新
curl -X PUT http://localhost:5001/api/stocks/AAPL \
  -H "Content-Type: application/json" \
  -d '{"notes": "关注点：服务业务增长"}'
```

### 数据采集
```bash
# 抓取 SEC 数据
curl -X POST http://localhost:5001/api/data/sec/fetch/AAPL?years=3

# 批量抓取
curl -X POST http://localhost:5001/api/data/sec/batch-fetch \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["AAPL", "MSFT"], "years": 3}'
```

### 筛选
```bash
# 获取筛选标准
curl http://localhost:5001/api/screening/criteria

# 运行筛选
curl -X POST http://localhost:5001/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Value Investing (Buffett Style)"}'
```

---

## 📊 数据库查询

```bash
# 进入 SQLite
sqlite3 /Users/hongyuany/Documents/claude_folder/stock_trading/data/stock_trading.db

# 常用查询
SELECT * FROM stocks;
SELECT * FROM screening_criteria;
SELECT * FROM investment_framework;
SELECT * FROM financial_data WHERE stock_id = 1;

# 退出
.quit
```

---

## 📚 文档索引

| 文档 | 说明 |
|------|------|
| `README.md` | 项目总览 |
| `GETTING_STARTED.md` | 快速开始指南 |
| `PHASE2_COMPLETE.md` | 第二阶段完整报告 |
| `EXPLORATION_GUIDE.md` | 深度探索指南 ⭐ |
| `QUICK_REFERENCE.md` | 本文档 |

---

## 🎯 5种投资策略速查

| 策略 | 市值 | 年限 | P/E | ROE | 适合 |
|------|------|------|-----|-----|------|
| 价值投资 | ≥100B | ≥5年 | ≤25 | ≥15% | 稳健投资者 |
| 成长投资 | ≥10B | - | ≤50 | - | 进取型投资者 |
| 股息投资 | ≥50B | ≥10年 | ≤20 | ≥12% | 追求现金流 |
| GARP | ≥20B | ≥3年 | ≤30 | ≥15% | 平衡型投资者 |
| 保守投资 | ≥100B | ≥10年 | ≤18 | ≥18% | 极端保守 |

---

## 🏗️ 项目结构速览

```
stock_trading/
├── backend/
│   ├── app/
│   │   ├── api/          # API路由
│   │   ├── models/       # 数据模型（7张表）
│   │   ├── services/     # 业务逻辑
│   │   ├── scrapers/     # 数据爬虫（SEC EDGAR）
│   │   ├── config/       # 配置
│   │   └── utils/        # 工具函数
│   ├── venv/             # Python环境
│   ├── run.py            # 主程序
│   ├── cli.py            # CLI工具
│   └── demo_features.py  # 演示脚本
├── data/                 # 数据库
├── logs/                 # 日志
└── *.md                  # 文档
```

---

## 💡 常见问题

### Q: Yahoo Finance 返回 429 错误？
**A:** 速率限制，等待几分钟后重试。可以考虑使用 Alpha Vantage API。

### Q: SEC 数据抓取失败？
**A:** SEC API 偶尔会有网络问题，建议：
1. 检查网络连接
2. 稍后重试
3. 查看 `logs/server.log` 获取详细错误

### Q: 如何备份数据？
**A:**
```bash
cp data/stock_trading.db data/backup_$(date +%Y%m%d).db
```

### Q: 如何重置系统？
**A:**
```bash
python run.py drop-db
python run.py init-db
python run.py seed
```

---

## 🔗 有用链接

- [SEC EDGAR 主页](https://www.sec.gov/edgar)
- [Yahoo Finance](https://finance.yahoo.com)
- [Flask 文档](https://flask.palletsprojects.com/)
- [SQLAlchemy 文档](https://www.sqlalchemy.org/)

---

## 📞 技术支持

遇到问题？
1. 查看日志：`tail -f logs/server.log`
2. 检查数据库：`sqlite3 data/stock_trading.db`
3. 阅读详细文档：`EXPLORATION_GUIDE.md`

---

**💡 提示**: 将此文件加入书签以便快速查阅！
