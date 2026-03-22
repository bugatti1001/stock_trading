from sqlalchemy import Column, String, Float, Integer, Date, Text, Index
from .base import BaseModel


class AiTradeRecord(BaseModel):
    """AI 模拟交易记录 — 记录每次 AI 建议的交易"""
    __tablename__ = 'ai_trade_records'

    trader = Column(String(30), nullable=False, default='scorer', index=True)
    symbol = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)  # 'buy' | 'sell'
    shares = Column(Float, nullable=False)
    price = Column(Float, nullable=False)  # 执行时的股价
    trade_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_ai_trades_symbol_date', 'symbol', 'trade_date'),
    )
