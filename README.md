# 📊 Stock Trading Analysis System

一个全面的美股分析和管理系统，基于价值投资理念构建。

## ✨ 功能特性

### Phase 1: 数据库与股票池管理 ✅ (100% 完成)
- ✅ 股票池管理（增删改查）
- ✅ 公司基本信息存储（59个字段）
- ✅ 财务数据提取和存储
- ✅ Yahoo Finance数据集成
- ✅ SEC EDGAR API集成

### Phase 2: 数据抓取与筛选 ✅ (100% 完成)
- ✅ 自动化股票筛选（5种预置策略）
- ✅ 可配置的筛选条件
- ✅ 综合评分系统
- ✅ 筛选结果排序和展示
- ✅ 财务指标计算（ROE、P/E、利润率等）

### Phase 3: Web可视化界面 ✅ (100% 完成)
- ✅ 交互式仪表盘
- ✅ 股票池管理界面
- ✅ 股票详情页（含图表）
- ✅ 筛选策略执行页面
- ✅ 新闻中心页面
- ✅ 投资框架检查清单
- ✅ 响应式设计（支持移动端）

### Phase 4: 信息采集与自动化 ✅ (85% 完成)
- ✅ 新闻数据自动采集（Yahoo Finance）
- ✅ 定时任务调度系统（5个预置任务）
- ✅ 智能新闻分类（10种分类）
- ✅ 自动数据刷新（股价、财务数据）
- ✅ 任务管理API
- 🔜 AI情感分析（计划中）
- 🔜 AI摘要生成（计划中）
- 🔜 邮件通知系统（计划中）

## 🏗️ 技术栈

- **后端**: Python 3.9+ with Flask
- **数据库**: SQLite (开发环境)
- **前端**: Jinja2 + Bootstrap 5 + Chart.js
- **任务调度**: APScheduler
- **数据源**:
  - Yahoo Finance API (股票数据、新闻)
  - SEC EDGAR API (财务报表)
  - RSS订阅（新闻采集）

## 📁 项目结构

```
stock_trading/
├── backend/                 # Python后端
│   ├── app/
│   │   ├── api/            # REST API端点
│   │   ├── models/         # 数据库模型
│   │   ├── services/       # 业务逻辑层
│   │   ├── scrapers/       # 数据爬虫
│   │   ├── config/         # 配置文件
│   │   ├── templates/      # HTML模板
│   │   ├── static/         # 静态资源
│   │   └── utils/          # 工具函数
│   ├── migrations/         # 数据库迁移
│   └── tests/              # 单元测试
├── data/                   # SQLite数据库
├── logs/                   # 应用日志
└── docs/                   # 项目文档
```

## 🚀 快速开始

### 前置要求
- Python 3.9+
- pip
- 互联网连接（获取股票数据）

### 安装步骤

1. **克隆项目并进入后端目录**:
```bash
cd stock_trading/backend
```

2. **创建虚拟环境**:
```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# 或 Windows: venv\Scripts\activate
```

3. **安装依赖**:
```bash
pip install -r requirements.txt
```

4. **配置环境变量**:
```bash
cp .env.example .env
# 编辑 .env 文件，添加API密钥（可选）
```

5. **初始化数据库**:
```bash
python init_db.py
```

6. **启动服务器**:
```bash
python run.py
```

7. **访问应用**:
打开浏览器访问 `http://localhost:5002`

## 📖 使用指南

### 快速入门

1. **添加股票到股票池**:
   - 访问 `http://localhost:5002/stocks`
   - 点击"添加股票"
   - 输入股票代码（如AAPL、MSFT）
   - 勾选"自动获取数据"

2. **查看股票详情**:
   - 在股票池中点击股票代码
   - 查看财务数据、趋势图表

3. **运行股票筛选**:
   - 访问 `http://localhost:5002/screening`
   - 选择筛选策略（如"价值投资"）
   - 查看筛选结果和评分

4. **查看新闻**:
   - 访问 `http://localhost:5002/news`
   - 按时间范围和分类过滤新闻

### 详细文档

- 📘 **[快速入门指南](GETTING_STARTED.md)** - 新用户必读
- 📗 **[股票筛选指南](SCREENING_GUIDE.md)** - 5种筛选策略详解
- 📙 **[Web界面指南](WEB_INTERFACE_GUIDE.md)** - 界面功能说明
- 📕 **[新闻采集指南](NEWS_COLLECTION_GUIDE.md)** - 定时任务和新闻采集
- 📓 **[快速参考](QUICK_REFERENCE.md)** - 常用命令速查

