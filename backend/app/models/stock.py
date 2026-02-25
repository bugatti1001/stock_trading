from sqlalchemy import Column, String, Float, Boolean, Date, Text, JSON, Integer, Index
from .base import BaseModel


class Stock(BaseModel):
    """Stock model for managing stock pool"""
    __tablename__ = 'stocks'

    # Basic Information
    symbol = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    exchange = Column(String(20))  # NASDAQ, NYSE, SSE, SZSE, HKEX, etc.
    market = Column(String(5))     # 'US', 'CN', 'HK'
    currency = Column(String(5), default='USD')  # 'USD', 'CNY', 'HKD'

    # Company Details
    sector = Column(String(100))
    industry = Column(String(100))
    description = Column(Text)
    website = Column(String(200))

    # Key Metrics
    market_cap = Column(Float)  # in billions
    ipo_date = Column(Date)
    employees = Column(Integer)

    # Trading Information
    current_price = Column(Float)
    volume = Column(Float)
    avg_volume = Column(Float)

    # Fundamental Data (cached for quick access)
    pe_ratio = Column(Float)
    pb_ratio = Column(Float)
    dividend_yield = Column(Float)
    eps = Column(Float)

    # Additional Info (stored as JSON for flexibility)
    extra_data = Column(JSON)  # For any additional unstructured data

    # Status
    in_pool = Column(Boolean, default=True)  # Whether stock is in active pool
    is_active = Column(Boolean, default=True)  # Whether stock is still trading

    # Notes
    notes = Column(Text)  # User's notes about the stock

    # Indexes for high-frequency queries
    __table_args__ = (
        Index('ix_stocks_in_pool', 'in_pool'),
        Index('ix_stocks_pool_active', 'in_pool', 'is_active'),
        Index('ix_stocks_sector', 'sector'),
    )

    def __repr__(self) -> str:
        return f"<Stock {self.symbol}: {self.name}>"

    def to_dict(self) -> dict:
        """Convert to dictionary with date serialization"""
        data = super().to_dict()
        if self.ipo_date:
            data['ipo_date'] = self.ipo_date.isoformat()
        return data
