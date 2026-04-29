import os

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.2",
    "quick_think_llm": "gpt-5-mini",
    "analyst_llm": None,  # If set, analysts use this cheaper model; None = use quick_think_llm
    "backend_url": "https://api.openai.com/v1",
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "yfinance",             # Options: alpha_vantage, yfinance
        "technical_indicators": "yfinance",        # Options: alpha_vantage, yfinance
        "fundamental_data": "stock_analysis",      # Options: stock_analysis, alpha_vantage, yfinance
        "news_data": "stock_analysis",             # Options: stock_analysis, alpha_vantage, yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # insider_transactions has no stock_analysis implementation, keep yfinance
        "get_insider_transactions": "yfinance",
    },
    # stock_analysis SQLite database path
    "stock_analysis_db_path": os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "stock_analysis", "backend", "data", "stock_trading_admin.db",
    ),
}
