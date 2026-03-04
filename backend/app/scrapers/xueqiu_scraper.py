"""
雪球(Xueqiu) 数据获取器
支持中国A股(SH/SZ)和港股(HK)的行情与财务数据
数据来源：https://xueqiu.com

Token 获取方式：
  1. 设置环境变量 XUEQIU_TOKEN（推荐）
  2. 自动访问雪球首页从 cookies 获取 xq_a_token
"""
import os
import re
import logging
import time
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone

import requests

from app.utils.market_utils import detect_market, MARKET_HK, get_currency_for_symbol

logger = logging.getLogger(__name__)

XUEQIU_TOKEN = os.getenv('XUEQIU_TOKEN', '')


class XueqiuScraper:
    """雪球数据获取器 — 支持 A 股和港股"""

    BASE_URL = 'https://stock.xueqiu.com'
    HOME_URL = 'https://xueqiu.com'

    # 请求间隔(秒)，避免限流
    REQUEST_INTERVAL = 0.3

    def __init__(self, token: str = None):
        self.token = token or XUEQIU_TOKEN
        self._session: Optional[requests.Session] = None
        self._last_request_time: float = 0

    def close(self) -> None:
        """关闭底层 HTTP 连接池，释放资源。"""
        if self._session is not None:
            self._session.close()
            self._session = None

    def __del__(self):
        self.close()

    # 用于自动获取 token 的页面列表（按优先级）
    _TOKEN_PAGES = [
        'https://xueqiu.com/hq',       # 行情页，最可靠
        'https://xueqiu.com/S/SH000001',  # 上证指数页
        'https://xueqiu.com/',          # 首页（备选）
    ]

    def _get_session(self) -> requests.Session:
        """延迟初始化 session，自动设置 cookie。"""
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;'
                          'q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Origin': 'https://xueqiu.com',
                'Referer': 'https://xueqiu.com/',
            })
            if self.token:
                self._session.cookies.set('xq_a_token', self.token,
                                          domain='.xueqiu.com')
            else:
                self._auto_fetch_token()
            # API 请求用 JSON accept
            self._session.headers['Accept'] = 'application/json, text/plain, */*'
        return self._session

    def _auto_fetch_token(self) -> None:
        """自动访问雪球页面获取 xq_a_token cookie。"""
        for url in self._TOKEN_PAGES:
            try:
                resp = self._session.get(url, timeout=10)
                resp.raise_for_status()
                token_val = self._session.cookies.get('xq_a_token')
                if token_val:
                    logger.info(f'[Xueqiu] 自动获取 token 成功 (via {url})')
                    return
            except Exception as e:
                logger.debug(f'[Xueqiu] 尝试 {url} 失败: {e}')
                continue
        logger.warning('[Xueqiu] 自动获取 token 失败，请设置 XUEQIU_TOKEN 环境变量')

    def is_configured(self) -> bool:
        """雪球始终可用（可自动获取 token）"""
        return True

    def _throttle(self) -> None:
        """简单限流"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_INTERVAL:
            time.sleep(self.REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _request(self, url: str, params: dict = None) -> Optional[Dict]:
        """统一请求方法，含错误处理和限流。"""
        self._throttle()
        try:
            session = self._get_session()
            resp = session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get('error_code', 0) != 0:
                desc = data.get('error_description', 'unknown')
                logger.warning(f'[Xueqiu] API 错误: {desc} (url={url})')
                return None
            return data.get('data')
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                logger.warning(f'[Xueqiu] 请求被拒: {e}')
            else:
                logger.warning(f'[Xueqiu] HTTP 错误: {e}')
            return None
        except Exception as e:
            logger.warning(f'[Xueqiu] 请求失败: {e}')
            return None

    @staticmethod
    def _to_xueqiu_symbol(symbol: str) -> str:
        """将内部 symbol 转换为雪球 API 所需格式。
        A股: SH600519 → SH600519（不变）
        港股: HK09888  → 09888（去掉 HK 前缀，雪球用纯5位数字）
        美股: AAPL     → AAPL（不变）
        """
        if detect_market(symbol) == MARKET_HK and symbol.upper().startswith('HK'):
            return symbol[2:]  # HK09888 → 09888
        return symbol

    def _finance_path(self, symbol: str) -> str:
        """A股 → /cn/, 港股 → /hk/, 美股 → /us/"""
        from app.utils.market_utils import MARKET_US
        market = detect_market(symbol)
        if market == MARKET_HK:
            return '/hk/'
        if market == MARKET_US:
            return '/us/'
        return '/cn/'

    # ── 行情 ──────────────────────────────────────────────

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """获取股票行情快照"""
        xq_sym = self._to_xueqiu_symbol(symbol)
        url = f'{self.BASE_URL}/v5/stock/quote.json'
        data = self._request(url, {'symbol': xq_sym, 'extend': 'detail'})
        if not data or not data.get('quote'):
            return None
        return data['quote']

    # ── 财报 ──────────────────────────────────────────────

    def get_income(self, symbol: str, count: int = 20) -> Optional[List[Dict]]:
        """利润表"""
        xq_sym = self._to_xueqiu_symbol(symbol)
        path = self._finance_path(symbol)
        url = f'{self.BASE_URL}/v5/stock/finance{path}income.json'
        data = self._request(url, {
            'symbol': xq_sym, 'type': 'all',
            'is_detail': 'true', 'count': count,
        })
        return data.get('list') if data else None

    def get_balance(self, symbol: str, count: int = 20) -> Optional[List[Dict]]:
        """资产负债表"""
        xq_sym = self._to_xueqiu_symbol(symbol)
        path = self._finance_path(symbol)
        url = f'{self.BASE_URL}/v5/stock/finance{path}balance.json'
        data = self._request(url, {
            'symbol': xq_sym, 'type': 'all',
            'is_detail': 'true', 'count': count,
        })
        return data.get('list') if data else None

    def get_cash_flow(self, symbol: str, count: int = 20) -> Optional[List[Dict]]:
        """现金流量表"""
        xq_sym = self._to_xueqiu_symbol(symbol)
        path = self._finance_path(symbol)
        url = f'{self.BASE_URL}/v5/stock/finance{path}cash_flow.json'
        data = self._request(url, {
            'symbol': xq_sym, 'type': 'all',
            'is_detail': 'true', 'count': count,
        })
        return data.get('list') if data else None

    # ── 搜索 ──────────────────────────────────────────────

    # 合法股票代码的正则：A股 SH/SZ+6位 | HK 5位纯数字 | 美股纯字母1-10位
    _VALID_CODE = re.compile(
        r'^(?:(?:SH|SZ)\d{6}|\d{5}|[A-Z]{1,10})$'
    )

    def search_stocks(self, query: str, size: int = 10) -> List[Dict[str, Any]]:
        """
        搜索股票（支持中文名、拼音、代码）。
        返回 [{symbol, name, market, current_price, exchange}, ...]
        """
        session = self._get_session()
        try:
            resp = session.get(
                'https://xueqiu.com/stock/search.json',
                params={'code': query, 'size': size, 'page': 1},
                timeout=10,
            )
            resp.raise_for_status()
            stocks_raw = resp.json().get('stocks', [])
        except Exception as e:
            logger.warning(f'[Xueqiu] 搜索失败: {e}')
            return []

        results = []
        for s in stocks_raw:
            code = (s.get('code') or '').strip()
            if not code or not self._VALID_CODE.match(code):
                continue

            name = s.get('name', '')
            exchange = s.get('exchange', '')

            # 判断市场 & 规范化 symbol
            if code.startswith(('SH', 'SZ')):
                symbol = code
                market = 'CN'
            elif re.match(r'^\d{5}$', code):
                # 港股：5位数字，加 HK 前缀
                symbol = f'HK{code}'
                market = 'HK'
            elif re.match(r'^[A-Z]{1,10}$', code):
                symbol = code
                market = 'US'
            else:
                continue

            results.append({
                'symbol': symbol,
                'name': name,
                'market': market,
                'current_price': s.get('current'),
                'exchange': exchange,
            })

        return results

    # ── 聚合：股票基本信息 ───────────────────────────────

    def get_stock_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取股票基本信息，返回与 Yahoo/Finnhub 相同结构的标准化 dict。
        """
        q = self.get_quote(symbol)
        if not q:
            return None

        currency = get_currency_for_symbol(symbol)

        # market_cap: 雪球返回的市值字段可能是 market_capital (元) 或 float_market_capital
        market_cap_raw = q.get('market_capital') or q.get('total_market_cap')
        market_cap_b = None
        if market_cap_raw:
            # 雪球市值单位通常为元，转换为十亿
            market_cap_b = market_cap_raw / 1e9

        # ipo_date: 雪球 issue_date 为毫秒时间戳
        ipo_date = None
        issue_ts = q.get('issue_date')
        if issue_ts and isinstance(issue_ts, (int, float)) and issue_ts > 0:
            try:
                ipo_date = datetime.fromtimestamp(issue_ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
            except Exception:
                pass

        return {
            'symbol': symbol,
            'name': q.get('name'),
            'exchange': q.get('exchange'),
            'sector': self._text_or_none(q.get('type')),        # 雪球 type 字段（美股可能返回数字）
            'industry': self._text_or_none(q.get('sub_type')),
            'description': None,             # 雪球行情接口不提供描述
            'website': None,
            'market_cap': market_cap_b,
            'employees': None,
            'ipo_date': ipo_date,
            'current_price': q.get('current'),
            'volume': q.get('volume'),
            'avg_volume': q.get('avg_volume'),
            'pe_ratio': q.get('pe_ttm') or q.get('pe_lyr'),
            'pb_ratio': q.get('pb'),
            'dividend_yield': q.get('dividend_yield'),
            'eps': q.get('eps'),
            'data_source': 'Xueqiu',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        }

    # ── 聚合：财务数据 ───────────────────────────────────

    def get_financial_data(self, symbol: str, years: int = 5) -> Optional[List[Dict]]:
        """
        获取财务数据（利润表+资产负债表+现金流），按 fiscal_year 合并。
        返回与 _fetch_financials_from_yahoo 相同结构的 list[dict]。
        """
        # 获取足够数据：年报+季报混合，取 count=years*5 确保覆盖
        count = years * 5

        income_list = self.get_income(symbol, count=count)
        balance_list = self.get_balance(symbol, count=count)
        cashflow_list = self.get_cash_flow(symbol, count=count)

        if not income_list:
            logger.warning(f'[Xueqiu] {symbol} 未获取到利润表数据')
            return None

        currency = get_currency_for_symbol(symbol)

        # 索引化 balance 和 cashflow
        # 使用 report_name（如 "2024年FY"）+ 截断到秒的 report_date 双索引
        # 因为美股 report_date 毫秒值末尾不一致（如 ...004 vs ...000 vs ...006）
        def _normalize_ts(ts):
            """将毫秒时间戳截断到秒级（去掉末尾毫秒差异）"""
            if isinstance(ts, (int, float)) and ts > 1e10:
                return int(ts / 1000) * 1000
            return ts

        balance_map = {}
        if balance_list:
            for item in balance_list:
                rn = item.get('report_name')
                rd = _normalize_ts(item.get('report_date'))
                if rn:
                    balance_map[rn] = item
                if rd:
                    balance_map[rd] = item

        cashflow_map = {}
        if cashflow_list:
            for item in cashflow_list:
                rn = item.get('report_name')
                rd = _normalize_ts(item.get('report_date'))
                if rn:
                    cashflow_map[rn] = item
                if rd:
                    cashflow_map[rd] = item

        financial_data_list = []
        seen_years = set()

        for inc in income_list:
            # 只取年报数据
            report_name = inc.get('report_name', '')
            report_date = inc.get('report_date')
            month_num = inc.get('month_num')  # 雪球直接提供的报告月份数

            # 判断是否为年报
            is_annual = False
            fiscal_year = None
            report_type_code = inc.get('report_type_code')

            # 优先使用 month_num（雪球直接提供：12=年报, 6=中报, 3=一季报, 9=三季报）
            if month_num == 12:
                is_annual = True

            # 美股通过 report_type_code 判断（596001=年报FY）
            if not is_annual and report_type_code == 596001:
                is_annual = True

            # 也通过 report_name 判断（A股"年报"、美股"FY"）
            if not is_annual and ('年报' in str(report_name) or
                                  'annual' in str(report_name).lower() or
                                  'FY' in str(report_name)):
                is_annual = True

            # 从 report_date 提取 fiscal_year
            if report_date:
                if isinstance(report_date, (int, float)) and report_date > 1e10:
                    from datetime import datetime as dt
                    rd = dt.fromtimestamp(report_date / 1000)
                    fiscal_year = rd.year
                    # 仅当 is_annual 尚未确定时，才用日期月份做 fallback 判断
                    # （美股 FY 可能不在12月结束，如 NVDA 在1月结束，
                    #   此时 report_type_code 已正确标记为年报，不应被覆盖）
                    if not is_annual and month_num is None:
                        is_annual = rd.month == 12 or (rd.month == 3 and detect_market(symbol) == MARKET_HK)
                elif isinstance(report_date, str):
                    try:
                        rd = datetime.fromisoformat(report_date)
                        fiscal_year = rd.year
                        if not is_annual and month_num is None:
                            is_annual = rd.month == 12
                    except (ValueError, TypeError):
                        pass

            # 从 report_annual 字段获取年份（美股雪球直接提供）
            if not fiscal_year and inc.get('report_annual'):
                try:
                    fiscal_year = int(inc['report_annual'])
                except (ValueError, TypeError):
                    pass

            # 从 report_name 提取年份（备选）
            if not fiscal_year and report_name:
                import re as _re
                m = _re.search(r'(\d{4})', str(report_name))
                if m:
                    fiscal_year = int(m.group(1))

            if not is_annual or not fiscal_year:
                continue

            if fiscal_year in seen_years:
                continue
            seen_years.add(fiscal_year)

            if len(seen_years) > years:
                break

            # 匹配 balance 和 cashflow（优先 report_name，fallback 截断的 report_date）
            inc_rn = inc.get('report_name')
            inc_rd = _normalize_ts(inc.get('report_date'))
            bal = balance_map.get(inc_rn) or balance_map.get(inc_rd) or {}
            cf = cashflow_map.get(inc_rn) or cashflow_map.get(inc_rd) or {}

            # 港股年报日期可能是3月31日或12月31日，用实际日期
            report_date_str = f'{fiscal_year}-12-31'
            if isinstance(report_date, (int, float)) and report_date > 1e10:
                from datetime import datetime as dt
                rd_dt = dt.fromtimestamp(report_date / 1000)
                report_date_str = rd_dt.strftime('%Y-%m-%d')

            # 构建标准化 dict
            # 三个市场使用不同的字段名，每个字段列出多套 key（按优先级）
            # A股: total_revenue, op, net_profit, ...
            # 港股: tto (turnover), opeplo, plocyr, sr_ta (revenue incl. tax), ...
            # 美股: total_revenue/revenue, operating_income, net_income, ...
            fin_dict = {
                'fiscal_year': fiscal_year,
                'period': 'FY',
                'report_date': report_date_str,
                'currency': currency,
                # 利润表
                'revenue': self._safe_val(inc, 'total_revenue', 'revenue',
                                          'tto', 'sr_ta'),              # 港股: tto=营业额; 美股: revenue
                'cost_of_revenue': self._safe_val(inc, 'operating_costs', 'revenue_cost',
                                                  'operating_cost', 'sales_cost',
                                                  'slgcost'),           # 港股: slgcost; 美股: sales_cost
                'operating_income': self._safe_val(inc, 'op', 'operating_profit',
                                                   'operating_income',
                                                   'opeplo'),           # 美股: operating_income
                'net_income': self._safe_val(inc, 'net_profit', 'net_income',
                                             'plocyr', 'tcphio'),       # 美股: net_income
                'net_income_to_parent': self._safe_val(inc, 'net_profit_atsopc',
                                                       'net_income_atcss',  # 美股: 归属普通股股东净利
                                                       'total_net_income_atcss',
                                                       'ploashh'),
                'adjusted_net_income': self._safe_val(inc, 'net_profit_after_nrgal_atsolc'),
                'selling_expense': self._safe_val(inc, 'sales_fee', 'selling_expense',
                                                  'marketing_selling_etc'),  # 美股: 销售及营销费用
                'admin_expense': self._safe_val(inc, 'manage_fee', 'management_expense',
                                                'admexp'),
                'rd_expense': self._safe_val(inc, 'rad_cost', 'research_expense',
                                             'rd_expense', 'rad_expenses',   # 美股: rad_expenses
                                             'rshdevexp'),
                'finance_cost': self._safe_val(inc, 'financing_expenses', 'finance_cost_exp',
                                               'interest_expense',           # 美股: 利息支出
                                               'fcgcost'),
                # 资产负债表
                'total_assets': self._safe_val(bal, 'total_assets',
                                               'ta'),
                'current_liabilities': self._safe_val(bal, 'total_current_liab',
                                                      'total_current_liabilities',
                                                      'current_liabilities',
                                                      'clia'),
                'total_equity': self._safe_val(bal, 'total_quity_atsopc',
                                               'total_holders_equity',
                                               'total_equity',
                                               'teqy', 'shhfd'),
                'cash_and_equivalents': self._safe_val(bal, 'cash_equivalents',
                                                       'currency_funds',
                                                       'cash_and_cash_equivalents',
                                                       'cce', 'total_cash',  # 美股: cce/total_cash
                                                       'cceq'),
                'accounts_receivable': self._safe_val(bal, 'account_receivable',
                                                      'accounts_rece',
                                                      'net_receivables',     # 美股: net_receivables
                                                      'trrb'),
                'inventory': self._safe_val(bal, 'inventories', 'inventory',
                                            'iv'),
                'investments': self._safe_val(bal, 'st_invest',              # 短期投资 (liquid)
                                              'fina',                    # 交易性金融资产 (trading securities)
                                              'equity_and_othr_invest',  # 美股: 权益及其他投资
                                              'inv'),                    # 通用投资; 排除 lt_equity_invest(战略性长期股权投资)
                'accounts_payable': self._safe_val(bal, 'accounts_payable',
                                                   'accounts_pay',
                                                   'trpy'),
                'short_term_borrowings': self._safe_val(bal, 'st_borr', 'st_loan',
                                                        'short_term_loan',
                                                        'st_debt',           # 美股: st_debt
                                                        'stdt', 'otstdt'),
                'long_term_borrowings': self._safe_val(bal, 'lt_borr', 'lt_loan',
                                                       'long_term_loan',
                                                       'lt_debt',            # 美股: lt_debt
                                                       'ltdt'),
                'non_current_assets': self._safe_val(bal, 'total_noncurrent_assets',
                                                     'non_current_assets',
                                                     'tnca'),
                'shares_outstanding': self._safe_val(bal, 'total_shares',
                                                     'shares', 'shares_outstanding',
                                                     'numtsh'),
                # 现金流量表
                'operating_cash_flow': self._safe_val(cf, 'ncf_from_oa',
                                                      'net_operating_cashflow',
                                                      'net_cash_provided_by_oa',  # 美股
                                                      'nocf'),
                'capital_expenditure': self._safe_abs(cf, 'capital_expenditure',
                                                      'cash_paid_for_assets',
                                                      'purchase_of_fixed_assets',
                                                      'payment_for_property_and_equip',  # 美股
                                                      'adtfxda'),
                # 每股分红 — 利润表中可能有
                'dividends_per_share': self._safe_val(inc, 'basic_eps_di',
                                                       'dps',
                                                       'cmnshdiv', 'divdbups_ajupd'),
                'data_source': 'Xueqiu',
            }

            # 过滤掉 None 值
            fin_dict = {k: v for k, v in fin_dict.items() if v is not None}

            financial_data_list.append(fin_dict)

        return financial_data_list if financial_data_list else None

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _text_or_none(val) -> Optional[str]:
        """过滤纯数字值（雪球对美股 sector/industry 返回数字编码），只保留有意义的文本。"""
        if val is None:
            return None
        s = str(val).strip()
        if not s or s.isdigit() or s == '0':
            return None
        return s

    @staticmethod
    def _extract_number(val) -> Optional[float]:
        """从雪球值中提取数字。雪球返回格式可能是:
        - 单个数字: 12345.67
        - 数组 [value, yoy_change]: [12345.67, 0.05]
        - None
        """
        if val is None:
            return None
        if isinstance(val, (list, tuple)):
            # [value, yoy_pct] — 取第一个元素
            val = val[0] if val else None
            if val is None:
                return None
        try:
            f = float(val)
            if str(f) == 'nan':
                return None
            return f
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_val(data: dict, *keys) -> Optional[float]:
        """从 dict 中按优先级尝试多个 key，返回第一个非 None 数值。"""
        if not data:
            return None
        for key in keys:
            val = data.get(key)
            result = XueqiuScraper._extract_number(val)
            if result is not None:
                return result
        return None

    @staticmethod
    def _safe_abs(data: dict, *keys) -> Optional[float]:
        """同 _safe_val 但返回绝对值（如 capex 通常为负数）。"""
        if not data:
            return None
        for key in keys:
            val = data.get(key)
            result = XueqiuScraper._extract_number(val)
            if result is not None:
                return abs(result)
        return None


# 全局单例
xueqiu_scraper = XueqiuScraper()
