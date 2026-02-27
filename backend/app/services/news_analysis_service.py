"""
News Analysis Service
AI 驱动的股票新闻分析服务

功能：
- analyze_all_news():  DELETE ALL → INSERT NEW 重建 stock_news_analysis 表
- build_news_analysis_summary():  构建文本摘要供 AI prompt 注入
- delete_analysis():  删除单条分析
- get_all_analyses():  获取所有当前分析（仅当天）

设计原则：
    新闻分析只保留当天的。过去的新闻没有价值，所有读取接口只返回
    当天的分析记录。每次触发新分析时，先清除所有非今天的旧记录。
"""
import logging
from datetime import datetime, timezone, date, timedelta
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from app.config.database import db_session
from app.config.settings import AI_MODEL, NEWS_MAX_PARALLEL, get_anthropic_key
from app.models.stock import Stock
from app.models.stock_news_analysis import StockNewsAnalysis
from app.utils.ai_helpers import build_principles_summary, parse_ai_json_response

logger = logging.getLogger(__name__)


def _purge_stale_analyses() -> int:
    """Delete all analyses whose analyzed_at is before today (UTC). Returns count deleted."""
    today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
    count = db_session.query(StockNewsAnalysis).filter(
        StockNewsAnalysis.analyzed_at < today_start
    ).delete(synchronize_session='fetch')
    if count:
        db_session.flush()
        logger.info(f"Purged {count} stale (non-today) news analyses")
    return count


def _today_filter() -> Tuple:
    """SQLAlchemy filter clauses: analyzed_at is today (UTC)."""
    utc_today = datetime.now(timezone.utc).date()
    today_start = datetime.combine(utc_today, datetime.min.time(), tzinfo=timezone.utc)
    tomorrow_start = datetime.combine(
        utc_today + timedelta(days=1),
        datetime.min.time(), tzinfo=timezone.utc
    )
    return (StockNewsAnalysis.analyzed_at >= today_start,
            StockNewsAnalysis.analyzed_at < tomorrow_start)


def _analyze_single_stock(symbol: str, stock_name: str,
                          news_items: List[Dict],
                          principles_text: str) -> Optional[Dict]:
    """
    调用 Claude 分析单只股票的新闻（线程安全，不使用 db_session）
    返回解析后的 dict 或 None
    """
    api_key: str = get_anthropic_key()
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not configured")
        return None

    # 构建新闻文本
    news_text_parts: List[str] = []
    for i, item in enumerate(news_items, 1):
        title: str = item.get('title', 'Untitled')
        snippet: str = item.get('snippet', '')
        source: str = item.get('source', '')
        date: str = item.get('published_date', '')
        news_text_parts.append(
            f"{i}. [{source}] {title} ({date})\n   {snippet}"
        )
    news_text: str = "\n".join(news_text_parts) if news_text_parts else "（暂无相关新闻）"

    prompt: str = f"""你是一名专注于基本面分析的投资新闻分析师。请分析以下 {symbol} ({stock_name}) 的近期新闻。

【近期新闻】
{news_text}

【用户个人投资原则】
{principles_text}

请综合分析这些新闻，生成以下内容：
1. 整体情绪判断（bullish / bearish / neutral）
2. 新闻综合摘要分析（300字以内，Markdown格式，重点关注对公司基本面的影响）
3. 关键事件列表（3-5个最重要的事件，简短描述）
4. 这些新闻对用户投资原则的影响评估（逐条分析与用户原则的关系，如果某条原则不相关则跳过）
5. 在分析中区分事实和推测

只返回 JSON，格式如下：
{{
  "sentiment": "bullish",
  "summary": "综合分析的 Markdown 文本...",
  "key_events": ["事件1简述", "事件2简述", "事件3简述"],
  "principle_impacts": ["影响描述1", "影响描述2"]
}}"""

    try:
        import anthropic
        import httpx
        # 显式设置 connect/read/write 各阶段超时，防止网络半连接导致无限挂起
        client = anthropic.Anthropic(
            api_key=api_key,
            timeout=httpx.Timeout(90.0, connect=15.0),
        )
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=2048,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw: str = msg.content[0].text.strip()
        parsed: Optional[Dict] = parse_ai_json_response(raw)
        logger.info(f"Analysis complete for {symbol}: {parsed.get('sentiment', '?') if parsed else '?'}")
        return parsed
    except Exception as e:
        logger.error(f"News analysis failed for {symbol}: {e}", exc_info=True)
        return None


