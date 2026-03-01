"""
Migration: 为 conversations 表新增 include_principles 列。
标记该对话是否将用户投资原则注入 AI 系统 prompt。
"""
import logging
from sqlalchemy import text, inspect

logger = logging.getLogger(__name__)


def run(engine):
    """执行迁移"""
    inspector = inspect(engine)

    if 'conversations' not in inspector.get_table_names():
        logger.info('conversations 表不存在，将由 create_all 创建')
        return

    existing = {col['name'] for col in inspector.get_columns('conversations')}

    if 'include_principles' in existing:
        logger.info('conversations.include_principles 列已存在，跳过')
        return

    with engine.connect() as conn:
        try:
            conn.execute(text(
                'ALTER TABLE conversations ADD COLUMN include_principles BOOLEAN DEFAULT 0 NOT NULL'
            ))
            conn.commit()
            logger.info('✅ 已添加 conversations.include_principles 列')
        except Exception as e:
            logger.error(f'添加 include_principles 列失败: {e}')
            conn.rollback()
