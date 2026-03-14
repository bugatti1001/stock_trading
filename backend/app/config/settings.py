"""
Application settings - centralized configuration
Replaces hardcoded values scattered across the codebase
"""
import os

# AI Model Configuration
AI_MODEL = os.getenv('AI_MODEL', 'claude-sonnet-4-5-20250929')
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '4096'))
AI_TRADE_MAX_TOKENS = int(os.getenv('AI_TRADE_MAX_TOKENS', '1024'))

# Conversation settings
CONVERSATION_HISTORY_LIMIT = 20
CONVERSATION_DEFAULT_TITLE = '新对话'

# Data source configuration
DATA_SOURCE_MAX_FAILURES = 3
DATA_SOURCE_COOLDOWN_MINUTES = 15

# Xueqiu (雪球) configuration — for CN A-shares and HK stocks
# Get token from browser cookies: login to xueqiu.com, open DevTools, find xq_a_token in cookies
# If not set, the scraper will auto-fetch a token by visiting the homepage
XUEQIU_TOKEN = os.getenv('XUEQIU_TOKEN', '')

# News analysis
NEWS_MAX_PARALLEL = int(os.getenv('NEWS_MAX_PARALLEL', '5'))

# Pagination defaults
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# File extraction
MAX_TEXT_CHARS = 300_000

# ── Anthropic Rate Limit Configuration ──
ANTHROPIC_RATE_LIMIT_TPM = int(os.getenv('ANTHROPIC_RATE_LIMIT_TPM', '30000'))  # tokens per minute
CHARS_PER_TOKEN_ESTIMATE = 4  # ~4 chars/token for English-dominant SEC filings
PROMPT_TOKEN_ESTIMATE = 1500  # EXTRACTION_PROMPT overhead with safety margin
SAFE_TEXT_CHARS_PER_CHUNK = (ANTHROPIC_RATE_LIMIT_TPM - PROMPT_TOKEN_ESTIMATE) * CHARS_PER_TOKEN_ESTIMATE
CHUNK_OVERLAP_CHARS = 500  # overlap between chunks to avoid losing data at boundaries
RATE_LIMIT_WAIT_SECONDS = int(os.getenv('RATE_LIMIT_WAIT_SECONDS', '65'))  # wait between chunks/retries
MAX_RATE_LIMIT_RETRIES = 3

# API keys (read dynamically to support runtime updates)
def get_anthropic_key() -> str:
    """从当前用户的数据库读取 Claude API Key"""
    try:
        from app.config.database import db_session
        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='anthropic_api_key').first()
        return row.value if row else ''
    except Exception:
        return ''

def get_openai_key() -> str:
    return os.getenv('OPENAI_API_KEY', '')

def get_minimax_key() -> str:
    """从当前用户的数据库读取 MiniMax API Key"""
    try:
        from app.config.database import db_session
        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='minimax_api_key').first()
        return row.value if row else ''
    except Exception:
        return ''

def get_ai_provider() -> str:
    """获取当前用户选择的 AI 提供商: 'claude' 或 'minimax'"""
    try:
        from app.config.database import db_session
        from app.models.user_setting import UserSetting
        row = db_session.query(UserSetting).filter_by(key='ai_provider').first()
        return row.value if row and row.value in ('claude', 'minimax') else 'claude'
    except Exception:
        return 'claude'

# MiniMax Configuration
MINIMAX_BASE_URL = 'https://api.minimaxi.chat/v1'
MINIMAX_DEFAULT_MODEL = 'MiniMax-Text-01'

# Supported AI models
OPENAI_MODELS = {'gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'}
ANTHROPIC_MODELS = {
    'claude-opus-4-5', 'claude-sonnet-4-5', 'claude-haiku-3-5',
    'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022',
    'claude-3-opus-20240229'
}
MINIMAX_MODELS = {'MiniMax-Text-01'}

# Valid principle categories
VALID_CATEGORIES = {'risk', 'valuation', 'selection', 'behavior'}

CATEGORY_LABELS = {
    'risk': '风险管理',
    'valuation': '估值',
    'selection': '选股',
    'behavior': '投资行为',
}
