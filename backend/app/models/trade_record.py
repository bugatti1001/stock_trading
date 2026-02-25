from sqlalchemy import Column, String, Float, Integer, Date, Text, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from .base import BaseModel


class TradeRecord(BaseModel):
    """Trading journal with AI analysis"""
    __tablename__ = 'trade_records'

    # Trade details
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=True)
    symbol = Column(String(20), nullable=False, index=True)
    action = Column(String(10), nullable=False)  # 'buy' | 'sell'
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    trade_date = Column(Date, nullable=False)

    # User rationale
    reason_text = Column(Text, nullable=True)

    # AI analysis
    ai_analysis = Column(Text, nullable=True)
    violations = Column(JSON, nullable=True)
    risk_score = Column(Integer, nullable=True)  # 0-100
    suggestions = Column(Text, nullable=True)

    # Data snapshot at trade time
    price_at_trade = Column(Float, nullable=True)
    pe_at_trade = Column(Float, nullable=True)
    pb_at_trade = Column(Float, nullable=True)
    market_cap_at_trade = Column(Float, nullable=True)

    # Relationship
    stock = relationship('Stock', backref='trades', foreign_keys=[stock_id])

    # Indexes
    __table_args__ = (
        Index('ix_trades_symbol_date', 'symbol', 'trade_date'),
        Index('ix_trades_date', 'trade_date'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'symbol': self.symbol,
            'action': self.action,
            'price': self.price,
            'quantity': self.quantity,
            'trade_date': self.trade_date.isoformat() if self.trade_date else None,
            'reason_text': self.reason_text,
            'ai_analysis': self.ai_analysis,
            'violations': self.violations or [],
            'risk_score': self.risk_score,
            'suggestions': self.suggestions,
            'pe_at_trade': self.pe_at_trade,
            'pb_at_trade': self.pb_at_trade,
            'market_cap_at_trade': self.market_cap_at_trade,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
