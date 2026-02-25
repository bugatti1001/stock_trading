# 📰 新闻采集与定时任务使用指南

## 🎯 功能概述

本系统实现了自动化的新闻数据采集和定时任务调度功能，能够：
- **自动采集**：每小时自动从Yahoo Finance采集市场新闻
- **股票新闻**：每2小时采集股票池中所有股票的相关新闻
- **智能分类**：基于关键词自动分类新闻（财报、分红、回购等）
- **去重处理**：基于URL自动去重，避免重复存储
- **定时清理**：每天自动清理30天以前的旧新闻
- **数据刷新**：定期自动刷新股票价格和财务数据

---

## 🚀 快速开始

### 1. 启动服务器

```bash
cd backend
source venv/bin/activate
python run.py
```

服务器启动后，定时任务调度器会自动初始化并开始运行。

### 2. 查看已调度的任务

**方法1：通过API**
```bash
curl http://localhost:5002/api/scheduler/jobs | jq '.'
```

**响应示例**：
```json
{
  "success": true,
  "count": 5,
  "data": [
    {
      "id": "collect_market_news",
      "name": "采集市场新闻",
      "next_run_time": "2026-02-11T22:00:00-08:00",
      "trigger": "cron[minute='0']"
    },
    {
      "id": "collect_stock_news",
      "name": "采集股票新闻",
      "next_run_time": "2026-02-11T22:30:00-08:00",
      "trigger": "cron[hour='*/2', minute='30']"
    }
  ]
}
```

### 3. 手动触发新闻采集（测试）

**采集市场新闻**：
```bash
curl -X POST http://localhost:5002/api/scheduler/test/collect-news \
  -H "Content-Type: application/json" \
  -d '{"type": "market", "hours": 24}'
```

**采集特定股票新闻**：
```bash
curl -X POST http://localhost:5002/api/scheduler/test/collect-news \
  -H "Content-Type: application/json" \
  -d '{"type": "stock", "symbol": "AAPL", "hours": 24}'
```

**采集所有股票池新闻**：
```bash
curl -X POST http://localhost:5002/api/scheduler/test/collect-news \
  -H "Content-Type: application/json" \
  -d '{"type": "all", "hours": 24}'
```

---

## 📋 5个定时任务详解

### 1️⃣ 采集市场新闻 (`collect_market_news`)
- **执行时间**：每小时整点（如 10:00, 11:00, 12:00...）
- **采集范围**：Yahoo Finance 市场新闻RSS
- **数据量**：最近2小时的新闻
- **分类**：全球头条、市场分析、AI行业等

**Cron表达式**：`minute='0'`

### 2️⃣ 采集股票新闻 (`collect_stock_news`)
- **执行时间**：每2小时的第30分钟（如 10:30, 12:30, 14:30...）
- **采集范围**：股票池中所有股票的专属新闻
- **数据量**：最近3小时的新闻
- **分类**：公司公告、财务报告、财报电话会等

**Cron表达式**：`hour='*/2', minute='30'`

**说明**：偏移30分钟执行，避免与市场新闻采集同时进行

### 3️⃣ 清理旧新闻 (`cleanup_old_news`)
- **执行时间**：每天凌晨2:00
- **清理范围**：30天以前的新闻记录
- **目的**：节省数据库空间，保持数据新鲜度

**Cron表达式**：`hour='2', minute='0'`

### 4️⃣ 刷新股票数据 (`refresh_stock_data`)
- **执行时间**：每天凌晨3:00
- **刷新内容**：股票池中所有股票的基本信息
  - 当前股价
  - 市值
  - 成交量
  - P/E、P/B比率
  - 股息率

**Cron表达式**：`hour='3', minute='0'`

**注意**：仅刷新基本信息，不包含财务数据

### 5️⃣ 刷新财务数据 (`refresh_financial_data`)
- **执行时间**：每周一凌晨4:00
- **刷新内容**：完整的财务数据（收入、利润、资产负债等）
- **数据来源**：SEC EDGAR API

**Cron表达式**：`day_of_week='mon', hour='4', minute='0'`

**说明**：财务数据更新频率低，每周刷新一次即可

---

## 🎨 任务管理API

### 查看所有任务状态
```bash
GET /api/scheduler/jobs
```

### 手动触发任务
```bash
POST /api/scheduler/jobs/<job_id>/trigger
```