## 🌐 API端点

### 股票管理
- `GET /api/stocks` - 获取所有股票
- `POST /api/stocks` - 添加股票到池
- `GET /api/stocks/{symbol}` - 获取股票详情
- `POST /api/stocks/{symbol}/refresh` - 刷新股票数据
- `DELETE /api/stocks/{symbol}` - 从池中移除

### 筛选功能
- `GET /api/screening/criteria` - 获取所有筛选策略
- `POST /api/screening/run` - 运行筛选

### 新闻功能
- `GET /api/news/` - 获取新闻列表
- `GET /api/news/digest` - 获取每日摘要

### 任务调度
- `GET /api/scheduler/jobs` - 查看所有定时任务
- `POST /api/scheduler/jobs/{id}/trigger` - 手动触发任务
- `POST /api/scheduler/test/collect-news` - 测试新闻采集

## 🎯 5种筛选策略

系统预置了5种经典投资策略：

1. **价值投资 (Buffett Style)** - 寻找稳定盈利、估值合理的优质公司
2. **成长投资** - 关注高成长性公司
3. **股息投资** - 寻找高股息、稳定分红的公司
4. **合理价格优质股 (GARP)** - 平衡质量和成长
5. **保守投资** - 注重安全性和稳定性

详见 [SCREENING_GUIDE.md](SCREENING_GUIDE.md)

## ⏰ 5个定时任务

系统自动执行以下任务：

1. **采集市场新闻** - 每小时整点执行
2. **采集股票新闻** - 每2小时执行
3. **清理旧新闻** - 每天凌晨2:00
4. **刷新股票数据** - 每天凌晨3:00
5. **刷新财务数据** - 每周一凌晨4:00

详见 [NEWS_COLLECTION_GUIDE.md](NEWS_COLLECTION_GUIDE.md)

## 📊 数据模型

### Stock (股票)
- 基本信息（代码、名称、行业、市值等）
- 估值指标（P/E、P/B、股息率等）
- 关联：财务数据、新闻

### FinancialData (财务数据)
- 收入、利润、资产、负债
- 关键比率（ROE、利润率、流动比率等）
- 按财年和周期组织

### News (新闻)
- 标题、摘要、内容、来源
- 自动分类（10种分类）
- 情感标签（可选）

### ScreeningCriteria (筛选策略)
- 可配置的筛选条件
- 多维度指标要求
- 综合评分规则

### InvestmentFramework (投资框架)
- 15项检查清单
- 7个类别分组
- 基于巴菲特价值投资理念

## 🛠️ 开发计划

### 已完成 ✅
- [x] 项目结构搭建
- [x] 数据库架构设计
- [x] 股票池CRUD操作
- [x] Yahoo Finance集成
- [x] SEC EDGAR爬虫
- [x] 股票筛选引擎
- [x] Web可视化界面
- [x] 新闻采集系统
- [x] 定时任务调度

### 进行中 🔄
- [ ] AI情感分析
- [ ] 新闻全文抓取
- [ ] Web任务管理界面

### 计划中 📋
- [ ] 桌面应用（Electron）
- [ ] 邮件通知系统
- [ ] 移动端适配
- [ ] 数据导出功能
- [ ] 回测系统
- [ ] 多用户支持

## 🧪 测试

```bash
# 运行单元测试
pytest tests/

# 运行集成测试
pytest tests/integration/

# 生成覆盖率报告
pytest --cov=app tests/
```

## 📈 性能指标

- **启动时间**: < 3秒
- **API响应时间**: < 200ms (90th percentile)
- **筛选速度**: ~100只股票/秒
- **新闻采集**: ~10条/秒
- **内存占用**: ~150MB (包含调度器)

## 🤝 贡献指南

欢迎提交Issue和Pull Request！

开发前请：
1. Fork项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启Pull Request

## 📄 许可证

Private project - All rights reserved

## 👥 作者

- 项目维护者: hongyuany
- 创建时间: 2026-02-11
- 当前版本: v0.4.1

## 📞 联系方式

如有问题或建议，请：
- 提交GitHub Issue
- 发送邮件至项目维护者

---

**祝你投资顺利！** 📈💰

*最后更新: 2026-02-11*
