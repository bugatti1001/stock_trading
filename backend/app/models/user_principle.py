from sqlalchemy import Column, String, Text, Boolean, Integer, ForeignKey
from .base import BaseModel


class UserPrinciple(BaseModel):
    """
    用户个人投资原则
    - 通过 AI 讨论提炼，或手动创建
    - is_active=True 时，注入交易日记 AI 分析 和 Dashboard 选股建议
    """
    __tablename__ = 'user_principles'

    title          = Column(String(100), nullable=False)         # 原则标题，如"不追涨原则"
    content        = Column(Text, nullable=False)                # 原则正文（自然语言）
    category       = Column(String(50), nullable=True)           # risk / valuation / selection / behavior
    is_active      = Column(Boolean, default=True, nullable=False)
    source_conv_id = Column(Integer, ForeignKey('conversations.id'), nullable=True)  # 来源对话

    def to_dict(self):
        return {
            'id':             self.id,
            'title':          self.title,
            'content':        self.content,
            'category':       self.category,
            'is_active':      self.is_active,
            'source_conv_id': self.source_conv_id,
            'created_at':     self.created_at.isoformat() if self.created_at else None,
            'updated_at':     self.updated_at.isoformat() if self.updated_at else None,
        }
