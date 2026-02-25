"""
定时任务管理API
"""
import logging
from flask import Blueprint, request

from app.services.scheduler_service import scheduler_service
from app.utils.response import success_response, error_response

bp = Blueprint('scheduler', __name__)
logger = logging.getLogger(__name__)


@bp.route('/jobs', methods=['GET'])
def get_jobs():
    """获取所有定时任务状态"""
    try:
        jobs = scheduler_service.get_jobs_status()
        return success_response(data=jobs, count=len(jobs))
    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        return error_response(str(e), 500)


@bp.route('/jobs/<job_id>/trigger', methods=['POST'])
def trigger_job(job_id: str):
    """手动触发任务"""
    try:
        result = scheduler_service.trigger_job(job_id)
        if result.get('success'):
            return success_response(message=result.get('message', '任务已触发'))
        return error_response(result.get('error', '触发失败'), 400)
    except Exception as e:
        logger.error(f"触发任务失败: {e}")
        return error_response(str(e), 500)


@bp.route('/jobs/<job_id>/pause', methods=['POST'])
def pause_job(job_id: str):
    """暂停任务"""
    try:
        result = scheduler_service.pause_job(job_id)
        if result.get('success'):
            return success_response(message=result.get('message', '任务已暂停'))
        return error_response(result.get('error', '暂停失败'), 400)
    except Exception as e:
        logger.error(f"暂停任务失败: {e}")
        return error_response(str(e), 500)


@bp.route('/jobs/<job_id>/resume', methods=['POST'])
def resume_job(job_id: str):
    """恢复任务"""
    try:
        result = scheduler_service.resume_job(job_id)
        if result.get('success'):
            return success_response(message=result.get('message', '任务已恢复'))
        return error_response(result.get('error', '恢复失败'), 400)
    except Exception as e:
        logger.error(f"恢复任务失败: {e}")
        return error_response(str(e), 500)
