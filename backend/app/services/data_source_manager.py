"""
智能数据源管理服务
实现多数据源的自动切换和容错机制

策略：雪球优先（所有市场），缺失字段用 Yahoo/Finnhub/SEC 补充
"""
import logging
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone, timedelta
import yfinance as yf
from requests.exceptions import RequestException, HTTPError

from app.config.settings import DATA_SOURCE_MAX_FAILURES, DATA_SOURCE_COOLDOWN_MINUTES
from app.scrapers.sec_edgar_scraper import SECEdgarScraper
from app.scrapers.financial_data_extractor import FinancialDataExtractor

logger = logging.getLogger(__name__)

# ── 元数据 key，合并时跳过 ──
_META_KEYS = frozenset({'data_source', 'fetched_at', 'field_sources', 'filing_url', 'cik'})


class DataSourcePriority:
    """数据源优先级定义"""
    XUEQIU = 0         # 首选：统一入口
    YAHOO_FINANCE = 1   # 补充：数据全面
    SEC_EDGAR = 2        # 兜底：官方权威
    CACHE = 3            # 本地缓存


class DataSourceStatus:
    """数据源状态追踪"""
    def __init__(self) -> None:
        self.failures: Dict[str, Dict[str, Any]] = {}
        self.max_failures: int = DATA_SOURCE_MAX_FAILURES
        self.cooldown_minutes: int = DATA_SOURCE_COOLDOWN_MINUTES

    def record_failure(self, source: str) -> None:
        if source not in self.failures:
            self.failures[source] = {'count': 0, 'last_failure': None}
        self.failures[source]['count'] += 1
        self.failures[source]['last_failure'] = datetime.now(timezone.utc)
        logger.warning(f"数据源 {source} 失败 (第{self.failures[source]['count']}次)")

    def record_success(self, source: str) -> None:
        if source in self.failures:
            del self.failures[source]
        logger.info(f"数据源 {source} 恢复正常")

    def is_available(self, source: str) -> bool:
        if source not in self.failures:
            return True
        failure_info = self.failures[source]
        if failure_info['last_failure']:
            time_since_failure = datetime.now(timezone.utc) - failure_info['last_failure']
            if time_since_failure < timedelta(minutes=self.cooldown_minutes):
                if failure_info['count'] >= self.max_failures:
                    return False
            else:
                # Cooldown expired, reset failure count
                del self.failures[source]
        return True


# ════════════════════════════════════════════════════════════
#  合并工具
# ════════════════════════════════════════════════════════════

def _merge_stock_info(primary: Dict[str, Any], secondary: Dict[str, Any],
                      primary_name: str, secondary_name: str) -> Dict[str, Any]:
    """
    合并两个数据源的行情信息，primary 优先，secondary 补缺。
    返回合并后的 dict，包含 field_sources 记录每个字段的来源。
    """
    merged = dict(primary)
    field_sources: Dict[str, str] = {}

    # 标记 primary 中有值的字段
    for key, val in primary.items():
        if key not in _META_KEYS and val is not None:
            field_sources[key] = primary_name

    # secondary 补充 primary 中缺失的字段
    for key, val in secondary.items():
        if key in _META_KEYS:
            continue
        if merged.get(key) is None and val is not None:
            merged[key] = val
            field_sources[key] = secondary_name

    merged['data_source'] = primary_name
    merged['field_sources'] = field_sources
    merged['fetched_at'] = datetime.now(timezone.utc).isoformat()
    return merged


