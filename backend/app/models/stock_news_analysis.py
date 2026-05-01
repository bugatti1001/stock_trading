from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from .base import BaseModel, _utcnow


class StockNewsAnalysis(BaseModel):
    """AI-generated stock news analysis results"""
    __tablename__ = 'stock_news_analysis'

    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=True)
    symbol = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(200), nullable=True)
    sentiment = Column(String(20), nullable=False)   # bullish / bearish / neutral
    summary = Column(Text, nullable=False)
    key_events = Column(JSON, nullable=True)
    principle_impacts = Column(JSON, nullable=True)
    news_sources = Column(JSON, nullable=True)
    analyzed_at = Column(DateTime, default=_utcnow, nullable=False)

    stock = relationship('Stock', backref='news_analyses', foreign_keys=[stock_id])

    # Indexes
    __table_args__ = (
        Index('ix_news_analysis_stock_id', 'stock_id'),
        Index('ix_news_analysis_analyzed_at', 'analyzed_at'),
        Index('ix_news_analysis_symbol_analyzed_at', 'symbol', 'analyzed_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'stock_id': self.stock_id,
            'symbol': self.symbol,
            'stock_name': self.stock_name,
            'sentiment': self.sentiment,
            'summary': self.summary,
            'key_events': self.key_events or [],
            'principle_impacts': self.principle_impacts or [],
            'news_sources': self.news_sources or [],
            'analyzed_at': self.analyzed_at.isoformat() if self.analyzed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<StockNewsAnalysis {self.symbol} - {self.sentiment}>"