**示例**：
```bash
# 立即触发市场新闻采集
curl -X POST http://localhost:5002/api/scheduler/jobs/collect_market_news/trigger

# 立即触发股票数据刷新
curl -X POST http://localhost:5002/api/scheduler/jobs/refresh_stock_data/trigger
```

### 暂停任务
```bash
POST /api/scheduler/jobs/<job_id>/pause
```

**示例**：
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/cleanup_old_news/pause
```

### 恢复任务
```bash
POST /api/scheduler/jobs/<job_id>/resume
```

**示例**：
```bash
curl -X POST http://localhost:5002/api/scheduler/jobs/cleanup_old_news/resume
```

---

## 🔧 新闻采集服务详解

### 数据源

**Yahoo Finance RSS**：
- 市场新闻：`https://feeds.finance.yahoo.com/rss/2.0/headline`
- 股票新闻：`https://feeds.finance.yahoo.com/rss/2.0/headline?s={SYMBOL}`

### 新闻分类逻辑

系统基于关键词自动分类新闻：

| 分类 | 关键词 |
|------|--------|
| Financial Report (财务报告) | earnings, quarterly, annual report, 10-K, 10-Q |
| Earnings Call (财报电话会) | earnings call, conference call, analyst call |
| Insider Trading (内部交易) | insider, buys, sells, stock sale |
| Dividend (分红) | dividend, payout, yield |
| Stock Buyback (股票回购) | buyback, repurchase, share repurchase |
| Company Announcement (公司公告) | announces, acquisition, merger, partnership |
| AI Industry (AI行业) | AI, artificial intelligence, machine learning |
| Market Analysis (市场分析) | outlook, forecast, prediction, analysis |

**默认分类**：如果不匹配任何关键词，归类为 `Global Headline`

### 去重机制

- **主键**：新闻URL
- **检查逻辑**：保存前先查询数据库，如果URL已存在则跳过
- **数据库约束**：`url` 字段设置为 UNIQUE
- **返回统计**：
  - `total_fetched`: 抓取到的新闻总数
  - `saved`: 成功保存的新闻数量
  - `duplicates`: 重复跳过的数量
  - `errors`: 保存失败的数量

### 数据存储结构

每条新闻包含以下字段：

```python
{
    "title": "新闻标题",
    "summary": "新闻摘要",
    "content": "新闻正文（可选）",
    "url": "原文链接",
    "source": "来源（如 Yahoo Finance）",
    "author": "作者（可选）",
    "published_at": "发布时间",
    "category": "分类枚举",
    "stock_id": "关联的股票ID（可选）",
    "is_important": "是否重要",
    "sentiment_label": "情感标签（POSITIVE/NEGATIVE/NEUTRAL）",
    "ai_summary": "AI生成的摘要（可选）"
}
```

---

## ⚙️ 配置选项

### 环境变量

在 `.env` 文件中配置：

```bash
# 是否启用定时任务调度器（默认：True）
SCHEDULER_ENABLED=True

# 服务器调试模式（默认：True）
DEBUG=True
```

### 禁用调度器

**临时禁用**（当前会话）：
```bash
export SCHEDULER_ENABLED=False
python run.py
```

**永久禁用**（修改 `.env`）：
```
SCHEDULER_ENABLED=False
```

---

## 📊 监控和日志

### 查看服务器日志

```bash
tail -f server.log
```

### 关键日志信息

**调度器启动**：
```
✅ 定时任务调度器已启动
============================================================
已调度的定时任务 (共 5 个):
============================================================
  • [collect_market_news] 采集市场新闻
    触发器: cron[minute='0']
    下次执行: 2026-02-11 22:00:00+00:00
...
```

**任务执行成功**：
```
🗞️  开始执行定时任务: 采集市场新闻
✅ 市场新闻采集完成: 获取 15 条, 保存 12 条, 重复 3 条
```

**任务执行失败**：
```
❌ 市场新闻采集失败: Connection timeout
❌ 股票新闻采集任务异常: [详细错误信息]
```

---

## 🐛 故障排查

### 问题1: 调度器未启动

**症状**：
```
curl http://localhost:5002/api/scheduler/jobs
# 返回空列表或错误
```

**解决方案**：
1. 检查环境变量 `SCHEDULER_ENABLED=True`
2. 查看服务器日志中是否有初始化错误
3. 检查是否正确安装 `apscheduler` 包

### 问题2: 新闻采集无数据

