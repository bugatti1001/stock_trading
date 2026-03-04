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
import json
import logging
from datetime import datetime, timezone, date, timedelta
from typing import List, Dict, Optional, Tuple, Iterator
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


def _get_existing_today_symbols() -> set:
    """查询当天已存在分析记录的 symbol 集合"""
    today_clauses = _today_filter()
    rows = db_session.query(StockNewsAnalysis.symbol).filter(*today_clauses).all()
    return {r[0] for r in rows}


def _analyze_single_stock(symbol: str, stock_name: str,
                          news_items: List[Dict],
                          principles_text: str,
                          api_key: str = '') -> Optional[Dict]:
    """
    调用 Claude 分析单只股票的新闻（线程安全，不使用 db_session）
    api_key 必须由调用方（主线程）传入，子线程中无法访问 Flask session。
    返回解析后的 dict 或 None
    """
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not provided to _analyze_single_stock")
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


def analyze_news_stream(news_by_symbol: Dict[str, List[Dict]]) -> Iterator[str]:
    """
    流式分析所有股票新闻的生成器。
    每分析完一只股票立即 yield 一个 SSE 事件，前端可实时显示。
    当天已分析的股票自动跳过，不重复调用 AI。

    Yields:
        SSE 格式字符串:
        - data: {"type":"skip","symbol":"...","stock_name":"...","completed":N,"total":M}
        - data: {"type":"analysis","analysis":{...},"completed":N,"total":M}
        - data: {"type":"error","symbol":"...","message":"...","completed":N,"total":M}
        - data: [DONE]
    """
    try:
        api_key: str = get_anthropic_key()
        if not api_key:
            yield f"data: {json.dumps({'type': 'error', 'symbol': '', 'message': '未配置 ANTHROPIC_API_KEY，请在登录时输入', 'completed': 0, 'total': 0}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        _purge_stale_analyses()

        # 检查当天已分析的股票
        existing_symbols: set = _get_existing_today_symbols()

        principles_text: str = build_principles_summary()

        # 查找股票信息
        all_symbols: List[str] = list(news_by_symbol.keys())
        stocks: List[Stock] = db_session.query(Stock).filter(
            Stock.symbol.in_(all_symbols)
        ).all()
        stock_map: Dict[str, Stock] = {s.symbol: s for s in stocks}

        # 分类：跳过 vs 需要分析
        skip_list: List[Tuple[str, str]] = []  # (symbol, stock_name)
        tasks: List[tuple] = []
        for symbol, news_items in news_by_symbol.items():
            if not news_items:
                continue
            stock: Optional[Stock] = stock_map.get(symbol)
            stock_name: str = stock.name if stock else symbol
            if symbol in existing_symbols:
                skip_list.append((symbol, stock_name))
            else:
                tasks.append((symbol, stock_name, news_items))

        total: int = len(skip_list) + len(tasks)
        completed: int = 0

        # 先 yield 跳过的股票
        for symbol, stock_name in skip_list:
            completed += 1
            yield f"data: {json.dumps({'type': 'skip', 'symbol': symbol, 'stock_name': stock_name, 'completed': completed, 'total': total}, ensure_ascii=False)}\n\n"

        if not tasks:
            yield "data: [DONE]\n\n"
            return

        # 并行调用 Claude API
        logger.info(f"Starting parallel news analysis for {len(tasks)} stocks "
                     f"(model={AI_MODEL}, max_workers={min(NEWS_MAX_PARALLEL, len(tasks))})")

        with ThreadPoolExecutor(max_workers=min(NEWS_MAX_PARALLEL, len(tasks))) as executor:
            future_to_symbol: Dict = {}
            for symbol, stock_name, news_items in tasks:
                future = executor.submit(
                    _analyze_single_stock,
                    symbol, stock_name, news_items, principles_text, api_key
                )
                future_to_symbol[future] = (symbol, stock_name, news_items)

            total_timeout = max(len(tasks) * 15, 120) + 60

            try:
                for future in as_completed(future_to_symbol, timeout=total_timeout):
                    symbol, stock_name, news_items = future_to_symbol[future]
                    completed += 1
                    try:
                        result: Optional[Dict] = future.result(timeout=10)
                        if result:
                            # 逐条写入 DB
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
                            db_session.commit()
                            yield f"data: {json.dumps({'type': 'analysis', 'analysis': analysis.to_dict(), 'completed': completed, 'total': total}, ensure_ascii=False)}\n\n"
                        else:
                            logger.warning(f"Analysis failed for {symbol}, skipping")
                            yield f"data: {json.dumps({'type': 'error', 'symbol': symbol, 'message': '分析失败（AI 返回空结果）', 'completed': completed, 'total': total}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        logger.error(f"Analysis thread error for {symbol}: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'symbol': symbol, 'message': str(e), 'completed': completed, 'total': total}, ensure_ascii=False)}\n\n"
            except FuturesTimeoutError:
                pending = [s for f, (s, _, _) in future_to_symbol.items() if not f.done()]
                logger.warning(f"News analysis timed out ({total_timeout}s). Pending: {pending}")
                for f in future_to_symbol:
                    f.cancel()
                for sym in pending:
                    completed += 1
                    yield f"data: {json.dumps({'type': 'error', 'symbol': sym, 'message': '分析超时', 'completed': completed, 'total': total}, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    except Exception as e:
        db_session.rollback()
        logger.error(f"analyze_news_stream failed: {e}", exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'symbol': '', 'message': str(e), 'completed': 0, 'total': 0}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


def analyze_all_news(news_by_symbol: Dict[str, List[Dict]]) -> Dict:
    """
    分析所有股票的新闻（向后兼容的同步包装器）。
    内部消费 analyze_news_stream() 生成器，收集结果返回 dict。

    Args:
        news_by_symbol: { "AAPL": [news_item, ...], "MSFT": [...] }

    Returns:
        { "success": True, "analyses": [...], "total_analyzed": N }
    """
    try:
        analyses = []
        for event_str in analyze_news_stream(news_by_symbol):
            # 解析 SSE 事件
            line = event_str.strip()
            if not line.startswith('data: '):
                continue
            payload = line[6:]
            if payload == '[DONE]':
                break
            try:
                event = json.loads(payload)
                if event.get('type') == 'analysis' and event.get('analysis'):
                    analyses.append(event['analysis'])
                elif event.get('type') == 'error' and event.get('message'):
                    # 如果是全局错误（无 symbol），返回失败
                    if not event.get('symbol'):
                        return {'success': False, 'error': event['message']}
            except json.JSONDecodeError:
                continue

        return {
            'success': True,
            'analyses': analyses,
            'total_analyzed': len(analyses),
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
