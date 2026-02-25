# ⚡ 快速启动指南

## 🚀 一分钟启动

```bash
# 1. 进入项目目录
cd /Users/hongyuany/Documents/claude_folder/stock_trading/backend

# 2. 激活虚拟环境
source venv/bin/activate

# 3. 启动服务器
python run.py

# 4. 访问应用
# 浏览器打开: http://localhost:5002
```

---

## 📋 核心功能速览

### Web界面
- **仪表盘**: http://localhost:5002/
- **股票池**: http://localhost:5002/stocks
- **股票筛选**: http://localhost:5002/screening
- **新闻中心**: http://localhost:5002/news
- **投资框架**: http://localhost:5002/framework

### 常用操作

#### 1. 添加股票
```
访问 /stocks → 点击"添加股票" → 输入AAPL → 勾选"自动获取数据" → 提交
```

#### 2. 运行筛选
```
访问 /screening → 选择"价值投资" → 点击"运行筛选" → 查看结果
```

#### 3. 查看新闻
```
访问 /news → 选择时间范围（24小时/7天/30天） → 按分类过滤
```

---

## 🎯 API快速测试

### 查看股票列表
```bash
curl http://localhost:5002/api/stocks | jq '.'
```

### 添加股票
```bash
curl -X POST http://localhost:5002/api/stocks \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "fetch_data": true}'
```

### 运行筛选
```bash
curl -X POST http://localhost:5002/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Value Investing (Buffett Style)"}'
```

### 查看定时任务
```bash
curl http://localhost:5002/api/scheduler/jobs | jq '.'
```

### 手动采集新闻
```bash
curl -X POST http://localhost:5002/api/scheduler/test/collect-news \
  -H "Content-Type: application/json" \
  -d '{"type": "market", "hours": 24}'
```

---

## 📊 测试数据准备

### 方法1: 使用演示数据脚本
```bash
cd backend
python add_demo_data.py
```
这将添加4只股票（AAPL, MSFT, GOOGL, NIO）及其3年财务数据。

### 方法2: 通过Web界面添加
1. 访问 http://localhost:5002/stocks
2. 点击"添加股票"
3. 输入股票代码（推荐: AAPL, MSFT, GOOGL, TSLA, NVDA）
4. 勾选"自动获取数据"
5. 重复添加多只股票

### 方法3: 通过API批量添加
```bash
for symbol in AAPL MSFT GOOGL TSLA NVDA; do
  curl -X POST http://localhost:5002/api/stocks \
    -H "Content-Type: application/json" \
    -d "{\"symbol\": \"$symbol\", \"fetch_data\": true}"
  sleep 2
done
```

---

## ⏰ 定时任务管理

### 查看所有任务状态
```bash
curl http://localhost:5002/api/scheduler/jobs | jq '.data[] | {id, name, next_run_time}'
```

### 手动触发任务

**采集市场新闻**:
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/collect_market_news/trigger
```

**刷新股票数据**:
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/refresh_stock_data/trigger
```

**清理旧新闻**:
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/cleanup_old_news/trigger
```

### 暂停/恢复任务

**暂停**:
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/collect_market_news/pause
```

**恢复**:
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/collect_market_news/resume
```

---

## 🔧 常用命令

### 数据库管理
```bash
# 初始化数据库
python init_db.py

# 查看数据库内容（SQLite）
sqlite3 ../data/stocks.db "SELECT * FROM stock;"
sqlite3 ../data/stocks.db "SELECT COUNT(*) FROM news;"
```

### 服务器管理
```bash
# 启动服务器
python run.py

# 查看日志
tail -f server.log

# 停止服务器
# Ctrl+C 或
lsof -ti:5002 | xargs kill -9
```

### 环境管理
```bash
# 激活虚拟环境
source venv/bin/activate

# 退出虚拟环境
deactivate