**症状**：
```json
{
  "success": true,
  "total_fetched": 0,
  "saved": 0
}
```

**可能原因**：
- Yahoo Finance RSS暂时无法访问
- 网络连接问题
- 指定的股票代码不存在

**解决方案**：
1. 检查网络连接
2. 手动访问 `https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL` 确认可用性
3. 查看服务器日志中的详细错误信息

### 问题3: 任务触发失败

**症状**：
```json
{
  "success": false,
  "error": "Job collect_market_news not found"
}
```

**解决方案**：
1. 确认 job_id 拼写正确
2. 重启服务器重新初始化调度器
3. 检查调度器是否正在运行：`GET /api/scheduler/jobs`

---

## 💡 使用场景示例

### 场景1: 每天早晨查看昨晚新闻

```bash
# 访问新闻中心页面
http://localhost:5002/news?hours=24

# 或通过API获取
curl http://localhost:5002/api/news?hours=24 | jq '.'
```

### 场景2: 重点关注某只股票的最新消息

```bash
# 手动采集该股票新闻
curl -X POST http://localhost:5002/api/scheduler/test/collect-news \
  -H "Content-Type: application/json" \
  -d '{"type": "stock", "symbol": "TSLA", "hours": 24}'

# 查看该股票的新闻
curl "http://localhost:5002/api/news?stock_symbol=TSLA" | jq '.'
```

### 场景3: 定期数据维护

```bash
# 手动触发旧新闻清理
curl -X POST http://localhost:5002/api/scheduler/jobs/cleanup_old_news/trigger

# 手动刷新股票数据
curl -X POST http://localhost:5002/api/scheduler/jobs/refresh_stock_data/trigger
```

### 场景4: 暂停所有自动任务（系统维护期间）

```bash
# 暂停所有任务
for job in collect_market_news collect_stock_news cleanup_old_news refresh_stock_data refresh_financial_data; do
  curl -X POST http://localhost:5002/api/scheduler/jobs/$job/pause
done

# 维护完成后恢复
for job in collect_market_news collect_stock_news cleanup_old_news refresh_stock_data refresh_financial_data; do
  curl -X POST http://localhost:5002/api/scheduler/jobs/$job/resume
done
```

---

## 🎯 最佳实践

### 1. 数据采集频率建议

- **市场新闻**：每小时一次 ✅（已配置）
- **股票新闻**：每2小时一次 ✅（已配置）
- **股票基本数据**：每天一次 ✅（已配置）
- **财务数据**：每周一次 ✅（已配置）

### 2. 数据清理策略

- **新闻保留期**：30天（可根据需求调整）
- **执行时间**：凌晨低峰期 ✅
- **备份**：清理前建议定期备份数据库

### 3. 性能优化

- **分批处理**：股票池较大时，新闻采集会分批进行
- **错误重试**：单个股票采集失败不影响其他股票
- **超时控制**：每个HTTP请求设置合理超时时间

### 4. 监控建议

- 定期检查调度器状态：`GET /api/scheduler/jobs`
- 关注日志中的错误信息
- 监控数据库新闻表的记录数量
- 验证新闻分类的准确性

---

## 📈 未来规划

### Phase 4.1 - 已完成 ✅
- [x] Yahoo Finance新闻采集
- [x] APScheduler定时任务
- [x] 自动去重和分类
- [x] 任务管理API

### Phase 4.2 - 计划中 🔜
- [ ] 添加更多新闻源（Bloomberg, Reuters, etc.）
- [ ] 情感分析集成（OpenAI API）
- [ ] AI摘要生成
- [ ] 新闻重要性评分
- [ ] 邮件通知（重要新闻推送）
- [ ] Web界面的任务管理页面

### Phase 4.3 - 后续规划 💡
- [ ] 新闻内容全文抓取（newspaper3k）
- [ ] 新闻实体识别（股票、公司、人物）
- [ ] 新闻影响力预测
- [ ] 历史新闻数据回测
- [ ] 新闻事件时间线可视化

---

## 🤝 贡献和反馈

如果你有以下需求：
- 新的新闻源建议
- 分类规则改进
- 定时任务优化建议
- Bug报告

请在项目中提交Issue或直接修改代码提交PR。

---

**祝你使用愉快，随时掌握市场动态！** 📰📈

---

*文档更新时间: 2026-02-11*
*版本: Phase 4.1 - News Collection & Automation*
