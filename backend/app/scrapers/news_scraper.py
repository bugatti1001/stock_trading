"""
新闻采集爬虫
支持从多个数据源采集股票相关新闻
"""
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class NewsScraperError(Exception):
    """新闻爬虫异常"""
    pass


class NewsSourceBase:
    """新闻源基类"""

    def fetch_news(self, symbol: Optional[str] = None, hours: int = 24) -> List[Dict]:
        """
        抓取新闻

        Args:
            symbol: 股票代码（可选）
            hours: 获取最近N小时的新闻

        Returns:
            新闻列表，每条新闻包含：
            - title: 标题
            - summary: 摘要
            - content: 正文（可选）
            - url: 链接
            - source: 来源
            - author: 作者（可选）
            - published_at: 发布时间
            - category: 分类
        """
        raise NotImplementedError


class YahooFinanceNewsSource(NewsSourceBase):
    """Yahoo Finance新闻源"""

    BASE_URL = "https://finance.yahoo.com"
    RSS_URL = "https://finance.yahoo.com/news/rssindex"

    def fetch_news(self, symbol: Optional[str] = None, hours: int = 24) -> List[Dict]:
        """抓取Yahoo Finance新闻"""
        news_list = []

        try:
            if symbol:
                # 获取特定股票的新闻
                news_list.extend(self._fetch_stock_news(symbol, hours))
            else:
                # 获取市场新闻
                news_list.extend(self._fetch_market_news(hours))

        except Exception as e:
            logger.error(f"Yahoo Finance新闻抓取失败: {e}")
            raise NewsScraperError(f"Failed to fetch Yahoo Finance news: {e}")

        return news_list

    def _fetch_stock_news(self, symbol: str, hours: int) -> List[Dict]:
        """抓取特定股票新闻"""
        news_list = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            # Yahoo Finance RSS feed for specific stock
            rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
            feed = feedparser.parse(rss_url)

            for entry in feed.entries:
                try:
                    # 解析发布时间
                    published_at = self._parse_date(entry.get('published'))

                    if published_at and published_at < cutoff_time:
                        continue

                    news_item = {
                        'title': entry.get('title', 'Untitled'),
                        'summary': entry.get('summary', ''),
                        'url': entry.get('link', ''),
                        'source': 'Yahoo Finance',
                        'author': entry.get('author'),
                        'published_at': published_at or datetime.utcnow(),
                        'category': self._categorize_news(entry.get('title', '')),
                        'stock_symbol': symbol
                    }

                    news_list.append(news_item)

                except Exception as e:
                    logger.warning(f"解析新闻条目失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"抓取{symbol}新闻失败: {e}")

        return news_list

    def _fetch_market_news(self, hours: int) -> List[Dict]:
        """抓取市场新闻"""
        news_list = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            # Yahoo Finance 主要新闻RSS
            rss_url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US"
            feed = feedparser.parse(rss_url)

            for entry in feed.entries:
                try:
                    published_at = self._parse_date(entry.get('published'))

                    if published_at and published_at < cutoff_time:
                        continue

                    news_item = {
                        'title': entry.get('title', 'Untitled'),
                        'summary': entry.get('summary', ''),
                        'url': entry.get('link', ''),
                        'source': 'Yahoo Finance',
                        'author': entry.get('author'),
                        'published_at': published_at or datetime.utcnow(),
                        'category': self._categorize_news(entry.get('title', ''))
                    }

                    news_list.append(news_item)

                except Exception as e:
                    logger.warning(f"解析新闻条目失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"抓取市场新闻失败: {e}")

        return news_list

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """解析日期字符串"""
        if not date_str:
            return None

        try:
            # feedparser通常会解析好日期
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            # 移除时区信息，统一使用UTC时间（无时区）
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except Exception:
            return None

    def _categorize_news(self, title: str) -> str:
        """根据标题自动分类新闻"""
        title_lower = title.lower()

        # 关键词匹配
        if any(word in title_lower for word in ['earnings', 'quarter', 'q1', 'q2', 'q3', 'q4', 'revenue']):
            return 'Financial Report'
        elif any(word in title_lower for word in ['dividend', 'payout']):
            return 'Dividend'
        elif any(word in title_lower for word in ['buyback', 'repurchase']):
            return 'Stock Buyback'
        elif any(word in title_lower for word in ['ceo', 'cfo', 'executive', 'appointment']):
            return 'Management'
        elif any(word in title_lower for word in ['ai', 'artificial intelligence', 'machine learning']):
            return 'AI Industry'
        elif any(word in title_lower for word in ['market', 'index', 'dow', 'nasdaq', 's&p']):
            return 'Market Analysis'
        elif any(word in title_lower for word in ['insider', 'sec filing']):
            return 'Insider Trading'
        else:
            return 'Global Headline'


class RSSNewsSource(NewsSourceBase):
    """通用RSS新闻源"""

    def __init__(self, feed_url: str, source_name: str):
        self.feed_url = feed_url
        self.source_name = source_name

    def fetch_news(self, symbol: Optional[str] = None, hours: int = 24) -> List[Dict]:
        """从RSS feed抓取新闻"""
        news_list = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            feed = feedparser.parse(self.feed_url)

            for entry in feed.entries:
                try:
                    published_at = self._parse_date(entry)

                    if published_at and published_at < cutoff_time:
                        continue

                    # 如果指定了股票代码，只获取相关新闻
                    if symbol:
                        title_and_summary = (entry.get('title', '') + ' ' + entry.get('summary', '')).upper()
                        if symbol.upper() not in title_and_summary:
                            continue

                    news_item = {
                        'title': entry.get('title', 'Untitled'),
                        'summary': entry.get('summary', ''),
                        'url': entry.get('link', ''),
                        'source': self.source_name,
                        'author': entry.get('author'),
                        'published_at': published_at or datetime.utcnow(),
                        'category': 'Global Headline'
                    }

                    if symbol:
                        news_item['stock_symbol'] = symbol

                    news_list.append(news_item)

                except Exception as e:
                    logger.warning(f"解析RSS条目失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"抓取RSS新闻失败 ({self.source_name}): {e}")

        return news_list

    def _parse_date(self, entry) -> Optional[datetime]:
        """解析RSS条目日期"""
        # 尝试多个日期字段
        date_fields = ['published_parsed', 'updated_parsed', 'created_parsed']

        for field in date_fields:
            if hasattr(entry, field):
                parsed_time = getattr(entry, field)
                if parsed_time:
                    try:
                        from time import struct_time, mktime
                        if isinstance(parsed_time, struct_time):
                            return datetime.fromtimestamp(mktime(parsed_time))
                    except Exception:
                        pass

        return None


class NewsAggregator:
    """新闻聚合器 - 整合多个新闻源"""

    def __init__(self):
        self.sources = [
            YahooFinanceNewsSource()
        ]

        # 可以添加更多RSS源
        # self.sources.append(RSSNewsSource(
        #     feed_url="https://www.reuters.com/finance/rss",
        #     source_name="Reuters"
        # ))

    def add_source(self, source: NewsSourceBase):
        """添加新闻源"""
        self.sources.append(source)

    def fetch_all_news(self, symbol: Optional[str] = None, hours: int = 24) -> List[Dict]:
        """
        从所有新闻源抓取新闻

        Args:
            symbol: 股票代码（可选）
            hours: 获取最近N小时的新闻

        Returns:
            去重后的新闻列表
        """
        all_news = []
        seen_urls = set()

        for source in self.sources:
            try:
                news_items = source.fetch_news(symbol, hours)

                # 去重（基于URL）
                for item in news_items:
                    url = item.get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_news.append(item)

                logger.info(f"从 {source.__class__.__name__} 获取了 {len(news_items)} 条新闻")

            except Exception as e:
                logger.error(f"新闻源失败 {source.__class__.__name__}: {e}")
                continue

        # 按发布时间降序排序
        all_news.sort(key=lambda x: x.get('published_at', datetime.min), reverse=True)

        return all_news


# 便捷函数
def fetch_stock_news(symbol: str, hours: int = 24) -> List[Dict]:
    """获取特定股票的新闻"""
    aggregator = NewsAggregator()
    return aggregator.fetch_all_news(symbol=symbol, hours=hours)


def fetch_market_news(hours: int = 24) -> List[Dict]:
    """获取市场新闻"""
    aggregator = NewsAggregator()
    return aggregator.fetch_all_news(hours=hours)