# 更新依赖
pip install -r requirements.txt --upgrade
```

---

## 🐛 故障排查

### 问题1: 端口占用
```bash
# 查找占用进程
lsof -i:5002

# 杀死进程
lsof -ti:5002 | xargs kill -9
```

### 问题2: 数据库锁定
```bash
# 重置数据库
rm ../data/stocks.db
python init_db.py
```

### 问题3: 依赖缺失
```bash
# 重新安装依赖
pip install -r requirements.txt
```

### 问题4: 股票数据获取失败
- 检查网络连接
- 验证股票代码是否正确
- 查看服务器日志获取详细错误信息

---

## 📚 推荐阅读顺序

**新用户**:
1. README.md - 了解项目概况
2. QUICK_START.md - 本文档
3. GETTING_STARTED.md - 详细入门
4. SCREENING_GUIDE.md - 学习筛选功能

**开发者**:
1. README.md
2. PROJECT_SUMMARY.md - 项目总览
3. PHASE4_COMPLETION.md - 最新功能
4. 源代码探索

---

## 🎯 典型使用流程

### 场景: 寻找价值投资标的

```
1. 启动服务器
   python run.py

2. 添加候选股票（通过Web或API）
   访问 /stocks → 添加 AAPL, MSFT, GOOGL 等

3. 等待数据获取（约30秒/股）
   查看日志确认数据已获取

4. 运行价值投资筛选
   访问 /screening → 选择"Value Investing" → 运行

5. 查看筛选结果
   评分≥80的股票值得深入研究

6. 分析股票详情
   点击股票代码 → 查看财务图表 → 分析趋势

7. 检查投资框架
   访问 /framework → 逐项评估 → 做出决策

8. 持续追踪
   系统自动采集新闻 → 每天刷新数据 → 定期复查
```

---

## 🌟 快速技巧

### 技巧1: 快速筛选多策略
```bash
# 运行所有5种策略
for strategy in "Value Investing (Buffett Style)" "Growth Investing" "Dividend Investing" "Quality at Reasonable Price (GARP)" "Conservative (Safety First)"; do
  echo "=== $strategy ==="
  curl -X POST http://localhost:5002/api/screening/run \
    -H "Content-Type: application/json" \
    -d "{\"criteria_name\": \"$strategy\"}" | jq '.matches'
done
```

### 技巧2: 监控新闻采集
```bash
# 实时查看日志
tail -f server.log | grep "新闻"
```

### 技巧3: 导出股票列表
```bash
curl -s http://localhost:5002/api/stocks | jq -r '.data[] | [.symbol, .name, .market_cap, .pe_ratio] | @csv'
```

### 技巧4: 批量刷新数据
```bash
# 刷新所有股票数据
curl -s http://localhost:5002/api/stocks | jq -r '.data[].symbol' | while read symbol; do
  echo "Refreshing $symbol..."
  curl -X POST http://localhost:5002/api/stocks/$symbol/refresh
  sleep 2
done
```

---

## 📞 获取帮助

### 文档
- **完整文档**: 查看项目根目录下的所有.md文件
- **API文档**: http://localhost:5002/api/docs
- **健康检查**: http://localhost:5002/health

### 日志
```bash
# 查看最近的错误
tail -100 server.log | grep -i error

# 查看定时任务日志
tail -100 server.log | grep -i "定时任务"
```

### 调试
```bash
# 启用调试模式（.env文件）
DEBUG=True
```

---

## ✅ 系统检查清单

启动前确认：
- [ ] Python 3.9+ 已安装
- [ ] 虚拟环境已激活
- [ ] 依赖已安装（requirements.txt）
- [ ] 数据库已初始化（stocks.db存在）
- [ ] 端口5002未被占用

运行中检查：
- [ ] 服务器成功启动（无报错）
- [ ] 定时任务已初始化（5个任务）
- [ ] Web界面可访问
- [ ] API端点响应正常

---

**准备就绪，开始投资分析之旅！** 🚀📊

*最后更新: 2026-02-11*
