"""
定时任务调度服务
使用APScheduler实现后台任务调度
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from app.config.database import db_session

logger = logging.getLogger(__name__)


class SchedulerService:
    """定时任务调度服务"""

    def __init__(self, app=None):
        self.scheduler = None
        self.app = app
        self._is_running = False

        if app:
            self.init_app(app)

    def init_app(self, app):
        """初始化Flask应用"""
        self.app = app

        # 从配置读取是否启用调度器
        enabled = app.config.get('SCHEDULER_ENABLED', True)

        if enabled:
            self.start()

    def start(self):
        """启动调度器"""
        if self._is_running:
            logger.warning("调度器已经在运行")
            return

        try:
            # 创建后台调度器
            self.scheduler = BackgroundScheduler(
                timezone='UTC',
                daemon=True
            )

            # 添加事件监听器
            self.scheduler.add_listener(
                self._job_executed_listener,
                EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
            )

            # 添加所有定时任务
            self._add_jobs()

            # 启动调度器
            self.scheduler.start()
            self._is_running = True

            logger.info("✅ 定时任务调度器已启动")
            self._log_scheduled_jobs()

        except Exception as e:
            logger.error(f"启动调度器失败: {e}")
            raise

    def stop(self):
        """停止调度器"""
        if not self._is_running:
            return

        try:
            if self.scheduler:
                self.scheduler.shutdown(wait=False)
                self._is_running = False
                logger.info("定时任务调度器已停止")
        except Exception as e:
            logger.error(f"停止调度器失败: {e}")

    def _add_jobs(self):
        """添加所有定时任务"""

        # 1. 每天凌晨3点刷新股票池数据（价格、市值等）
        self.scheduler.add_job(
            func=self._refresh_stock_data_job,
            trigger=CronTrigger(hour=3, minute=0),
            id='refresh_stock_data',
            name='刷新股票数据',
            replace_existing=True
        )

        # 2. 每周一凌晨4点刷新财务数据
        self.scheduler.add_job(
            func=self._refresh_financial_data_job,
            trigger=CronTrigger(day_of_week='mon', hour=4, minute=0),
            id='refresh_financial_data',
            name='刷新财务数据',
            replace_existing=True
        )

    @staticmethod
    def _refresh_single_stock(service, symbol: str, refresh_type: str = 'info') -> dict:
        """Refresh a single stock (thread-safe helper for parallel execution)."""
        try:
            if refresh_type == 'financial':
                result = service.fetch_financial_data(symbol)
            else:
                result = service.update_stock_info(symbol)
            return {'symbol': symbol, 'success': result.get('success', False), 'error': result.get('error')}
        except Exception as e:
            return {'symbol': symbol, 'success': False, 'error': str(e)}

    def _refresh_stock_data_job(self):
        """刷新股票数据任务（并行执行）"""
        logger.info("💹 开始执行定时任务: 刷新股票数据")

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time as _time
            from app.services.data_service import DataService
            from app.models.stock import Stock

            stocks = db_session.query(Stock).filter_by(in_pool=True).all()

            if not stocks:
                logger.warning("股票池为空，跳过刷新")
                return

            service = DataService()
            success_count = 0
            error_count = 0

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for i, stock in enumerate(stocks):
                    if i > 0:
                        _time.sleep(1)  # Rate-limit: 1s between submissions
                    future = executor.submit(self._refresh_single_stock, service, stock.symbol, 'info')
                    futures[future] = stock.symbol

                for future in as_completed(futures):
                    result = future.result()
                    if result['success']:
                        success_count += 1
                    else:
                        error_count += 1
                        logger.warning(f"刷新 {result['symbol']} 失败: {result.get('error')}")

            logger.info(
                f"✅ 股票数据刷新完成: "
                f"成功 {success_count} 只, 失败 {error_count} 只"
            )

        except Exception as e:
            logger.error(f"❌ 股票数据刷新任务异常: {e}", exc_info=True)

    def _refresh_financial_data_job(self):
        """刷新财务数据任务（每周一次，并行执行）"""
        logger.info("📊 开始执行定时任务: 刷新财务数据")

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import time as _time
            from app.services.data_service import DataService
            from app.models.stock import Stock

            stocks = db_session.query(Stock).filter_by(in_pool=True).all()

            if not stocks:
                logger.warning("股票池为空，跳过刷新")
                return

            service = DataService()
            success_count = 0
            error_count = 0

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {}
                for i, stock in enumerate(stocks):
                    if i > 0:
                        _time.sleep(1)  # Rate-limit: 1s between submissions
                    future = executor.submit(self._refresh_single_stock, service, stock.symbol, 'financial')
                    futures[future] = stock.symbol

                for future in as_completed(futures):
                    result = future.result()
                    if result['success']:
                        success_count += 1
                    else:
                        error_count += 1
                        logger.warning(f"刷新 {result['symbol']} 财务数据失败: {result.get('error')}")

            logger.info(
                f"✅ 财务数据刷新完成: "
                f"成功 {success_count} 只, 失败 {error_count} 只"
            )

        except Exception as e:
            logger.error(f"❌ 财务数据刷新任务异常: {e}", exc_info=True)

    def _job_executed_listener(self, event):
        """任务执行监听器"""
        if event.exception:
            logger.error(
                f"定时任务执行失败: {event.job_id}, "
                f"异常: {event.exception}"
            )
        else:
            logger.debug(f"定时任务执行成功: {event.job_id}")

    def _log_scheduled_jobs(self):
        """记录所有已调度的任务"""
        if not self.scheduler:
            return

        jobs = self.scheduler.get_jobs()
        if jobs:
            logger.info(f"\n{'='*60}")
            logger.info(f"已调度的定时任务 (共 {len(jobs)} 个):")
            logger.info(f"{'='*60}")

            for job in jobs:
                logger.info(
                    f"  • [{job.id}] {job.name}\n"
                    f"    触发器: {job.trigger}\n"
                    f"    下次执行: {job.next_run_time}"
                )

            logger.info(f"{'='*60}\n")

    def get_jobs_status(self) -> list:
        """获取所有任务状态"""
        if not self.scheduler or not self._is_running:
            return []

        jobs = self.scheduler.get_jobs()
        return [
            {
                'id': job.id,
                'name': job.name,
                'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                'trigger': str(job.trigger)
            }
            for job in jobs
        ]

    def trigger_job(self, job_id: str) -> dict:
        """手动触发任务"""
        if not self.scheduler or not self._is_running:
            return {'success': False, 'error': 'Scheduler not running'}

        try:
            job = self.scheduler.get_job(job_id)
            if not job:
                return {'success': False, 'error': f'Job {job_id} not found'}

            # 立即执行任务
            job.modify(next_run_time=datetime.now(timezone.utc))

            return {
                'success': True,
                'message': f'Job {job_id} triggered successfully'
            }

        except Exception as e:
            logger.error(f"触发任务失败: {e}")
            return {'success': False, 'error': str(e)}

    def pause_job(self, job_id: str) -> dict:
        """暂停任务"""
        if not self.scheduler or not self._is_running:
            return {'success': False, 'error': 'Scheduler not running'}

        try:
            self.scheduler.pause_job(job_id)
            return {'success': True, 'message': f'Job {job_id} paused'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def resume_job(self, job_id: str) -> dict:
        """恢复任务"""
        if not self.scheduler or not self._is_running:
            return {'success': False, 'error': 'Scheduler not running'}

        try:
            self.scheduler.resume_job(job_id)
            return {'success': True, 'message': f'Job {job_id} resumed'}
        except Exception as e:
            return {'success': False, 'error': str(e)}


# 全局调度器实例
scheduler_service = SchedulerService()
