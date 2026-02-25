from sqlalchemy import Column, String, Integer, ForeignKey, Date, Text, Boolean, Index
from sqlalchemy.orm import relationship
from .base import BaseModel


class AnnualReport(BaseModel):
    """Annual report storage and metadata"""
    __tablename__ = 'annual_reports'

    # Foreign Key
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=False)

    # Report Information
    fiscal_year = Column(Integer, nullable=False)
    report_type = Column(String(20), nullable=False)  # 10-K, 10-Q, 8-K, etc.
    filing_date = Column(Date, nullable=False)
    period_end_date = Column(Date)

    # SEC Information
    accession_number = Column(String(50), unique=True)
    filing_url = Column(String(500))

    # Document Storage
    file_path = Column(String(500))
    file_size = Column(Integer)  # File size in bytes

    # Processing Status
    is_downloaded = Column(Boolean, default=False)
    is_processed = Column(Boolean, default=False)
    analysis_error = Column(Text)  # 解析失败时存储错误信息，非空表示解析失败

    # Content Summary
    summary = Column(Text)
    key_points = Column(Text)

    # Relationship
    stock = relationship("Stock", backref="annual_reports")

    # Indexes
    __table_args__ = (
        Index('ix_reports_stock_year', 'stock_id', 'fiscal_year'),
        Index('ix_reports_stock_id', 'stock_id'),
    )

    def __repr__(self) -> str:
        return f"<AnnualReport {self.stock_id} - {self.fiscal_year} ({self.report_type})>"
