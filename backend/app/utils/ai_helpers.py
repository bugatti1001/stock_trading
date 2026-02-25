"""
Shared AI helper functions - deduplicated from ai_agent_service and news_analysis_service.
"""
import re
import json
import logging
from typing import Optional, Dict

from app.config.database import db_session
from app.models.user_principle import UserPrinciple
from app.config.settings import CATEGORY_LABELS

logger = logging.getLogger(__name__)


def build_principles_summary() -> str:
    """Build active user principles summary text for AI prompts.
    Shared by ai_agent_service, news_analysis_service, and trade analysis.
    """
    try:
        principles = (db_session.query(UserPrinciple)
                      .filter_by(is_active=True)
                      .order_by(UserPrinciple.created_at)
                      .all())
        if not principles:
            return "（暂无已激活的个人投资原则）"

        lines = []
        for p in principles:
            cat = CATEGORY_LABELS.get(p.category, p.category or '其他')
            lines.append(f"[{cat}] {p.title}：{p.content}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"构建用户原则摘要失败: {e}")
        return "（获取用户原则时出错）"


def parse_ai_json_response(raw: str) -> Optional[Dict]:
    """Parse AI response text, stripping markdown code blocks.
    Returns parsed dict or None on failure.
    """
    raw = raw.strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"AI 响应 JSON 解析失败: {e}, raw={raw[:200]}")
        return None