def analyze_all_news(news_by_symbol: Dict[str, List[Dict]]) -> Dict:
    """
    分析所有股票的新闻，重建 stock_news_analysis 表。
    使用线程池并行调用 Claude API，大幅缩短总耗时。

    Args:
        news_by_symbol: { "AAPL": [news_item, ...], "MSFT": [...] }

    Returns:
        { "success": True, "analyses": [...], "total_analyzed": N }
    """
    try:
        # Step 0: 清除所有非今天的旧分析（过去的新闻没有价值）
        _purge_stale_analyses()

        # Step 1: 删除本次要分析的股票的旧分析
        symbols_to_analyze: List[str] = [s for s, items in news_by_symbol.items() if items]
        if symbols_to_analyze:
            db_session.query(StockNewsAnalysis).filter(
                StockNewsAnalysis.symbol.in_(symbols_to_analyze)
            ).delete(synchronize_session='fetch')
            db_session.flush()

        principles_text: str = build_principles_summary()

        # Step 2: 查找股票信息
        all_symbols: List[str] = list(news_by_symbol.keys())
        stocks: List[Stock] = db_session.query(Stock).filter(
            Stock.symbol.in_(all_symbols)
        ).all()
        stock_map: Dict[str, Stock] = {s.symbol: s for s in stocks}

        # Step 3: 准备任务列表（过滤掉空新闻的股票）
        tasks: List[tuple] = []
        for symbol, news_items in news_by_symbol.items():
            if not news_items:
                continue
            stock: Optional[Stock] = stock_map.get(symbol)
            stock_name: str = stock.name if stock else symbol
            tasks.append((symbol, stock_name, news_items))

        if not tasks:
            db_session.commit()
            return {'success': True, 'analyses': [], 'total_analyzed': 0}

        # Step 4: 并行调用 Claude API 分析所有股票
        logger.info(f"Starting parallel news analysis for {len(tasks)} stocks "
                     f"(model={AI_MODEL}, max_workers={min(NEWS_MAX_PARALLEL, len(tasks))})")

        analysis_results: Dict[str, tuple] = {}  # symbol -> (result_dict, stock_name, news_items)

        # 不使用 with 块，避免 executor.shutdown(wait=True) 在线程卡死时永远阻塞
        executor = ThreadPoolExecutor(max_workers=min(NEWS_MAX_PARALLEL, len(tasks)))
        try:
            future_to_symbol: Dict = {}
            for symbol, stock_name, news_items in tasks:
                future = executor.submit(
                    _analyze_single_stock,
                    symbol, stock_name, news_items, principles_text
                )
                future_to_symbol[future] = (symbol, stock_name, news_items)

            # 超时时间 = 每只股票约 15 秒（含 API 限流重试），留 60 秒余量
            total_timeout = max(len(tasks) * 15, 120) + 60

            try:
                for future in as_completed(future_to_symbol, timeout=total_timeout):
                    symbol, stock_name, news_items = future_to_symbol[future]
                    try:
                        result: Optional[Dict] = future.result(timeout=10)
                        if result:
                            analysis_results[symbol] = (result, stock_name, news_items)
                        else:
                            logger.warning(f"Analysis failed for {symbol}, skipping")
                    except Exception as e:
                        logger.error(f"Analysis thread error for {symbol}: {e}")
            except FuturesTimeoutError:
                pending = [s for f, (s, _, _) in future_to_symbol.items() if not f.done()]
                logger.warning(f"News analysis timed out ({total_timeout}s). "
                               f"Completed {len(analysis_results)}, pending: {pending}")
                # 取消尚未开始的任务
                for f in future_to_symbol:
                    f.cancel()
        finally:
            # wait=False: 不阻塞等待卡死的线程，让 Flask 能立即返回响应
            executor.shutdown(wait=False, cancel_futures=True)

        # Step 5: 将结果写入数据库（在主线程中操作 db_session）
        created: List[StockNewsAnalysis] = []
        for symbol, (result, stock_name, news_items) in analysis_results.items():
            stock = stock_map.get(symbol)
            sources: List[Dict] = [
                {'title': n.get('title', ''), 'url': n.get('url', ''),
                 'source': n.get('source', '')}
                for n in news_items
            ]

            analysis = StockNewsAnalysis(
                stock_id=stock.id if stock else None,
                symbol=symbol,
                stock_name=stock_name,
                sentiment=result.get('sentiment', 'neutral'),
                summary=result.get('summary', ''),
                key_events=result.get('key_events', []),
                principle_impacts=result.get('principle_impacts', []),
                news_sources=sources,
                analyzed_at=datetime.now(timezone.utc),
            )
            db_session.add(analysis)
            created.append(analysis)

        db_session.commit()
        logger.info(f"News analysis complete: {len(created)} stocks analyzed")
        return {
            'success': True,
            'analyses': [a.to_dict() for a in created],
            'total_analyzed': len(created),
        }

    except Exception as e:
        db_session.rollback()
        logger.error(f"analyze_all_news failed: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def get_all_analyses() -> List[StockNewsAnalysis]:
    """获取当天的分析记录（过期记录不返回）"""
    _purge_stale_analyses()
    today_clauses = _today_filter()
    return db_session.query(StockNewsAnalysis).filter(
        *today_clauses
    ).order_by(
        StockNewsAnalysis.symbol
    ).all()


def delete_analysis(analysis_id: int) -> bool:
    """删除单条股票新闻分析记录"""
    record: Optional[StockNewsAnalysis] = db_session.query(StockNewsAnalysis).get(analysis_id)
    if not record:
        return False
    db_session.delete(record)
    db_session.commit()
    return True


def build_news_analysis_summary() -> str:
    """
    构建当天新闻分析的文本摘要（供 AI prompt 注入）
    被 ai_agent_service._build_system_prompt() / analyze_trade() /
    generate_dashboard_insight() 调用。

    只返回当天的分析，过去的新闻分析自动忽略。
    """
    try:
        today_clauses = _today_filter()
        analyses: List[StockNewsAnalysis] = db_session.query(StockNewsAnalysis).filter(
            *today_clauses
        ).order_by(
            StockNewsAnalysis.symbol
        ).all()
        if not analyses:
            return "（暂无股票新闻分析数据）"

        lines: List[str] = []
        for a in analyses:
            sentiment_cn: str = {
                'bullish': '看涨', 'bearish': '看跌', 'neutral': '中性'
            }.get(a.sentiment, a.sentiment)

            block: str = f"## {a.symbol} ({a.stock_name or 'N/A'}) -- 情绪: {sentiment_cn}"
            block += f"\n分析摘要: {a.summary}"

            if a.key_events:
                events_str: str = "; ".join(a.key_events[:5])
                block += f"\n关键事件: {events_str}"

            if a.principle_impacts:
                impacts_str: str = "; ".join(a.principle_impacts[:5])
                block += f"\n原则影响: {impacts_str}"

            if a.analyzed_at:
                block += f"\n分析时间: {a.analyzed_at.strftime('%Y-%m-%d %H:%M')}"

            lines.append(block)

        return "\n\n".join(lines)
    except Exception as e:
        logger.error(f"build_news_analysis_summary failed: {e}")
        return "（获取新闻分析数据时出错）"
