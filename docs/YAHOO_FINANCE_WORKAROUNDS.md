# 🔧 Yahoo Finance 限流问题解决方案

## 问题描述

Yahoo Finance API经常出现以下问题：
- 🚫 **429错误**：Too Many Requests
- ⏱️ **超时**：请求响应缓慢或超时
- 🔒 **IP限制**：同一IP短时间内请求过多被封禁
- ❌ **数据缺失**：部分股票数据无法获取

---

## 💡 解决方案汇总

### 方案1: 使用演示数据（推荐，最快）⭐⭐⭐⭐⭐

**优点**：
- ✅ 无需API调用，立即可用
- ✅ 数据完整，包含3年财务数据
- ✅ 适合测试和演示

**使用方法**：
```bash
cd backend
source venv/bin/activate
python add_demo_data.py
```

**包含的股票**：
- AAPL (Apple Inc.)
- MSFT (Microsoft Corporation)
- GOOGL (Alphabet Inc.)
- NIO (NIO Inc.)

**数据范围**：
- 基本信息：市值、P/E、P/B、股息率等
- 财务数据：2021-2023年度数据
- 关键指标：ROE、利润率、流动比率、负债率等

---

### 方案2: 添加请求延迟和重试机制 ⭐⭐⭐⭐

**实现方式**：修改数据服务，添加智能重试

```python
import time
import random
from requests.exceptions import RequestException

class DataServiceWithRetry:
    def __init__(self):
        self.retry_delays = [2, 5, 10, 30]  # 重试延迟（秒）

    def fetch_with_retry(self, url, max_retries=3):
        """带重试的请求"""
        for attempt in range(max_retries):
            try:
                # 添加随机延迟，避免被识别为机器人
                time.sleep(random.uniform(1, 3))

                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    return response
                elif response.status_code == 429:
                    # 限流，等待后重试
                    delay = self.retry_delays[attempt]
                    print(f"遇到限流，等待{delay}秒后重试...")
                    time.sleep(delay)
                else:
                    break

            except RequestException as e:
                if attempt < max_retries - 1:
                    delay = self.retry_delays[attempt]
                    print(f"请求失败: {e}，{delay}秒后重试...")
                    time.sleep(delay)
                else:
                    raise

        return None
```

**优点**：
- ✅ 自动重试失败的请求
- ✅ 智能延迟，避免触发限流
- ✅ 适合小规模数据获取

**缺点**：
- ❌ 批量获取数据速度慢
- ❌ 仍可能被限流

---

### 方案3: 使用SEC EDGAR API（官方数据）⭐⭐⭐⭐⭐

**优点**：
- ✅ 官方数据源，稳定可靠
- ✅ 无限流限制（需要User-Agent）
- ✅ 数据权威，适合正式使用

**当前状态**：✅ 已集成

系统已经集成了SEC EDGAR API，可以获取美股公司的官方财务数据。

**使用方法**：
```bash
# 通过API获取SEC数据
curl -X POST http://localhost:5002/api/data/fetch/AAPL/sec
```

**支持的数据**：
- 10-K年报
- 10-Q季报
- 8-K临时公告
- 财务报表数据

---

### 方案4: 使用代理池轮换 IP ⭐⭐⭐

**适用场景**：需要大量抓取数据

**实现方式**：
```python
import requests

PROXIES = [
    {'http': 'http://proxy1.com:8080'},
    {'http': 'http://proxy2.com:8080'},
    {'http': 'http://proxy3.com:8080'},
]

def fetch_with_proxy(url):
    for proxy in PROXIES:
        try:
            response = requests.get(url, proxies=proxy, timeout=10)
            if response.status_code == 200:
                return response
        except:
            continue
    return None
```

**优点**：
- ✅ 绕过IP限制
- ✅ 提高成功率

**缺点**：
- ❌ 需要购买/维护代理
- ❌ 增加复杂度
- ❌ 成本较高

---

### 方案5: 使用付费数据服务 ⭐⭐⭐⭐

**推荐服务**：

#### Alpha Vantage
- **价格**：免费版5次/分钟，付费版无限制
- **数据**：股票、外汇、加密货币
- **API**: 简单易用
- **官网**: https://www.alphavantage.co/

```python
import requests

API_KEY = 'your_api_key'
symbol = 'AAPL'
url = f'https://www.alphavantage.co/query?function=OVERVIEW&symbol={symbol}&apikey={API_KEY}'
response = requests.get(url)
data = response.json()
```

