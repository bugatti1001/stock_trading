from sqlalchemy import Column, String, Text, Integer, Boolean, ForeignKey, Enum, Index
from sqlalchemy.orm import relationship
import enum
from .base import BaseModel


class ContextMode(enum.Enum):
    GLOBAL = "global"
    STOCK = "stock"
    AI_TRADE = "ai_trade"


class Conversation(BaseModel):
    """AI discussion conversation"""
    __tablename__ = 'conversations'

    title = Column(String(200), nullable=False, default='新对话')
    context_mode = Column(Enum(ContextMode), default=ContextMode.GLOBAL, nullable=False)
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=True)
    ai_trade_id = Column(Integer, nullable=True)
    include_principles = Column(Boolean, default=False, nullable=False)

    messages = relationship('Message', backref='conversation', lazy='selectin',
                            cascade='all, delete-orphan', order_by='Message.created_at')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'title': self.title,
            'context_mode': self.context_mode.value if self.context_mode else 'global',
            'stock_id': self.stock_id,
            'ai_trade_id': self.ai_trade_id,
            'include_principles': bool(self.include_principles),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'message_count': len(self.messages)
        }


class Message(BaseModel):
    """Individual message in a conversation"""
    __tablename__ = 'messages'

    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    context_snapshot = Column(Text, nullable=True)  # JSON string

    # Indexes
    __table_args__ = (
        Index('ix_messages_conv_created', 'conversation_id', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'role': self.role,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
