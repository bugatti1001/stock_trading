# Getting Started Guide

## 🎉 恭喜！你的股票分析系统第一阶段已完成

我们已经成功搭建了股票交易分析系统的基础框架。以下是快速开始指南。

## ✅ 已完成的功能

### 第一阶段：数据库 & 股票池管理
- ✅ 完整的项目结构
- ✅ SQLite 数据库（7张表）
- ✅ 股票池 CRUD 操作
- ✅ RESTful API 服务器
- ✅ CLI 测试工具
- ✅ Yahoo Finance 集成（基础）

## 🚀 快速开始

### 1. 启动服务器

```bash
cd /Users/hongyuany/Documents/claude_folder/stock_trading/backend
source venv/bin/activate
python run.py
```

服务器将在 `http://localhost:5001` 启动

### 2. 使用 CLI 工具

在另一个终端窗口：

```bash
cd /Users/hongyuany/Documents/claude_folder/stock_trading/backend
source venv/bin/activate

# 健康检查
python cli.py health

# 添加股票到池子
python cli.py add AAPL "Apple Inc."
python cli.py add MSFT "Microsoft Corporation"
python cli.py add GOOGL "Alphabet Inc."

# 列出所有股票
python cli.py list

# 查看单个股票详情
python cli.py get AAPL

# 刷新股票数据（从 Yahoo Finance）
python cli.py refresh AAPL

# 从池子中移除股票
python cli.py remove AAPL
```

### 3. 使用 API（通过 curl 或 Postman）

```bash
# 健康检查
curl http://localhost:5001/health

# 获取所有股票
curl http://localhost:5001/api/stocks

# 添加股票
curl -X POST http://localhost:5001/api/stocks \
  -H "Content-Type: application/json" \
  -d '{"symbol": "TSLA", "name": "Tesla Inc."}'

# 获取单个股票
curl http://localhost:5001/api/stocks/AAPL

# 刷新股票数据
curl -X POST http://localhost:5001/api/stocks/AAPL/refresh

# 更新股票信息
curl -X PUT http://localhost:5001/api/stocks/AAPL \
  -H "Content-Type: application/json" \
  -d '{"notes": "持续关注这只股票"}'

# 从池子中移除
curl -X DELETE http://localhost:5001/api/stocks/AAPL
```

## 📊 数据库结构

系统包含以下数据表：

1. **stocks** - 股票池主表
2. **financial_data** - 财务数据（季度/年度）
3. **annual_reports** - 年报存储
4. **screening_criteria** - 筛选标准配置
5. **investment_framework** - 投资框架检查清单
6. **framework_check_results** - 框架检查结果
7. **news** - 新闻收集

数据库位置：`/Users/hongyuany/Documents/claude_folder/stock_trading/data/stock_trading.db`

## 🔧 常用命令

### 数据库管理

```bash
# 初始化数据库（创建所有表）
python run.py init-db

# 删除数据库（危险！）
python run.py drop-db

# 查看帮助
python run.py help
```

### 开发工具

```bash
# 运行测试（未来添加）
pytest

# 代码格式化
black app/

# 代码检查
flake8 app/
```

## ⚠️ 已知问题与解决方案

### 1. Yahoo Finance 速率限制

**问题**：添加股票时遇到 429 错误（Too Many Requests）

**原因**：Yahoo Finance 有速率限制

**解决方案**：
- 等待几分钟后重试
- 或者先添加股票但不抓取数据：
  ```python
  # 在代码中设置 fetch_data=False
  stock_service.add_stock("AAPL", "Apple Inc.", fetch_data=False)
  ```
- 未来可以申请 Alpha Vantage API key（更稳定）

### 2. SSL 警告

**问题**：看到 `NotOpenSSLWarning` 警告

**说明**：这是 macOS 系统的已知问题，不影响功能

**可选解决**：升级到 Python 3.11+ 或忽略此警告

## 📝 配置文件

编辑 `backend/.env` 文件来配置：

```env
# 数据库
DATABASE_URL=sqlite:///../data/stock_trading.db

# API Keys（获取后填入）
ALPHA_VANTAGE_API_KEY=your_key_here
NEWS_API_KEY=your_key_here

# 服务器
PORT=5001
DEBUG=True
```

## 🎯 下一步开发计划

### 阶段 2：数据抓取 & 筛选系统
- [ ] SEC EDGAR 爬虫（获取年报）
- [ ] 财务数据提取器
- [ ] 股票筛选引擎实现
- [ ] 定时任务（每日更新）

### 阶段 3：配置管理 & 投资框架
- [ ] 筛选标准 UI
- [ ] 投资框架检查清单管理
- [ ] 交易前验证系统

### 阶段 4：信息收集 & 分析
- [ ] 新闻采集器（Yahoo, Reuters, Bloomberg）
- [ ] AI 行业动态跟踪
- [ ] 公司公告监控
- [ ] 每日新闻摘要生成
- [ ] AI 对话分析功能

### 桌面应用（可选）
- [ ] Electron + React 前端
- [ ] 数据可视化
- [ ] 实时更新

## 💡 使用提示

1. **开发模式**：服务器在 DEBUG=True 时会自动重载代码
2. **日志文件**：查看 `logs/app.log` 了解详细运行信息
3. **数据持久化**：所有数据保存在 SQLite 数据库中
4. **扩展性**：后续可轻松迁移到 PostgreSQL

## 📞 获取帮助

如果遇到问题，请检查：
1. 虚拟环境是否激活（`source venv/bin/activate`）
2. 所有依赖是否安装（`pip install -r requirements.txt`）
3. 数据库是否初始化（`python run.py init-db`）
4. 端口 5001 是否被占用

## 🎊 总结

第一阶段已成功完成！系统现在可以：
- ✅ 管理股票池（添加、删除、查询）
- ✅ 存储股票基础信息
- ✅ 提供 RESTful API
- ✅ 集成 Yahoo Finance（基础）

准备好继续第二阶段的开发了吗？
