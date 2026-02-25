from .stock_routes import bp as stock_bp
from .data_collection_routes import bp as data_collection_bp
from .scheduler_routes import bp as scheduler_bp
from .data_source_routes import bp as data_source_bp
from .report_routes import bp as report_bp
from .agent_routes import bp as agent_bp
from .trade_routes import bp as trade_bp
from .principle_routes import bp as principle_bp
from .media_routes import bp as media_bp

__all__ = [
    'stock_bp',
    'data_collection_bp',
    'scheduler_bp',
    'data_source_bp',
    'report_bp',
    'agent_bp',
    'trade_bp',
    'principle_bp',
    'media_bp',
]
