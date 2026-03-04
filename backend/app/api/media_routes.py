"""
Media/News API Routes
新闻获取、AI分析、分析结果管理
"""
import json
import logging
from datetime import date
from flask import Blueprint, request, Response, stream_with_context

from app.config.database import db_session
from app.models.stock import Stock
from app.scrapers.google_news_scraper import fetch_news_for_stocks
from app.services.news_analysis_service import (
    analyze_all_news, analyze_news_stream, get_all_analyses, delete_analysis
)
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

bp = Blueprint('media', __name__)


@bp.route('/api/media/news', methods=['GET'])
def fetch_news():
    """通过 API 搜索池中股票的最新新闻"""
    try:
        symbols_param = request.args.get('symbols', '')

        if symbols_param:
            symbol_list = [s.strip().upper() for s in symbols_param.split(',') if s.strip()]
            stocks = db_session.query(Stock).filter(
                Stock.symbol.in_(symbol_list),
                Stock.in_pool == True
            ).all()
        else:
            stocks = db_session.query(Stock).filter_by(
                in_pool=True, is_active=True
            ).all()

        if not stocks:
            return success_response(news={}, total=0, stocks_searched=0)

        stock_dicts = [{'symbol': s.symbol, 'name': s.name or s.symbol} for s in stocks]
        stock_names = {s.symbol: s.name or s.symbol for s in stocks}
        news_by_symbol = fetch_news_for_stocks(stock_dicts, days_back=7, num_per_stock=8)
        total = sum(len(items) for items in news_by_symbol.values())

        return success_response(
            news=news_by_symbol,
            stock_names=stock_names,
            total=total,
            stocks_searched=len(stock_dicts)
        )
    except Exception as e:
        logger.error(f"fetch_news error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/media/analyze', methods=['POST'])
def trigger_analysis():
    """触发 AI 新闻分析。重建 stock_news_analysis 表。"""
    try:
        data = request.get_json()
        if not data or not data.get('news_by_symbol'):
            return error_response('缺少 news_by_symbol 数据', 400)

        # 过滤掉没有新闻的股票，如果全部为空则直接返回
        news_by_symbol = {s: items for s, items in data['news_by_symbol'].items() if items}
        if not news_by_symbol:
            return success_response(analyses=[], total_analyzed=0, message='所选股票暂无新闻，跳过分析')

        result = analyze_all_news(news_by_symbol)
        if not result.get('success'):
            return error_response(result.get('error', '分析失败'), 500)

        return success_response(**{k: v for k, v in result.items() if k != 'success'})
    except Exception as e:
        logger.error(f"trigger_analysis error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/media/analyze/stream', methods=['POST'])
def trigger_analysis_stream():
    """流式 AI 新闻分析（SSE）。每分析完一只股票立即推送事件。"""
    try:
        data = request.get_json()
        if not data or not data.get('news_by_symbol'):
            return error_response('缺少 news_by_symbol 数据', 400)

        news_by_symbol = {s: items for s, items in data['news_by_symbol'].items() if items}
        if not news_by_symbol:
            return error_response('所选股票暂无新闻，跳过分析', 400)

        def generate():
            yield from analyze_news_stream(news_by_symbol)

        return Response(
            stream_with_context(generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            }
        )
    except Exception as e:
        logger.error(f"trigger_analysis_stream error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/media/analyses', methods=['GET'])
def list_analyses():
    """获取所有当前股票新闻分析记录"""
    try:
        analyses = get_all_analyses()
        return success_response(analyses=[a.to_dict() for a in analyses])
    except Exception as e:
        logger.error(f"list_analyses error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/media/analyses/<int:analysis_id>', methods=['DELETE'])
def remove_analysis(analysis_id: int):
    """删除某只股票的新闻分析记录"""
    try:
        ok = delete_analysis(analysis_id)
        if not ok:
            return error_response('记录不存在', 404)
        return success_response()
    except Exception as e:
        logger.error(f"remove_analysis error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/media/analyses/export', methods=['GET'])
def export_analyses() -> Response:
    """导出所有新闻分析为 JSON 文件下载"""
    try:
        analyses = get_all_analyses()
        data = [a.to_dict() for a in analyses]
        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        today = date.today().strftime('%Y%m%d')
        return Response(
            json_str,
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename=news_analyses_{today}.json'},
        )
    except Exception as e:
        logger.error(f"export_analyses error: {e}", exc_info=True)
        return error_response(str(e), 500)
