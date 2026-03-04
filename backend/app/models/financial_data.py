"""
FinancialData 模型 — v3
严格对齐 Excel Raw 表字段结构，支持多币种（USD/CNY/HKD）
所有衍生 KPI 由 kpi_calculator.py 实时计算，不存入数据库
"""
from sqlalchemy import Column, String, Float, Integer, ForeignKey, Date, Enum, Text, Index
from sqlalchemy.orm import relationship
import enum
import json
import logging
from .base import BaseModel

logger = logging.getLogger(__name__)


class ReportPeriod(enum.Enum):
    """报告期类型 / Report period type"""
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    ANNUAL = "Annual"


class FinancialData(BaseModel):
    """
    财务数据 — 对齐 Excel Raw 表
    Financial data aligned with Excel Raw sheet columns.
    Only raw (directly-extractable) fields are stored.
    All derived KPIs are computed at query time by kpi_calculator.
    """
    __tablename__ = 'financial_data'

    # ── Foreign Key ──
    stock_id = Column(Integer, ForeignKey('stocks.id'), nullable=False)

    # ── Period Information / 期间信息 ──
    fiscal_year = Column(Integer, nullable=False)           # 财年
    period = Column(Enum(ReportPeriod), nullable=False)     # 报告期 (Q1~Q4 / Annual)
    report_date = Column(Date, nullable=False)              # 报告截止日 / Period end date

    # ── Report Metadata / 报告元数据 ──
    report_name = Column(String(100))                       # 报告名称 e.g. "2023年报", "FY2024 20-F"  (Raw B列)
    currency = Column(String(10), default='USD')            # 货币单位 USD/CNY/HKD

    # ══════════════════════════════════════════════════════════
    #  Income Statement / 利润表
    # ══════════════════════════════════════════════════════════
    revenue = Column(Float)                                 # 营业收入 / Revenue  (Raw Q列)
    cost_of_revenue = Column(Float)                         # 营业成本 / Cost of Revenue  (Raw R列)
    operating_income = Column(Float)                        # 营业利润 / Operating Income  (Raw S列)
    net_income = Column(Float)                              # 净利润 / Net Income  (Raw T列)
    net_income_to_parent = Column(Float)                    # 归母利润 / Net Income to Parent  (Raw U列)
    adjusted_net_income = Column(Float)                     # 扣非利润 / Adjusted Net Income  (Raw W列)

    # ══════════════════════════════════════════════════════════
    #  Expense Breakdown / 费用明细
    # ══════════════════════════════════════════════════════════
    selling_expense = Column(Float)                         # 销售费用 / Selling Expense  (Raw X列)
    admin_expense = Column(Float)                           # 管理费用 / Admin Expense  (Raw Y列)
    rd_expense = Column(Float)                              # 研发费用 / R&D Expense  (Raw Z列)
    finance_cost = Column(Float)                            # 融资成本 / Finance Cost  (Raw AA列)

    # ══════════════════════════════════════════════════════════
    #  Balance Sheet / 资产负债表
    # ══════════════════════════════════════════════════════════
    cash_and_equivalents = Column(Float)                    # 货币资金 / Cash & Equivalents  (Raw AB列)
    accounts_receivable = Column(Float)                     # 应收账款 / Accounts Receivable  (Raw AC列)
    inventory = Column(Float)                               # 库存 / Inventory  (Raw AD列)
    investments = Column(Float)                             # 可变现金融资产(不含长期股权投资) / Liquid investments (excl. strategic equity)
    accounts_payable = Column(Float)                        # 应付账款 / Accounts Payable  (Raw AF列)
    contract_liability_change_pct = Column(Float)           # 合同负债变动% / Contract Liability Change %  (Raw AG列)
    short_term_borrowings = Column(Float)                   # 短期贷款 / Short-term Borrowings  (Raw AH列)
    long_term_borrowings = Column(Float)                    # 长期贷款 / Long-term Borrowings  (Raw AI列)
    total_assets = Column(Float)                            # 总资产 / Total Assets  (Raw AJ列)
    total_equity = Column(Float)                            # 归母权益 / Equity to parent (excl. minority interest)
    non_current_assets = Column(Float)                      # 非流动资产 / Non-current Assets  (Raw AL列)
    current_liabilities = Column(Float)                     # 流动负债 / Current Liabilities  (Raw AM列)

    # ══════════════════════════════════════════════════════════
    #  Cash Flow / 现金流量表
    # ══════════════════════════════════════════════════════════
    operating_cash_flow = Column(Float)                     # 经营现金流净额 / Operating Cash Flow  (Raw AO列)
    capital_expenditure = Column(Float)                     # 资本开支 / Capital Expenditure  (Raw AP列)

    # ══════════════════════════════════════════════════════════
    #  Per Share & Shareholder / 每股指标与股东数据
    # ══════════════════════════════════════════════════════════
    shares_outstanding = Column(Float)                      # 总股本(实际股数) / Shares Outstanding (actual number)
    dividends_per_share = Column(Float)                     # 每股分红 / Dividends per Share  (Raw I列)
    nav_per_share = Column(Float)                           # 每股净资产 / NAV per Share  (Raw F列, = total_equity/shares)

    # ══════════════════════════════════════════════════════════
    #  Data Source / 数据来源
    # ══════════════════════════════════════════════════════════
    data_source = Column(String(50))                        # 主数据源: 'Xueqiu', 'Yahoo Finance', 'SEC EDGAR'

    # ══════════════════════════════════════════════════════════
    #  Extended Metrics (JSON) / 扩展指标
    # ══════════════════════════════════════════════════════════
    extended_metrics = Column(Text)                         # JSON: business_segments, moat_indicators, field_sources, etc.

    # ── Relationship ──
    stock = relationship("Stock", backref="financial_data")

    # ── Indexes ──
    __table_args__ = (
        Index('ix_fin_stock_year', 'stock_id', 'fiscal_year'),
        Index('ix_fin_stock_period', 'stock_id', 'fiscal_year', 'period'),
        Index('ix_fin_stock_id', 'stock_id'),
    )

    # ── extended_metrics JSON helpers ──

    @property
    def extended_metrics_dict(self) -> dict:
        """Parse extended_metrics JSON string to dict"""
        if self.extended_metrics:
            try:
                return json.loads(self.extended_metrics) if isinstance(self.extended_metrics, str) else self.extended_metrics
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @extended_metrics_dict.setter
    def extended_metrics_dict(self, value):
        """Store dict as JSON string"""
        if value is not None:
            self.extended_metrics = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        else:
            self.extended_metrics = None

    def to_dict(self) -> dict:
        """Convert to dictionary with proper enum and date serialization"""
        data = super().to_dict()
        if self.period:
            data['period'] = self.period.value
        if self.report_date:
            data['report_date'] = self.report_date.isoformat()
        data['extended_metrics'] = self.extended_metrics_dict
        return data

    def __repr__(self) -> str:
        return f"<FinancialData {self.stock_id} - {self.fiscal_year} {self.period.value}>"


def migrate_financial_data_v3(engine):
    """Backward compatible entry - actual logic in migrations/"""
    from migrations.migrate_financial_data_v3 import run
    run(engine)
