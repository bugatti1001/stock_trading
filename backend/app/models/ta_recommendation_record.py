from sqlalchemy import Column, String, Float, Date, Text, Index, UniqueConstraint
from .base import BaseModel


class TaRecommendationRecord(BaseModel):
    """Raw daily TradingAgents recommendation for one analyzed stock."""
    __tablename__ = 'ta_recommendation_records'

    symbol = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    rating = Column(String(20), nullable=False, default='Hold')
    action = Column(String(10), nullable=False, default='hold')
    raw_action = Column(String(20), nullable=True)
    shares = Column(Float, nullable=False, default=0)
    price = Column(Float, nullable=False, default=0)
    amount = Column(Float, nullable=False, default=0)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('symbol', 'trade_date', name='uq_ta_rec_symbol_date'),
        Index('ix_ta_rec_date_symbol', 'trade_date', 'symbol'),
    )

    def to_dict(self) -> dict:
        data = super().to_dict()
        if self.trade_date:
            data['trade_date'] = self.trade_date.isoformat()
        return data