def _merge_financial_records(primary_list: List[Dict], secondary_list: Optional[List[Dict]],
                             primary_name: str, secondary_name: str) -> List[Dict]:
    """
    按 fiscal_year 合并两个数据源的财务数据。
    primary 优先，secondary 按年匹配后字段级补缺。
    """
    if not secondary_list:
        # 没有补充源，直接给 primary 打上来源标记
        for rec in primary_list:
            fs = {}
            for k, v in rec.items():
                if k not in _META_KEYS and v is not None:
                    fs[k] = primary_name
            rec['field_sources'] = fs
            rec['data_source'] = primary_name
        return primary_list

    # 以 fiscal_year 为 key 建立 secondary 索引
    sec_by_year: Dict[int, Dict] = {}
    for rec in secondary_list:
        fy = rec.get('fiscal_year')
        if fy is not None:
            sec_by_year[int(fy)] = rec

    merged_list = []
    seen_years = set()

    for p_rec in primary_list:
        fy = p_rec.get('fiscal_year')
        seen_years.add(fy)
        s_rec = sec_by_year.get(int(fy)) if fy is not None else None

        field_sources: Dict[str, str] = {}
        merged = dict(p_rec)

        # 标记 primary 有值字段
        for k, v in p_rec.items():
            if k not in _META_KEYS and v is not None:
                field_sources[k] = primary_name

        # secondary 补缺
        if s_rec:
            for k, v in s_rec.items():
                if k in _META_KEYS:
                    continue
                if merged.get(k) is None and v is not None:
                    merged[k] = v
                    field_sources[k] = secondary_name

        merged['data_source'] = primary_name
        merged['field_sources'] = field_sources
        merged_list.append(merged)

    # secondary 中有但 primary 中没有的年份 → 整条补进来
    for fy, s_rec in sec_by_year.items():
        if fy not in seen_years:
            fs = {}
            for k, v in s_rec.items():
                if k not in _META_KEYS and v is not None:
                    fs[k] = secondary_name
            s_rec['data_source'] = secondary_name
            s_rec['field_sources'] = fs
            merged_list.append(s_rec)

    # 按 fiscal_year 降序
    merged_list.sort(key=lambda r: r.get('fiscal_year', 0), reverse=True)
    return merged_list