#### Financial Modeling Prep
- **价格**：免费版250次/天，$14/月起
- **数据**：财务报表、估值、历史数据
- **API**: RESTful，文档完善
- **官网**: https://financialmodelingprep.com/

#### IEX Cloud
- **价格**：免费版50K次/月，$9/月起
- **数据**：实时行情、财务数据
- **API**: 高性能
- **官网**: https://iexcloud.io/

---

### 方案6: 本地缓存机制 ⭐⭐⭐⭐

**优点**：
- ✅ 减少重复请求
- ✅ 提高响应速度
- ✅ 降低被限流风险

**实现方式**：

```python
import json
import os
from datetime import datetime, timedelta

class DataCache:
    def __init__(self, cache_dir='cache'):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, key, max_age_hours=24):
        """获取缓存数据"""
        cache_file = os.path.join(self.cache_dir, f"{key}.json")

        if not os.path.exists(cache_file):
            return None

        # 检查缓存是否过期
        file_time = datetime.fromtimestamp(os.path.getmtime(cache_file))
        if datetime.now() - file_time > timedelta(hours=max_age_hours):
            return None

        with open(cache_file, 'r') as f:
            return json.load(f)

    def set(self, key, data):
        """设置缓存数据"""
        cache_file = os.path.join(self.cache_dir, f"{key}.json")
        with open(cache_file, 'w') as f:
            json.dump(data, f)
```

**使用示例**：
```python
cache = DataCache()

# 尝试从缓存获取
data = cache.get(f'stock_{symbol}')
if data is None:
    # 缓存未命中，从API获取
    data = fetch_from_yahoo(symbol)
    cache.set(f'stock_{symbol}', data)
```

---

## 🎯 推荐方案组合

### 开发/测试环境
1. **使用演示数据脚本** - 快速测试功能
2. **使用SEC EDGAR API** - 获取真实数据
3. **添加本地缓存** - 避免重复请求

### 生产环境
1. **SEC EDGAR API（主）** - 官方财务数据
2. **Alpha Vantage（辅）** - 实时价格和补充数据
3. **本地缓存 + 定时刷新** - 优化性能
4. **请求限流和重试机制** - 稳定性保障

---

## 📋 当前系统集成状态

### ✅ 已实现
- SEC EDGAR API集成
- 演示数据脚本
- 基础错误处理

### 🔜 计划实现（Phase 5）
- 智能重试机制
- 本地缓存系统
- Alpha Vantage集成
- 代理池支持（可选）

---

## 🛠️ 快速修复步骤

### 步骤1: 立即使用演示数据
```bash
cd backend
python add_demo_data.py
```

### 步骤2: 验证数据
```bash
# 访问Web界面
http://localhost:5002/stocks

# 或通过API查询
curl http://localhost:5002/api/stocks | jq '.data[] | {symbol, market_cap, pe_ratio}'
```

### 步骤3: 运行筛选测试
```bash
# 访问筛选页面
http://localhost:5002/screening

# 或通过API
curl -X POST http://localhost:5002/api/screening/run \
  -H "Content-Type: application/json" \
  -d '{"criteria_name": "Value Investing (Buffett Style)"}'
```

---

## 💡 最佳实践

### 1. 数据获取策略
- 📊 **基本信息**：使用演示数据或Yahoo Finance（低频）
- 📈 **财务数据**：优先使用SEC EDGAR
- 💹 **实时价格**：考虑Alpha Vantage或IEX Cloud
- 📰 **新闻数据**：Yahoo Finance RSS（已实现）

### 2. 请求频率控制
```python
# 建议的请求间隔
YAHOO_FINANCE_DELAY = 2  # 秒
SEC_EDGAR_DELAY = 0.1    # SEC允许每秒10次
ALPHA_VANTAGE_DELAY = 12 # 免费版每分钟5次
```

### 3. 错误处理
```python
try:
    data = fetch_stock_data(symbol)
except RateLimitError:
    # 方案1: 使用缓存数据
    data = cache.get(f'stock_{symbol}')
    if data is None:
        # 方案2: 切换到备用数据源
        data = fetch_from_sec(symbol)
except Exception as e:
    logger.error(f"获取{symbol}数据失败: {e}")
    return None
```

---

## 📞 获取帮助

### 问题排查
1. 检查网络连接
2. 查看服务器日志：`tail -f server.log`
3. 验证API密钥配置
4. 确认请求频率是否过高

### 联系方式
- 查看文档：项目根目录的 `.md` 文件
- 提交Issue：GitHub项目页面

---

**最后更新**: 2026-02-11
**适用版本**: v0.4.1+
