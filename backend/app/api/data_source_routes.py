"""
数据源状态和管理API
"""
import logging
from flask import Blueprint

from app.services.data_source_manager import data_source_manager
from app.utils.response import success_response, error_response

bp = Blueprint('data_sources', __name__)
logger = logging.getLogger(__name__)


@bp.route('/status', methods=['GET'])
def get_data_source_status():
    """获取所有数据源的状态"""
    try:
        status = data_source_manager.get_data_source_status()
        return success_response(
            data=status,
            summary={
                'total_sources': 3,
                'available': sum(1 for s in status.values() if s['available']),
                'unavailable': sum(1 for s in status.values() if not s['available'])
            }
        )
    except Exception as e:
        logger.error(f"获取数据源状态失败: {e}")
        return error_response(str(e), 500)


@bp.route('/test/<source>', methods=['POST'])
def test_data_source(source: str):
    """测试特定数据源 (yahoo | sec)"""
    try:
        if source == 'yahoo':
            test_data = data_source_manager._fetch_from_yahoo('AAPL')
            if test_data:
                return success_response(
                    source='Yahoo Finance',
                    message='Yahoo Finance连接正常',
                    sample_data={
                        'symbol': test_data.get('symbol'),
                        'name': test_data.get('name'),
                        'price': test_data.get('current_price')
                    }
                )
            return error_response('Yahoo Finance返回空数据', 503)

        elif source == 'sec':
            test_data = data_source_manager._fetch_from_sec('AAPL')
            if test_data:
                return success_response(
                    source='SEC EDGAR',
                    message='SEC EDGAR连接正常',
                    sample_data={
                        'symbol': test_data.get('symbol'),
                        'name': test_data.get('name'),
                        'cik': test_data.get('cik')
                    }
                )
            return error_response('SEC EDGAR返回空数据', 503)

        elif source == 'xueqiu':
            xueqiu = data_source_manager._get_xueqiu()
            if not xueqiu:
                return error_response('Xueqiu scraper not available', 503)
            test_data = xueqiu.get_stock_info('SH600519')
            if test_data:
                return success_response(
                    source='Xueqiu',
                    message='Xueqiu连接正常',
                    sample_data={
                        'symbol': test_data.get('symbol'),
                        'name': test_data.get('name'),
                        'price': test_data.get('current_price')
                    }
                )
            return error_response('Xueqiu返回空数据', 503)

        return error_response(f'Unknown data source: {source}. Use "yahoo", "sec", or "xueqiu"', 400)

    except Exception as e:
        logger.error(f"测试数据源 {source} 失败: {e}")
        return error_response(str(e), 500)


@bp.route('/reset', methods=['POST'])
def reset_data_source_status():
    """重置所有数据源的失败计数"""
    try:
        data_source_manager.status.failures = {}
        return success_response(message='所有数据源状态已重置')
    except Exception as e:
        logger.error(f"重置数据源状态失败: {e}")
        return error_response(str(e), 500)
