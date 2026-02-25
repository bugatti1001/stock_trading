"""
Finnhub API 数据获取器
提供实时股价、PE、市值等数据
免费额度：60次/分钟
注册地址：https://finnhub.io/register
"""
import os
import logging
from typing import Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY', '')


class FinnhubScraper:
    """Finnhub 实时数据获取器"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or FINNHUB_API_KEY
        self._client = None

    def _get_client(self):
        if self._client is None:
            import finnhub
            self._client = finnhub.Client(api_key=self.api_key)
        return self._client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """获取实时报价"""
        if not self.is_configured():
            return None
        try:
            client = self._get_client()
            q = client.quote(symbol)
            # q: {c: current, h: high, l: low, o: open, pc: prev_close, t: timestamp}
            if not q or q.get('c', 0) == 0:
                return None
            return {
                'current_price': q.get('c'),
                'high': q.get('h'),
                'low': q.get('l'),
                'open': q.get('o'),
                'prev_close': q.get('pc'),
                'timestamp': q.get('t'),
            }
        except Exception as e:
            logger.warning(f"[Finnhub] Quote {symbol} 失败: {e}")
            return None

    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """获取公司基本信息和关键指标"""
        if not self.is_configured():
            return None
        try:
            client = self._get_client()
            profile = client.company_profile2(symbol=symbol)
            if not profile:
                return None
            return {
                'name': profile.get('name'),
                'exchange': profile.get('exchange'),
                'sector': profile.get('finnhubIndustry'),  # Finnhub 用 finnhubIndustry
                'market_cap': profile.get('marketCapitalization'),  # 单位：百万美元
                'website': profile.get('weburl'),
                'logo': profile.get('logo'),
                'ipo_date': profile.get('ipo'),
                'currency': profile.get('currency'),
                'country': profile.get('country'),
            }
        except Exception as e:
            logger.warning(f"[Finnhub] Profile {symbol} 失败: {e}")
            return None

    def get_basic_financials(self, symbol: str) -> Optional[Dict]:
        """获取关键财务比率（PE、PB、ROE等）"""
        if not self.is_configured():
            return None
        try:
            client = self._get_client()
            data = client.company_basic_financials(symbol, 'all')
            if not data or not data.get('metric'):
                return None
            m = data['metric']
            return {
                'pe_ratio': m.get('peTTM') or m.get('peAnnual'),
                'pb_ratio': m.get('pbAnnual') or m.get('pbQuarterly'),
                'ps_ratio': m.get('psTTM'),
                'dividend_yield': m.get('dividendYieldIndicatedAnnual'),  # 已是百分比形式
                'eps': m.get('epsTTM'),
                'roe': m.get('roeTTM'),          # 百分比，如 15.2 = 15.2%
                'roa': m.get('roaTTM'),
                'profit_margin': m.get('netProfitMarginTTM'),  # 百分比
                'revenue_ttm': m.get('revenueTTM'),             # 百万美元
                'beta': m.get('beta'),
                '52w_high': m.get('52WeekHigh'),
                '52w_low': m.get('52WeekLow'),
            }
        except Exception as e:
            logger.warning(f"[Finnhub] BasicFinancials {symbol} 失败: {e}")
            return None

    def get_all_data(self, symbol: str) -> Optional[Dict]:
        """
        获取股票完整实时数据
        合并 quote + profile + basic_financials
        """
        if not self.is_configured():
            logger.info("[Finnhub] 未配置 API Key，跳过")
            return None

        result = {'symbol': symbol, 'data_source': 'Finnhub'}

        # 1. 实时报价
        quote = self.get_quote(symbol)
        if quote:
            result['current_price'] = quote['current_price']
        else:
            logger.warning(f"[Finnhub] 无法获取 {symbol} 实时报价")

        # 2. 公司资料
        profile = self.get_company_profile(symbol)
        if profile:
            result['name'] = profile.get('name')
            result['exchange'] = profile.get('exchange')
            result['sector'] = profile.get('sector')
            result['website'] = profile.get('website')
            result['ipo_date'] = profile.get('ipo_date')
            # market_cap 单位是百万美元，转为十亿
            if profile.get('market_cap'):
                result['market_cap'] = profile['market_cap'] / 1000.0

        # 3. 关键财务比率
        financials = self.get_basic_financials(symbol)
        if financials:
            result['pe_ratio'] = financials.get('pe_ratio')
            result['pb_ratio'] = financials.get('pb_ratio')
            result['ps_ratio'] = financials.get('ps_ratio')
            result['eps'] = financials.get('eps')
            # dividend_yield: Finnhub 返回的是百分比（如 0.5 = 0.5%），转为小数
            div = financials.get('dividend_yield')
            result['dividend_yield'] = div / 100.0 if div else 0.0
            # roe: Finnhub 返回百分比（如 151.9），转为小数
            roe = financials.get('roe')
            result['roe_pct'] = roe  # 保留百分比格式供显示用
            result['beta'] = financials.get('beta')
            result['roa_pct'] = financials.get('roa')  # 百分比格式

        result['fetched_at'] = datetime.utcnow().isoformat()

        # 至少要有价格或公司名才认为成功
        if result.get('current_price') or result.get('name'):
            return result

        logger.warning(f"[Finnhub] {symbol} 未获取到有效数据")
        return None


# 全局单例
finnhub_scraper = FinnhubScraper()
