"""
SEC EDGAR Scraper
Fetches annual reports (10-K) and quarterly reports (10-Q) from SEC EDGAR database
"""
import requests
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json


class SECEdgarScraper:
    """Scraper for SEC EDGAR filings"""

    BASE_URL = "https://data.sec.gov"
    WWW_BASE_URL = "https://www.sec.gov"
    HEADERS = {
        'User-Agent': 'Stock Analysis System contact@example.com',  # SEC requires user agent
        'Accept-Encoding': 'gzip, deflate',
    }

    def __init__(self, download_dir: str = None):
        """
        Initialize SEC EDGAR scraper

        Args:
            download_dir: Directory to save downloaded filings
        """
        self.download_dir = download_dir or './data/sec_filings'
        os.makedirs(self.download_dir, exist_ok=True)

    def get_company_cik(self, ticker: str) -> Optional[str]:
        """
        Get CIK (Central Index Key) for a company by ticker symbol

        Args:
            ticker: Stock ticker symbol

        Returns:
            CIK number as string, or None if not found
        """
        try:
            # SEC provides a ticker to CIK mapping file (hosted on www.sec.gov)
            url = f"{self.WWW_BASE_URL}/files/company_tickers.json"
            response = requests.get(url, headers=self.HEADERS)
            response.raise_for_status()

            data = response.json()

            # Search for ticker
            for item in data.values():
                if item['ticker'].upper() == ticker.upper():
                    # CIK needs to be 10 digits with leading zeros
                    return str(item['cik_str']).zfill(10)

            return None

        except Exception as e:
            print(f"Error getting CIK for {ticker}: {str(e)}")
            return None

    def get_company_filings(
        self,
        cik: str,
        filing_type: str = "10-K",
        count: int = 5
    ) -> List[Dict]:
        """
        Get recent filings for a company

        Args:
            cik: Company CIK number
            filing_type: Type of filing (10-K, 10-Q, 8-K, etc.)
            count: Number of recent filings to retrieve

        Returns:
            List of filing dictionaries with metadata
        """
        try:
            # SEC submissions endpoint
            url = f"{self.BASE_URL}/submissions/CIK{cik}.json"
            response = requests.get(url, headers=self.HEADERS)
            response.raise_for_status()

            data = response.json()
            filings = data.get('filings', {}).get('recent', {})

            # Filter by filing type
            results = []
            forms = filings.get('form', [])
            filing_dates = filings.get('filingDate', [])
            accession_numbers = filings.get('accessionNumber', [])
            primary_documents = filings.get('primaryDocument', [])

            for i, form in enumerate(forms):
                if form == filing_type and len(results) < count:
                    # Remove dashes from accession number for URL
                    acc_num = accession_numbers[i].replace('-', '')

                    filing = {
                        'form_type': form,
                        'filing_date': filing_dates[i],
                        'accession_number': accession_numbers[i],
                        'primary_document': primary_documents[i],
                        'document_url': f"{self.WWW_BASE_URL}/Archives/edgar/data/{cik.lstrip('0')}/{acc_num}/{primary_documents[i]}"
                    }
                    results.append(filing)

            # Be respectful with rate limiting
            time.sleep(0.1)

            return results

        except Exception as e:
            print(f"Error getting filings for CIK {cik}: {str(e)}")
            return []

    def download_filing(
        self,
        cik: str,
        accession_number: str,
        primary_document: str,
        ticker: str = None
    ) -> Optional[str]:
        """
        Download a specific filing document

        Args:
            cik: Company CIK
            accession_number: Filing accession number
            primary_document: Primary document filename
            ticker: Stock ticker (for filename)

        Returns:
            Path to downloaded file, or None if failed
        """
        try:
            # Build URL - Archives must use www.sec.gov (not data.sec.gov)
            acc_num = accession_number.replace('-', '')
            url = f"{self.WWW_BASE_URL}/Archives/edgar/data/{cik.lstrip('0')}/{acc_num}/{primary_document}"

            # Download
            response = requests.get(url, headers=self.HEADERS)
            response.raise_for_status()

            # Create filename
            ticker_part = f"{ticker}_" if ticker else ""
            filename = f"{ticker_part}{accession_number}_{primary_document}"
            filepath = os.path.join(self.download_dir, filename)

            # Save file
            with open(filepath, 'wb') as f:
                f.write(response.content)

            print(f"Downloaded: {filename}")

            # Rate limiting
            time.sleep(0.1)

            return filepath

        except Exception as e:
            print(f"Error downloading filing: {str(e)}")
            return None

    def get_latest_annual_filing(self, ticker: str) -> Optional[Dict]:
        """
        获取公司最新年报的元数据（不下载文件）
        优先查 10-K（美国公司），若无则查 20-F（外国私人发行人，如 NIO）

        Returns:
            包含 cik, filing_date, accession_number, primary_document, document_url,
            filing_url, form_type 的字典
        """
        cik = self.get_company_cik(ticker)
        if not cik:
            return None

        # 先尝试 10-K
        filings = self.get_company_filings(cik, "10-K", count=1)
        filing_type = "10-K"

        # 没有 10-K 则尝试 20-F（外国公司）
        if not filings:
            filings = self.get_company_filings(cik, "20-F", count=1)
            filing_type = "20-F"

        if not filings:
            return None

        filing = filings[0]
        acc_num_clean = filing['accession_number'].replace('-', '')
        filing['cik'] = cik
        filing['form_type'] = filing_type
        filing['filing_url'] = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type={filing_type}&dateb=&owner=include&count=10"
        )
        filing['viewer_url'] = (
            f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}"
            f"/{acc_num_clean}/{filing['primary_document']}"
        )
        return filing

    def get_latest_10k_filing(self, ticker: str) -> Optional[Dict]:
        """向后兼容别名，调用 get_latest_annual_filing()"""
        return self.get_latest_annual_filing(ticker)

    def get_latest_quarterly_filing(self, ticker: str) -> Optional[Dict]:
        """
        获取公司最新季报的元数据（不下载文件）
        美国公司查 10-Q，外国私人发行人（如 NIO）查最新 20-F（季报通过6-K提交但难以区分，用20-F代替）

        Returns:
            包含 cik, filing_date, accession_number, primary_document,
            filing_url, viewer_url, form_type 的字典
        """
        cik = self.get_company_cik(ticker)
        if not cik:
            return None

        # 先尝试 10-Q（美国公司季报）
        filings = self.get_company_filings(cik, "10-Q", count=1)
        filing_type = "10-Q"

        # 没有 10-Q 则尝试 20-F（外国公司年报，含最近季度财务数据）
        if not filings:
            filings = self.get_company_filings(cik, "20-F", count=1)
            filing_type = "20-F"

        if not filings:
            return None

        filing = filings[0]
        acc_num_clean = filing['accession_number'].replace('-', '')
        filing['cik'] = cik
        filing['form_type'] = filing_type
        filing['filing_url'] = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={cik}&type={filing_type}&dateb=&owner=include&count=10"
        )
        filing['viewer_url'] = (
            f"https://www.sec.gov/Archives/edgar/data/{cik.lstrip('0')}"
            f"/{acc_num_clean}/{filing['primary_document']}"
        )
        return filing

    def get_company_facts(self, cik: str) -> Optional[Dict]:
        """
        Get company facts (financial data in XBRL format)
        This is a newer SEC API that provides structured financial data

        Args:
            cik: Company CIK

        Returns:
            Dictionary of company facts, or None if failed
        """
        try:
            url = f"{self.BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
            response = requests.get(url, headers=self.HEADERS)
            response.raise_for_status()

            data = response.json()

            # Rate limiting
            time.sleep(0.1)

            return data

        except Exception as e:
            print(f"Error getting company facts for CIK {cik}: {str(e)}")
            return None

    def extract_key_metrics(self, company_facts: Dict) -> Dict:
        """
        Extract key financial metrics from company facts (XBRL).
        Covers income statement, balance sheet, cash flow, and per-share data.

        Args:
            company_facts: Raw company facts data from SEC API

        Returns:
            Dictionary of extracted metrics
        """
        metrics = {}

        try:
            us_gaap = company_facts.get('facts', {}).get('us-gaap', {})

            def _first(*concepts):
                """Return latest 10-K value from the first available concept."""
                for c in concepts:
                    if c in us_gaap:
                        v = self._get_latest_value(us_gaap[c])
                        if v is not None:
                            return v
                return None

            # ── Income Statement ──
            metrics['revenue'] = _first(
                'Revenues',
                'RevenueFromContractWithCustomerExcludingAssessedTax',
                'SalesRevenueNet',
            )
            metrics['cost_of_revenue'] = _first(
                'CostOfRevenue',
                'CostOfGoodsAndServicesSold',
                'CostOfGoodsSold',
            )
            metrics['operating_income'] = _first(
                'OperatingIncomeLoss',
            )
            metrics['net_income'] = _first(
                'NetIncomeLoss',
            )
            metrics['net_income_to_parent'] = _first(
                'NetIncomeLossAvailableToCommonStockholdersBasic',
                'NetIncomeLossAttributableToParent',
            )
            metrics['rd_expense'] = _first(
                'ResearchAndDevelopmentExpense',
                'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost',
            )
            metrics['selling_expense'] = _first(
                'SellingGeneralAndAdministrativeExpense',
                'SellingAndMarketingExpense',
            )
            metrics['finance_cost'] = _first(
                'InterestExpense',
                'InterestExpenseDebt',
            )

            # ── Balance Sheet ──
            metrics['total_assets'] = _first('Assets')
            metrics['total_equity'] = _first(
                'StockholdersEquity',
            )
            metrics['cash_and_equivalents'] = _first(
                'CashAndCashEquivalentsAtCarryingValue',
                'CashAndCashEquivalentsAtFairValue',
            )
            metrics['accounts_receivable'] = _first(
                'AccountsReceivableNetCurrent',
                'AccountsReceivableNet',
            )
            metrics['inventory'] = _first('InventoryNet', 'Inventories')
            metrics['investments'] = _first(
                'ShortTermInvestments',
                'MarketableSecuritiesCurrent',
                'AvailableForSaleSecuritiesCurrent',
                'TradingSecuritiesCurrent',
                'InvestmentsCurrent',
            )
            metrics['accounts_payable'] = _first('AccountsPayableCurrent')
            metrics['current_liabilities'] = _first('LiabilitiesCurrent')
            metrics['non_current_assets'] = _first(
                'NoncurrentAssets',
                'AssetsNoncurrent',
            )
            # short_term = pure ST + current portion of LTD
            st_pure = _first('ShortTermBorrowings', 'ShortTermDebtCurrent')
            current_ltd = _first('LongTermDebtCurrent', 'CurrentPortionOfLongTermDebt')
            if st_pure is not None or current_ltd is not None:
                metrics['short_term_borrowings'] = (st_pure or 0) + (current_ltd or 0)
            metrics['long_term_borrowings'] = _first(
                'LongTermDebtNoncurrent',
                'LongTermDebt',
            )

            # ── Cash Flow ──
            metrics['operating_cash_flow'] = _first(
                'NetCashProvidedByUsedInOperatingActivities',
            )
            capex = _first('PaymentsToAcquirePropertyPlantAndEquipment')
            metrics['capital_expenditure'] = abs(capex) if capex is not None else None

            # ── Per Share / Shareholder ──
            metrics['shares_outstanding'] = _first(
                'CommonStockSharesOutstanding',
                'EntityCommonStockSharesOutstanding',
            )
            metrics['dividends_per_share'] = _first(
                'CommonStockDividendsPerShareDeclared',
                'CommonStockDividendsPerShareCashPaid',
            )

            # Remove None values
            metrics = {k: v for k, v in metrics.items() if v is not None}

        except Exception as e:
            print(f"Error extracting metrics: {str(e)}")

        return metrics

    def _get_latest_value(self, metric_data: Dict) -> Optional[float]:
        """
        Get the latest value from a metric's data

        Args:
            metric_data: Metric data structure from SEC API

        Returns:
            Latest value as float, or None
        """
        try:
            # Try to get annual data first (10-K)
            if 'units' in metric_data:
                for unit, values in metric_data['units'].items():
                    if isinstance(values, list) and values:
                        # Sort by date and get latest
                        sorted_values = sorted(
                            values,
                            key=lambda x: x.get('end', ''),
                            reverse=True
                        )
                        # Get latest annual filing (form 10-K)
                        for val in sorted_values:
                            if val.get('form') == '10-K':
                                return val.get('val')
                        # Fallback to any latest value
                        return sorted_values[0].get('val')

            return None

        except Exception as e:
            return None

    def fetch_company_data(
        self,
        ticker: str,
        years: int = 3,
        include_facts: bool = True
    ) -> Dict:
        """
        Fetch comprehensive company data from SEC

        Args:
            ticker: Stock ticker symbol
            years: Number of years of data to fetch
            include_facts: Whether to include company facts (financial data)

        Returns:
            Dictionary containing all fetched data
        """
        result = {
            'ticker': ticker,
            'cik': None,
            'filings': [],
            'facts': None,
            'metrics': {},
            'fetch_date': datetime.utcnow().isoformat()
        }

        # Get CIK
        cik = self.get_company_cik(ticker)
        if not cik:
            result['error'] = f"Could not find CIK for ticker {ticker}"
            return result

        result['cik'] = cik

        # Get 10-K filings
        filings = self.get_company_filings(cik, "10-K", count=years)
        result['filings'] = filings

        # Get company facts if requested
        if include_facts:
            facts = self.get_company_facts(cik)
            if facts:
                result['facts'] = facts
                result['metrics'] = self.extract_key_metrics(facts)

        return result
