"""
Financial Data Extractor — v3 对齐 Raw 表
Extracts and processes financial data from SEC EDGAR and other sources.
所有衍生比率已改为通过 kpi_calculator 实时计算，不再存入 DB。
"""
from datetime import datetime
from typing import Dict, List, Optional
from app.models.financial_data import FinancialData, ReportPeriod
from app.models.annual_report import AnnualReport
from app.models.stock import Stock


class FinancialDataExtractor:
    """Extract and process financial data from SEC filings and other sources"""

    def __init__(self, db_session):
        self.db = db_session

    def save_sec_financial_data(
        self,
        stock: Stock,
        sec_data: Dict,
        fiscal_year: int
    ) -> Optional[FinancialData]:
        """
        Save financial data from SEC EDGAR to database.
        只存原始字段，衍生比率由 kpi_calculator 实时计算。

        Args:
            stock: Stock model instance
            sec_data: Financial data from SEC API
            fiscal_year: Fiscal year

        Returns:
            Created FinancialData instance or None
        """
        try:
            metrics = sec_data.get('metrics', {})

            # Check if data already exists
            existing = self.db.query(FinancialData).filter_by(
                stock_id=stock.id,
                fiscal_year=fiscal_year,
                period=ReportPeriod.ANNUAL
            ).first()

            if existing:
                financial_data = existing
            else:
                financial_data = FinancialData(
                    stock_id=stock.id,
                    fiscal_year=fiscal_year,
                    period=ReportPeriod.ANNUAL,
                    report_date=datetime.utcnow().date()
                )

            # SEC 数据固定 USD
            financial_data.currency = 'USD'

            # 利润表
            financial_data.revenue = metrics.get('revenue')
            financial_data.cost_of_revenue = metrics.get('cost_of_revenue')
            financial_data.operating_income = metrics.get('operating_income')
            financial_data.net_income = metrics.get('net_income')
            financial_data.net_income_to_parent = metrics.get('net_income_to_parent')
            financial_data.adjusted_net_income = metrics.get('adjusted_net_income')
            financial_data.selling_expense = metrics.get('selling_expense')
            financial_data.admin_expense = metrics.get('admin_expense')
            financial_data.rd_expense = metrics.get('rd_expense')
            financial_data.finance_cost = metrics.get('finance_cost')

            # 资产负债表
            financial_data.total_assets = metrics.get('total_assets')
            financial_data.total_equity = metrics.get('stockholders_equity') or metrics.get('total_equity')
            financial_data.cash_and_equivalents = metrics.get('cash') or metrics.get('cash_and_equivalents')
            financial_data.accounts_receivable = metrics.get('accounts_receivable')
            financial_data.inventory = metrics.get('inventory')
            financial_data.investments = metrics.get('investments')
            financial_data.accounts_payable = metrics.get('accounts_payable')
            financial_data.short_term_borrowings = metrics.get('short_term_borrowings')
            financial_data.long_term_borrowings = metrics.get('long_term_borrowings')
            financial_data.current_liabilities = metrics.get('current_liabilities')
            financial_data.non_current_assets = metrics.get('non_current_assets')

            # 现金流
            financial_data.operating_cash_flow = metrics.get('operating_cash_flow')
            financial_data.capital_expenditure = metrics.get('capital_expenditure')

            # 每股数据
            financial_data.shares_outstanding = metrics.get('shares_outstanding')
            financial_data.dividends_per_share = metrics.get('dividends_per_share')

            # nav_per_share 后补
            from app.services.kpi_calculator import backfill_nav_per_share
            backfill_nav_per_share(financial_data)

            if not existing:
                self.db.add(financial_data)

            self.db.commit()
            self.db.refresh(financial_data)

            return financial_data

        except Exception as e:
            self.db.rollback()
            print(f"Error saving financial data: {str(e)}")
            return None

    def save_annual_report_metadata(
        self,
        stock: Stock,
        filing: Dict
    ) -> Optional[AnnualReport]:
        """
        Save annual report metadata to database

        Args:
            stock: Stock model instance
            filing: Filing information from SEC

        Returns:
            Created AnnualReport instance or None
        """
        try:
            # Extract fiscal year from filing date
            filing_date = datetime.strptime(filing['filing_date'], '%Y-%m-%d').date()
            fiscal_year = filing_date.year

            # Check if already exists
            existing = self.db.query(AnnualReport).filter_by(
                accession_number=filing['accession_number']
            ).first()

            if existing:
                return existing

            # Create new record
            report = AnnualReport(
                stock_id=stock.id,
                fiscal_year=fiscal_year,
                report_type=filing['form_type'],
                filing_date=filing_date,
                accession_number=filing['accession_number'],
                filing_url=filing.get('document_url'),
                is_downloaded=False,
                is_processed=False
            )

            self.db.add(report)
            self.db.commit()
            self.db.refresh(report)

            return report

        except Exception as e:
            self.db.rollback()
            print(f"Error saving annual report metadata: {str(e)}")
            return None

    def get_consecutive_profitable_years(self, stock: Stock) -> int:
        """
        Get number of consecutive profitable years

        Args:
            stock: Stock model instance

        Returns:
            Number of consecutive profitable years
        """
        financials = self.db.query(FinancialData).filter_by(
            stock_id=stock.id,
            period=ReportPeriod.ANNUAL
        ).order_by(FinancialData.fiscal_year.desc()).all()

        consecutive_years = 0
        for financial in financials:
            if financial.net_income and financial.net_income > 0:
                consecutive_years += 1
            else:
                break

        return consecutive_years

    def extract_all_for_stock(
        self,
        stock: Stock,
        sec_scraper,
        years: int = 5
    ) -> Dict:
        """
        Extract all financial data for a stock from SEC

        Args:
            stock: Stock model instance
            sec_scraper: SECEdgarScraper instance
            years: Number of years to fetch

        Returns:
            Summary dictionary
        """
        result = {
            'ticker': stock.symbol,
            'filings_saved': 0,
            'financial_data_saved': 0,
            'errors': []
        }

        try:
            # Fetch data from SEC
            print(f"Fetching SEC data for {stock.symbol}...")
            sec_data = sec_scraper.fetch_company_data(stock.symbol, years=years)

            if 'error' in sec_data:
                result['errors'].append(sec_data['error'])
                return result

            # Save annual report metadata
            for filing in sec_data.get('filings', []):
                report = self.save_annual_report_metadata(stock, filing)
                if report:
                    result['filings_saved'] += 1

            # Save financial data
            if sec_data.get('metrics'):
                # Get the most recent filing year
                if sec_data['filings']:
                    latest_filing = sec_data['filings'][0]
                    filing_date = datetime.strptime(
                        latest_filing['filing_date'], '%Y-%m-%d'
                    )
                    fiscal_year = filing_date.year

                    financial_data = self.save_sec_financial_data(
                        stock, sec_data, fiscal_year
                    )
                    if financial_data:
                        result['financial_data_saved'] += 1

            # Get consecutive profitable years
            profitable_years = self.get_consecutive_profitable_years(stock)
            result['consecutive_profitable_years'] = profitable_years

        except Exception as e:
            result['errors'].append(str(e))
            print(f"Error extracting data for {stock.symbol}: {str(e)}")

        return result
