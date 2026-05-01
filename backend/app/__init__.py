from flask import Flask, session as flask_session
from flask_cors import CORS
from app.config.database import db_session, bind_user_session, get_all_engines
import os
import logging

logger = logging.getLogger(__name__)


def create_app():
    """Application factory pattern"""
    app = Flask(__name__)

    # Load configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    app.config['DEBUG'] = os.getenv('DEBUG', 'True').lower() == 'true'
    app.config['SCHEDULER_ENABLED'] = os.getenv('SCHEDULER_ENABLED', 'True').lower() == 'true'
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB 上传限制

    # Enable CORS
    CORS(app)

    # Register teardown function
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db_session.remove()

    # Register API blueprints (all imported from app.api)
    from app.api import (
        stock_bp, data_collection_bp, scheduler_bp, data_source_bp,
        report_bp, agent_bp, trade_bp, principle_bp, media_bp,
    )
    app.register_blueprint(stock_bp, url_prefix='/api/stocks')
    app.register_blueprint(data_collection_bp, url_prefix='/api/data')
    app.register_blueprint(scheduler_bp, url_prefix='/api/scheduler')
    app.register_blueprint(data_source_bp, url_prefix='/api/data-sources')
    app.register_blueprint(report_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(trade_bp)
    app.register_blueprint(principle_bp)
    app.register_blueprint(media_bp)

    # Register Web UI blueprint
    from app.web_routes import bp as web_bp
    app.register_blueprint(web_bp)

    # Register auth blueprint and before_request hook
    from app.auth import auth_bp, login_required
    app.register_blueprint(auth_bp)
    app.before_request(login_required)

    # Bind db_session to the logged-in user's database on each request
    @app.before_request
    def _bind_user_db():
        username = flask_session.get('username')
        if username:
            bind_user_session(username)

    # Run database migrations on ALL user databases
    _run_migrations_all_users()

    # Initialize scheduler service
    if app.config.get('SCHEDULER_ENABLED', True):
        try:
            from app.services.scheduler_service import scheduler_service
            scheduler_service.init_app(app)
            logger.info("✅ 定时任务调度器已初始化")
        except Exception as e:
            logger.error(f"❌ 定时任务调度器初始化失败: {e}")

    # Health check endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok', 'message': 'Stock Trading API is running'}

    return app


def _run_migrations_all_users():
    """Run all migrations on every user database."""
    from app.models.financial_data import migrate_financial_data_v3
    from migrations.add_market_currency import run as migrate_market_currency
    from migrations.add_data_source_column import run as migrate_data_source
    from migrations.fix_us_stock_currency import run as fix_us_currency
    from migrations.add_include_principles import run as migrate_include_principles
    from migrations.fix_shares_outstanding_unit import run as fix_shares_unit
    from migrations.add_ai_trade_id import run as migrate_ai_trade_id
    from migrations.add_ai_trader_column import run as migrate_ai_trader
    from migrations.add_ta_recommendation_records import run as migrate_ta_recommendations
    from migrations.add_performance_indexes import run as migrate_performance_indexes

    migrations = [
        ('数据库迁移检查', migrate_financial_data_v3),
        ('市场/货币迁移检查', migrate_market_currency),
        ('数据来源列迁移检查', migrate_data_source),
        ('美股货币/来源修复', fix_us_currency),
        ('对话投资原则字段迁移检查', migrate_include_principles),
        ('shares_outstanding单位修复', fix_shares_unit),
        ('AI交易讨论字段迁移', migrate_ai_trade_id),
        ('AI交易trader字段迁移', migrate_ai_trader),
        ('TA推荐历史表迁移', migrate_ta_recommendations),
        ('查询性能索引迁移', migrate_performance_indexes),
    ]

    for username, eng in get_all_engines().items():
        for label, migrate_fn in migrations:
            try:
                migrate_fn(eng)
            except Exception as e:
                logger.warning(f"[{username}] {label}: {e}")