class DataSourceManager:
    """
    智能数据源管理器

    策略（所有市场统一）：
      1. 雪球优先 — 行情 + 财报
      2. 缺失字段用 Yahoo / Finnhub / SEC 补充
      3. 每个字段记录来源 (field_sources)
    """

    def __init__(self) -> None:
        self.status: DataSourceStatus = DataSourceStatus()
        self.sec_scraper: SECEdgarScraper = SECEdgarScraper()
        self.financial_extractor: Optional[FinancialDataExtractor] = None
        self._finnhub: Optional[Any] = None
        self._xueqiu: Optional[Any] = None

    def _get_finnhub(self) -> Optional[Any]:
        if self._finnhub is None:
            try:
                from app.scrapers.finnhub_scraper import finnhub_scraper
                self._finnhub = finnhub_scraper
            except Exception:
                self._finnhub = None
        return self._finnhub

    def _get_xueqiu(self) -> Optional[Any]:
        if self._xueqiu is None:
            try:
                from app.scrapers.xueqiu_scraper import xueqiu_scraper
                self._xueqiu = xueqiu_scraper
            except Exception:
                self._xueqiu = None
        return self._xueqiu

    # ════════════════════════════════════════════════════════════
    #  行情数据
    # ════════════════════════════════════════════════════════════

    def fetch_stock_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取股票基本信息 — 雪球优先，其他源补缺

        所有市场先走雪球；如果雪球成功但部分字段缺失，
        对 US 股票再用 Finnhub/Yahoo/SEC 补充。
        """
        from app.utils.market_utils import detect_market, MARKET_US

        symbol = symbol.upper()
        market = detect_market(symbol)

        # ── Step 1: 雪球优先 ──
        xueqiu_data = self._try_fetch_stock_info_xueqiu(symbol)

        if market != MARKET_US:
            # CN/HK：雪球就是唯一源
            if xueqiu_data:
                # 打上 field_sources
                fs = {k: 'Xueqiu' for k, v in xueqiu_data.items()
                      if k not in _META_KEYS and v is not None}
                xueqiu_data['field_sources'] = fs
                return xueqiu_data
            logger.error(f"无法从 Xueqiu 获取 {symbol} 的信息")
            return None

        # ── Step 2: US 市场 — 尝试其他源补充 ──
        us_data = self._try_fetch_stock_info_us(symbol)

        if xueqiu_data and us_data:
            # 合并：雪球优先，其他源补缺
            return _merge_stock_info(xueqiu_data, us_data, 'Xueqiu', us_data.get('data_source', 'Yahoo Finance'))
        elif xueqiu_data:
            # 雪球有，其他源全部失败 → 只用雪球
            fs = {k: 'Xueqiu' for k, v in xueqiu_data.items()
                  if k not in _META_KEYS and v is not None}
            xueqiu_data['field_sources'] = fs
            return xueqiu_data
        elif us_data:
            # 雪球失败，用其他源
            src = us_data.get('data_source', 'Yahoo Finance')
            fs = {k: src for k, v in us_data.items()
                  if k not in _META_KEYS and v is not None}
            us_data['field_sources'] = fs
            return us_data
        else:
            logger.error(f"所有数据源均无法获取 {symbol} 的信息")
            return None

    def _try_fetch_stock_info_xueqiu(self, symbol: str) -> Optional[Dict[str, Any]]:
        """尝试从雪球获取行情，失败返回 None（不抛异常）"""
        xueqiu = self._get_xueqiu()
        if xueqiu and self.status.is_available('xueqiu'):
            try:
                logger.info(f"[Xueqiu] 获取 {symbol} 股票信息...")
                data = xueqiu.get_stock_info(symbol)
                if data:
                    self.status.record_success('xueqiu')
                    return data
            except Exception as e:
                logger.warning(f"[Xueqiu] 失败: {e}")
                self.status.record_failure('xueqiu')
        return None

    def _try_fetch_stock_info_us(self, symbol: str) -> Optional[Dict[str, Any]]:
        """US 股票信息获取：Finnhub -> Yahoo -> SEC"""
        # Finnhub
        finnhub = self._get_finnhub()
        if finnhub and finnhub.is_configured() and self.status.is_available('finnhub'):
            try:
                logger.info(f"[Finnhub] 获取 {symbol} 实时数据...")
                data = finnhub.get_all_data(symbol)
                if data:
                    self.status.record_success('finnhub')
                    return data
            except Exception as e:
                logger.warning(f"[Finnhub] 失败: {e}")
                self.status.record_failure('finnhub')

        # Yahoo Finance
        if self.status.is_available('yahoo'):
            try:
                logger.info(f"[Yahoo Finance] 获取 {symbol} 基本信息...")
                data = self._fetch_from_yahoo(symbol)
                if data:
                    self.status.record_success('yahoo')
                    return data
            except Exception as e:
                logger.warning(f"[Yahoo Finance] 失败: {e}")
                self.status.record_failure('yahoo')

        # SEC EDGAR
        if self.status.is_available('sec'):
            try:
                logger.info(f"[SEC EDGAR] 获取 {symbol} 基本信息...")
                data = self._fetch_from_sec(symbol)
                if data:
                    self.status.record_success('sec')
                    return data
            except Exception as e:
                logger.warning(f"[SEC EDGAR] 失败: {e}")
                self.status.record_failure('sec')

        return None

    # ════════════════════════════════════════════════════════════
    #  财务数据
    # ════════════════════════════════════════════════════════════

    def fetch_financial_data(self, symbol: str, years: int = 5) -> Optional[List[Dict]]:
        """
        获取财务数据 — 雪球优先，其他源补缺

        所有市场先走雪球；对 US 股票再用 Yahoo 按年按字段补充。
        """
        from app.utils.market_utils import detect_market, MARKET_US

        symbol = symbol.upper()
        market = detect_market(symbol)

        # ── Step 1: 雪球优先 ──
        xueqiu_data = self._try_fetch_financials_xueqiu(symbol, years)

        if market != MARKET_US:
            # CN/HK：雪球是唯一源
            if xueqiu_data:
                # 标记 field_sources
                for rec in xueqiu_data:
                    fs = {k: 'Xueqiu' for k, v in rec.items()
                          if k not in _META_KEYS and v is not None}
                    rec['field_sources'] = fs
                    rec['data_source'] = 'Xueqiu'
                return xueqiu_data
            logger.error(f"无法从 Xueqiu 获取 {symbol} 的财务数据")
            return None

        # ── Step 2: US 市场 — 尝试 Yahoo 补充 ──
        yahoo_data = self._try_fetch_financials_yahoo(symbol, years)

        if xueqiu_data and yahoo_data:
            return _merge_financial_records(xueqiu_data, yahoo_data, 'Xueqiu', 'Yahoo Finance')
        elif xueqiu_data:
            for rec in xueqiu_data:
                fs = {k: 'Xueqiu' for k, v in rec.items()
                      if k not in _META_KEYS and v is not None}
                rec['field_sources'] = fs
                rec['data_source'] = 'Xueqiu'
            return xueqiu_data
        elif yahoo_data:
            for rec in yahoo_data:
                fs = {k: 'Yahoo Finance' for k, v in rec.items()
                      if k not in _META_KEYS and v is not None}
                rec['field_sources'] = fs
                rec['data_source'] = 'Yahoo Finance'
            return yahoo_data
        else:
            # 最后尝试 SEC
            sec_data = self._try_fetch_financials_sec(symbol, years)
            if sec_data:
                for rec in sec_data:
                    fs = {k: 'SEC EDGAR' for k, v in rec.items()
                          if k not in _META_KEYS and v is not None}
                    rec['field_sources'] = fs
                    rec['data_source'] = 'SEC EDGAR'
                return sec_data
            logger.error(f"所有数据源均无法获取 {symbol} 的财务数据")
            return None

    def _try_fetch_financials_xueqiu(self, symbol: str, years: int) -> Optional[List[Dict]]:
        """尝试从雪球获取财务数据"""
        xueqiu = self._get_xueqiu()
        if xueqiu and self.status.is_available('xueqiu'):
            try:
                logger.info(f"[Xueqiu] 获取 {symbol} 财务数据...")
                data = xueqiu.get_financial_data(symbol, years)
                if data:
                    self.status.record_success('xueqiu')
                    return data
            except Exception as e:
                logger.warning(f"[Xueqiu] 财务数据失败: {e}")
                self.status.record_failure('xueqiu')
        return None

    def _try_fetch_financials_yahoo(self, symbol: str, years: int) -> Optional[List[Dict]]:
        """尝试从 Yahoo Finance 获取财务数据"""
        if self.status.is_available('yahoo'):
            try:
                logger.info(f"[Yahoo Finance] 获取 {symbol} 财务数据...")
                data = self._fetch_financials_from_yahoo(symbol, years)
                if data:
                    self.status.record_success('yahoo')
                    return data
            except Exception as e:
                logger.warning(f"[Yahoo Finance] 财务数据失败: {e}")
                self.status.record_failure('yahoo')
        return None

    def _try_fetch_financials_sec(self, symbol: str, years: int) -> Optional[List[Dict]]:
        """尝试从 SEC EDGAR 获取财务数据"""
        if self.status.is_available('sec'):
            try:
                logger.info(f"[SEC EDGAR] 获取 {symbol} 财务数据...")
                data = self._fetch_financials_from_sec(symbol, years)
                if data:
                    self.status.record_success('sec')
                    return data
            except Exception as e:
                logger.warning(f"[SEC EDGAR] 财务数据失败: {e}")
                self.status.record_failure('sec')
        return None

    # ════════════════════════════════════════════════════════════
    #  底层获取方法（不变）
    # ════════════════════════════════════════════════════════════

    def _fetch_from_yahoo(self, symbol: str) -> Optional[Dict[str, Any]]:
        """从Yahoo Finance获取股票信息"""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            if not info or 'symbol' not in info:
                raise ValueError("Yahoo Finance返回空数据或无效数据")

            return {
                'symbol': symbol,
                'name': info.get('longName', symbol),
                'exchange': info.get('exchange'),
                'sector': info.get('sector'),
                'industry': info.get('industry'),
                'description': info.get('longBusinessSummary'),
                'website': info.get('website'),
                'market_cap': info.get('marketCap', 0) / 1e9 if info.get('marketCap') else None,
                'employees': info.get('fullTimeEmployees'),
                'ipo_date': info.get('ipoDate'),
                'current_price': info.get('currentPrice') or info.get('regularMarketPrice'),
                'volume': info.get('volume'),
                'avg_volume': info.get('averageVolume'),
                'pe_ratio': info.get('trailingPE'),
                'pb_ratio': info.get('priceToBook'),
                'dividend_yield': info.get('dividendYield'),
                'eps': info.get('trailingEps'),
                'data_source': 'Yahoo Finance',
                'fetched_at': datetime.now(timezone.utc).isoformat()
            }

        except HTTPError as e:
            if e.response.status_code == 429:
                raise Exception("Yahoo Finance限流")
            raise
        except Exception as e:
            raise Exception(f"Yahoo Finance获取失败: {str(e)}")

    def _fetch_from_sec(self, symbol: str) -> Optional[Dict[str, Any]]:
        """从SEC EDGAR获取股票信息"""
        try:
            cik = self.sec_scraper.get_company_cik(symbol)
            if not cik:
                raise ValueError("SEC EDGAR未找到CIK")

            facts = self.sec_scraper.get_company_facts(cik)
            name = facts.get('entityName', symbol) if facts else symbol

            return {
                'symbol': symbol,
                'name': name,
                'exchange': None,
                'sector': None,
                'industry': None,
                'description': None,
                'website': None,
                'market_cap': None,
                'employees': None,
                'ipo_date': None,
                'current_price': None,
                'volume': None,
                'avg_volume': None,
                'pe_ratio': None,
                'pb_ratio': None,
                'dividend_yield': None,
                'eps': None,
                'cik': cik,
                'data_source': 'SEC EDGAR',
                'fetched_at': datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            raise Exception(f"SEC EDGAR获取失败: {str(e)}")

    @staticmethod
    def _safe_int(df, row_name, col):
        """安全地从 DataFrame 中提取整数值"""
        try:
            if row_name in df.index and col in df.columns:
                v = df.loc[row_name, col]
                if v is not None and str(v) != 'nan':
                    return int(v)
        except Exception:
            pass
        return None

    def _fetch_financials_from_yahoo(self, symbol: str, years: int = 5) -> Optional[List[Dict]]:
        """从Yahoo Finance获取财务数据（完整版）"""
        try:
            ticker = yf.Ticker(symbol)

            financials = ticker.financials
            balance_sheet = ticker.balance_sheet
            cash_flow = ticker.cashflow

            if financials.empty:
                raise ValueError("Yahoo Finance未返回财务数据")

            _si = self._safe_int
            financial_data_list = []

            for col in financials.columns[:years]:
                try:
                    year = col.year

                    # === 利润表 ===
                    revenue = _si(financials, 'Total Revenue', col)
                    cost_of_revenue = _si(financials, 'Cost Of Revenue', col)
                    operating_income = _si(financials, 'Operating Income', col)
                    net_income = _si(financials, 'Net Income', col)
                    rd_expense = _si(financials, 'Research And Development', col)
                    selling_expense = _si(financials, 'Selling General And Administration', col)

                    # === 资产负债表 ===
                    total_assets = _si(balance_sheet, 'Total Assets', col)
                    current_liabilities = _si(balance_sheet, 'Current Liabilities', col)
                    total_equity = _si(balance_sheet, 'Total Equity Gross Minority Interest', col)
                    cash = _si(balance_sheet, 'Cash And Cash Equivalents', col)
                    if cash is None:
                        cash = _si(balance_sheet, 'Cash Cash Equivalents And Short Term Investments', col)
                    accounts_receivable = _si(balance_sheet, 'Accounts Receivable', col)
                    if accounts_receivable is None:
                        accounts_receivable = _si(balance_sheet, 'Net Receivables', col)
                    inventory = _si(balance_sheet, 'Inventory', col)
                    investments = _si(balance_sheet, 'Investments And Advances', col)
                    if investments is None:
                        investments = _si(balance_sheet, 'Long Term Investments', col)
                    accounts_payable = _si(balance_sheet, 'Accounts Payable', col)
                    short_term_debt = _si(balance_sheet, 'Current Debt', col)
                    if short_term_debt is None:
                        short_term_debt = _si(balance_sheet, 'Current Debt And Capital Lease Obligation', col)
                    long_term_debt = _si(balance_sheet, 'Long Term Debt', col)
                    if long_term_debt is None:
                        long_term_debt = _si(balance_sheet, 'Long Term Debt And Capital Lease Obligation', col)
                    non_current_assets = _si(balance_sheet, 'Total Non Current Assets', col)
                    shares = _si(balance_sheet, 'Share Issued', col)
                    if shares is None:
                        shares = _si(balance_sheet, 'Ordinary Shares Number', col)

                    # === 现金流量表 ===
                    ocf = _si(cash_flow, 'Operating Cash Flow', col)
                    if ocf is None:
                        ocf = _si(cash_flow, 'Cash Flow From Continuing Operating Activities', col)
                    capex = _si(cash_flow, 'Capital Expenditure', col)
                    if capex is None:
                        capex = _si(cash_flow, 'Purchase Of PPE', col)
                    if capex is not None:
                        capex = abs(capex)

                    fin_dict = {
                        'fiscal_year': year,
                        'period': 'FY',
                        'report_date': col.date(),
                        'currency': 'USD',
                        'revenue': revenue,
                        'cost_of_revenue': cost_of_revenue,
                        'operating_income': operating_income,
                        'net_income': net_income,
                        'rd_expense': rd_expense,
                        'selling_expense': selling_expense,
                        'total_assets': total_assets,
                        'current_liabilities': current_liabilities,
                        'total_equity': total_equity,
                        'cash_and_equivalents': cash,
                        'accounts_receivable': accounts_receivable,
                        'inventory': inventory,
                        'investments': investments,
                        'accounts_payable': accounts_payable,
                        'short_term_borrowings': short_term_debt,
                        'long_term_borrowings': long_term_debt,
                        'non_current_assets': non_current_assets,
                        'shares_outstanding': shares,
                        'operating_cash_flow': ocf,
                        'capital_expenditure': capex,
                        'data_source': 'Yahoo Finance'
                    }
                    fin_dict = {k: v for k, v in fin_dict.items() if v is not None}
                    financial_data_list.append(fin_dict)
                except Exception as e:
                    logger.debug(f"处理{year}年数据失败: {e}")
                    continue

            return financial_data_list if financial_data_list else None

        except Exception as e:
            raise Exception(f"Yahoo Finance财务数据获取失败: {str(e)}")

    def _fetch_financials_from_sec(self, symbol: str, years: int = 5) -> Optional[List[Dict]]:
        """从SEC EDGAR获取财务数据，支持 10-K 和 20-F（外国私人发行人）"""
        try:
            # 先将 ticker 转换为 CIK（之前直接传 symbol 导致 404）
            cik = self.sec_scraper.get_company_cik(symbol)
            if not cik:
                raise ValueError(f"SEC EDGAR未找到 {symbol} 的CIK编号")

            # 先尝试 10-K（美国公司），再尝试 20-F（外国私人发行人如 TCOM/PDD）
            filings = self.sec_scraper.get_company_filings(cik, filing_type='10-K', count=years)
            filing_type = '10-K'

            if not filings:
                filings = self.sec_scraper.get_company_filings(cik, filing_type='20-F', count=years)
                filing_type = '20-F'

            if not filings:
                raise ValueError(f"SEC EDGAR未找到 {symbol} 的10-K或20-F报告")

            financial_data_list = []
            for filing in filings:
                try:
                    filing_date = filing.get('filing_date') or filing.get('filingDate')
                    if filing_date:
                        year = int(filing_date.split('-')[0])
                        fin_dict = {
                            'fiscal_year': year,
                            'period': 'FY',
                            'report_date': filing_date,
                            'data_source': 'SEC EDGAR',
                            'filing_url': filing.get('document_url') or filing.get('filingDetailUrl')
                        }
                        financial_data_list.append(fin_dict)
                except Exception as e:
                    logger.warning(f"处理 {filing.get('filing_date')} 的 {filing_type} 失败: {e}")
                    continue

            return financial_data_list if financial_data_list else None

        except Exception as e:
            raise Exception(f"SEC EDGAR财务数据获取失败: {str(e)}")

    def get_data_source_status(self) -> Dict[str, Any]:
        """获取所有数据源的状态"""
        return {
            'xueqiu': {
                'available': self.status.is_available('xueqiu'),
                'failures': self.status.failures.get('xueqiu', {}).get('count', 0),
                'last_failure': self.status.failures.get('xueqiu', {}).get('last_failure')
            },
            'yahoo_finance': {
                'available': self.status.is_available('yahoo'),
                'failures': self.status.failures.get('yahoo', {}).get('count', 0),
                'last_failure': self.status.failures.get('yahoo', {}).get('last_failure')
            },
            'sec_edgar': {
                'available': self.status.is_available('sec'),
                'failures': self.status.failures.get('sec', {}).get('count', 0),
                'last_failure': self.status.failures.get('sec', {}).get('last_failure')
            }
        }


# 全局单例
data_source_manager = DataSourceManager()
