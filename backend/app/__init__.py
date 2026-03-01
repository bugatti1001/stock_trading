from flask import Flask
from flask_cors import CORS
from app.config.database import db_session
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

    # Run database migrations (safe to re-run, skips existing columns)
    try:
        from app.config.database import engine
        from app.models.financial_data import migrate_financial_data_v3
        migrate_financial_data_v3(engine)
    except Exception as e:
        logger.warning(f"数据库迁移检查: {e}")

    try:
        from app.config.database import engine
        from migrations.add_market_currency import run as migrate_market_currency
        migrate_market_currency(engine)
    except Exception as e:
        logger.warning(f"市场/货币迁移检查: {e}")

    try:
        from app.config.database import engine
        from migrations.add_data_source_column import run as migrate_data_source
        migrate_data_source(engine)
    except Exception as e:
        logger.warning(f"数据来源列迁移检查: {e}")

    try:
        from app.config.database import engine
        from migrations.fix_us_stock_currency import run as fix_us_currency
        fix_us_currency(engine)
    except Exception as e:
        logger.warning(f"美股货币/来源修复: {e}")

    try:
        from app.config.database import engine
        from migrations.add_include_principles import run as migrate_include_principles
        migrate_include_principles(engine)
    except Exception as e:
        logger.warning(f"对话投资原则字段迁移检查: {e}")

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

    # API docs redirect
    @app.route('/api/docs')
    def api_docs():
        return '''
        <html>
        <head><title>API Documentation</title></head>
        <body style="font-family: sans-serif; padding: 20px;">
            <h1>Stock Analysis API Documentation</h1>
            <p>Visit <a href="/">Dashboard</a> for the web interface</p>
            <p>API Endpoints available at <a href="/health">/health</a></p>
        </body>
        </html>
        '''

    return app
