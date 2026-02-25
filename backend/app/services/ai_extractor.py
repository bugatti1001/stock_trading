"""
AI 财报解析服务 — v3（对齐 Excel Raw 表）
支持 OpenAI (GPT) 和 Anthropic (Claude) 两种模型
从上传的财报文件（HTML/PDF）中提取结构化财务数据
"""
import os
import json
import logging
import re
import time
from typing import Optional, Dict, Any, Tuple, List

from app.config.settings import (
    OPENAI_MODELS, ANTHROPIC_MODELS, MAX_TEXT_CHARS,
    get_openai_key, get_anthropic_key,
    SAFE_TEXT_CHARS_PER_CHUNK, CHUNK_OVERLAP_CHARS,
    RATE_LIMIT_WAIT_SECONDS, MAX_RATE_LIMIT_RETRIES,
    CHARS_PER_TOKEN_ESTIMATE, PROMPT_TOKEN_ESTIMATE,
)

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
#  AI 提取 Prompt — 中英文双语，严格对齐 Raw 表字段
# ════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """你是一名专业的财务分析师。请从以下财报文本中提取关键财务数据。
You are a professional financial analyst. Extract key financial data from the report below.

要求 / Requirements：
1. 所有金额必须转换为【实际金额】（即非缩写的完整数值）。
   例如：如果报告写 "$28,095 million"，应填写 28095000000（乘以1,000,000）。
   如果报告写 "¥2.81万亿"，应填写 2810000000000。
   如果报告写 "$95.3 billion"，应填写 95300000000。
   All amounts MUST be in ACTUAL full values (not abbreviated).
   e.g., "$28,095 million" → 28095000000; "¥2.81万亿" → 2810000000000; "$95.3 billion" → 95300000000.
   并在 currency 字段标注币种 / Specify in 'currency' field (USD/CNY/HKD/etc.)
2. 如果某字段在报告中找不到且无法计算，填 null
   If a field cannot be found or calculated, use null.
3. period 字段规则：
   - 年报 / 20-F / Annual Report → "ANNUAL"
   - 第一季度 (Q1, Jan-Mar) → "Q1"
   - 第二季度 (Q2, Apr-Jun) → "Q2"
   - 第三季度 (Q3, Jul-Sep) → "Q3"
   - 第四季度 (Q4, Oct-Dec) → "Q4"
4. report_name: 报告名称，如 "2023年报", "FY2023 Annual Report", "2024Q3"
5. 日期格式 / Date format: YYYY-MM-DD
6. shares_outstanding 单位为【亿股】/ unit: hundred millions of shares (亿)
7. dividends_per_share 为每股现金分红金额（原始货币）
8. 只返回 JSON，不要有任何额外文字或 markdown 代码块

字段说明（双语）/ Field descriptions (bilingual):
- revenue: 营业收入 / Total Revenue
- cost_of_revenue: 营业成本 / Cost of Revenue (Cost of Goods Sold)
- operating_income: 营业利润 / Operating Income (Operating Profit)
- net_income: 净利润 / Net Income (Net Profit)
- net_income_to_parent: 归属于母公司股东的净利润 / Net Income attributable to parent company shareholders
- adjusted_net_income: 扣除非经常性损益后的净利润 / Adjusted Net Income (excluding non-recurring items)
- selling_expense: 销售费用 / Selling & Distribution Expense
- admin_expense: 管理费用（含一般管理） / General & Administrative Expense
- rd_expense: 研发费用 / Research & Development Expense
- finance_cost: 财务费用/融资成本/利息支出净额 / Finance Cost / Net Interest Expense
- cash_and_equivalents: 货币资金（现金及现金等价物 + 受限制现金）/ Cash and Cash Equivalents (incl. restricted cash)
- accounts_receivable: 应收账款 + 应收票据 / Accounts Receivable (incl. notes receivable, trade receivables)
- inventory: 存货 / Inventory
- investments: 交易性金融资产 + 其他权益工具投资 + 长期股权投资 + 可供出售金融资产 / Total investments (trading financial assets + equity investments + long-term equity investments + available-for-sale)
- accounts_payable: 应付账款 + 应付票据 / Accounts Payable (incl. notes payable, trade payables)
- contract_liability_change_pct: 合同负债（预收款项）同比变动百分比，如增长20%填0.2 / Contract Liability (advances from customers) YoY change as decimal, e.g., 20% increase = 0.2
- short_term_borrowings: 短期借款（含一年内到期的长期借款）/ Short-term Borrowings (incl. current portion of long-term debt)
- long_term_borrowings: 长期借款 + 应付债券 / Long-term Borrowings (incl. bonds payable, long-term debt)
- total_assets: 资产总计 / Total Assets
- total_equity: 所有者权益合计（= 净资产）/ Total Equity = Net Assets (including minority interest)
- non_current_assets: 非流动资产合计 / Total Non-current Assets
- current_liabilities: 流动负债合计 / Total Current Liabilities
- operating_cash_flow: 经营活动产生的现金流量净额 / Net Cash from Operating Activities
- capital_expenditure: 购建固定资产、无形资产和其他长期资产支付的现金（取正数）/ Capital Expenditure (positive number) / Purchases of property, plant & equipment
- shares_outstanding: 总股本，单位亿股 / Total Shares Outstanding, in hundred millions (亿)
- dividends_per_share: 每股现金分红（含中期 + 末期）/ Cash Dividends per Share (interim + final)
- nav_per_share: 每股净资产 = total_equity / shares_outstanding / NAV per Share

返回格式（严格 JSON）/ Return format:
{
  "period_end_date": "2025-12-31",
  "fiscal_year": 2025,
  "period": "ANNUAL",
  "report_name": "2025年报",
  "currency": "CNY",
  "revenue": null,
  "cost_of_revenue": null,
  "operating_income": null,
  "net_income": null,
  "net_income_to_parent": null,
  "adjusted_net_income": null,
  "selling_expense": null,
  "admin_expense": null,
  "rd_expense": null,
  "finance_cost": null,
  "cash_and_equivalents": null,
  "accounts_receivable": null,
  "inventory": null,
  "investments": null,
  "accounts_payable": null,
  "contract_liability_change_pct": null,
  "short_term_borrowings": null,
  "long_term_borrowings": null,
  "total_assets": null,
  "total_equity": null,
  "non_current_assets": null,
  "current_liabilities": null,
  "operating_cash_flow": null,
  "capital_expenditure": null,
  "shares_outstanding": null,
  "dividends_per_share": null,
  "nav_per_share": null,
  "extended_metrics": {
    "business_segments": [
      {"name": "segment_name", "revenue": null, "operating_income": null, "margin_pct": null}
    ],
    "moat_indicators": {"market_share_pct": null, "user_retention_rate": null, "repeat_purchase_rate": null}
  }
}

财报文本如下：
"""

# 分块提取时的补充提示 — 告诉模型只处理片段
CHUNK_PROMPT_SUFFIX = """
注意/Note: 以下只是完整财报的一个片段。请尽量从这个片段中提取能找到的字段。
如果某个字段在此片段中找不到，请填 null。不要猜测或编造数据。
IMPORTANT: The text below is only a PARTIAL segment of the full financial report.
Extract whatever fields you can find in this segment. Use null for fields not found.
Do NOT guess or fabricate data.

财报文本片段如下：
"""


# ════════════════════════════════════════════════════════════════
#  文本提取
# ════════════════════════════════════════════════════════════════

def _extract_text_from_html(file_path: str) -> str:
    """从 HTML 文件提取纯文本，去除脚本/样式标签"""
    try:
        from bs4 import BeautifulSoup
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            html = f.read()
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'head', 'meta', 'link']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text[:MAX_TEXT_CHARS]
    except Exception as e:
        logger.error(f"[AI] HTML 文本提取失败: {e}")
        raise


def _extract_text_from_pdf(file_path: str) -> str:
    """从 PDF 文件提取纯文本"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        pages_text = []
        for page in reader.pages:
            pages_text.append(page.extract_text() or '')
        text = '\n'.join(pages_text)
        return text[:MAX_TEXT_CHARS]
    except Exception as e:
        logger.error(f"[AI] PDF 文本提取失败: {e}")
        raise


def _extract_text(file_path: str) -> str:
    """根据文件扩展名选择提取方式"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == '.pdf':
        return _extract_text_from_pdf(file_path)
    else:
        return _extract_text_from_html(file_path)


def _parse_json_response(content: str) -> Dict[str, Any]:
    """从 AI 返回内容中解析 JSON，兼容 markdown 代码块"""
    content = content.strip()
    content = re.sub(r'^```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```$', '', content)
    content = content.strip()
    return json.loads(content)


# ════════════════════════════════════════════════════════════════
#  Token 估算 & 分块
# ════════════════════════════════════════════════════════════════

def _estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数。
    英文 ~4 chars/token，CJK ~2.5 chars/token。
    """
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3400' <= c <= '\u4dbf'
                    or '\uf900' <= c <= '\ufaff')
    non_cjk_count = len(text) - cjk_count
    return int(non_cjk_count / 4 + cjk_count / 2.5)


def _split_text_into_chunks(text: str) -> List[str]:
    """
    按段落边界将文本分割为多个 chunk，每块不超过 SAFE_TEXT_CHARS_PER_CHUNK。
    块之间有 CHUNK_OVERLAP_CHARS 字符的重叠以避免边界处数据丢失。
    """
    max_chars = SAFE_TEXT_CHARS_PER_CHUNK
    overlap = CHUNK_OVERLAP_CHARS

    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))

        # 尽量在段落边界处切割
        if end < len(text):
            search_start = max(end - 2000, start)
            last_para = text.rfind('\n\n', search_start, end)
            if last_para > start + max_chars // 2:
                end = last_para + 2

        chunks.append(text[start:end])
        start = end - overlap

        if start >= len(text):
            break

    logger.info(f"[AI] 文本分为 {len(chunks)} 个块 "
                f"(总 {len(text)} 字符, ~{_estimate_tokens(text)} tokens)")
    return chunks


# 合并时需要比较的所有字段
_ALL_MERGE_FIELDS = [
    'period_end_date', 'fiscal_year', 'period', 'report_name', 'currency',
    'revenue', 'cost_of_revenue', 'operating_income',
    'net_income', 'net_income_to_parent', 'adjusted_net_income',
    'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
    'cash_and_equivalents', 'accounts_receivable', 'inventory',
    'investments', 'accounts_payable', 'contract_liability_change_pct',
    'short_term_borrowings', 'long_term_borrowings',
    'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
    'operating_cash_flow', 'capital_expenditure',
    'shares_outstanding', 'dividends_per_share', 'nav_per_share',
]

# 数值字段子集（用于冲突的 1% 容差比较）
_NUMERIC_MERGE_FIELDS = {
    'revenue', 'cost_of_revenue', 'operating_income',
    'net_income', 'net_income_to_parent', 'adjusted_net_income',
    'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
    'cash_and_equivalents', 'accounts_receivable', 'inventory',
    'investments', 'accounts_payable',
    'short_term_borrowings', 'long_term_borrowings',
    'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
    'operating_cash_flow', 'capital_expenditure',
    'shares_outstanding', 'dividends_per_share', 'nav_per_share',
    'contract_liability_change_pct', 'fiscal_year',
}


def _values_conflict(field: str, val_a, val_b) -> bool:
    """
    判断两个值是否冲突。
    数值字段：差异 > 1% 算冲突（处理四舍五入差异）。
    字符串字段：不等则冲突。
    """
    if val_a is None or val_b is None:
        return False
    if field in _NUMERIC_MERGE_FIELDS:
        try:
            a, b = float(val_a), float(val_b)
            if a == 0 and b == 0:
                return False
            denom = max(abs(a), abs(b))
            if denom == 0:
                return False
            return abs(a - b) / denom > 0.01  # 1% tolerance
        except (ValueError, TypeError):
            return str(val_a) != str(val_b)
    return str(val_a) != str(val_b)


def _merge_chunk_results(results: List[Dict]) -> Tuple[Dict, Dict]:
    """
    合并多个 chunk 的提取结果，检测字段冲突。

    Returns:
        (merged_result, conflicts)
        conflicts 格式: {"field_name": {"values": [...], "chunks": [...], "used": value}}
    """
    if not results:
        raise ValueError("无 chunk 结果可合并")
    if len(results) == 1:
        return results[0], {}

    merged = {}
    conflicts = {}

    for field in _ALL_MERGE_FIELDS:
        # 收集所有 chunk 中该字段的非 null 值
        non_null_entries = []
        for i, result in enumerate(results):
            val = result.get(field)
            if val is not None:
                non_null_entries.append((i, val))

        if not non_null_entries:
            merged[field] = None
            continue

        # 取第一个非 null 值
        first_chunk_idx, first_val = non_null_entries[0]
        merged[field] = first_val

        # 检查后续 chunk 是否有冲突值
        for chunk_idx, val in non_null_entries[1:]:
            if _values_conflict(field, first_val, val):
                conflicts[field] = {
                    'values': [first_val] + [v for _, v in non_null_entries[1:]
                                             if _values_conflict(field, first_val, v)],
                    'chunks': [first_chunk_idx + 1] + [ci + 1 for ci, v in non_null_entries[1:]
                                                       if _values_conflict(field, first_val, v)],
                    'used': first_val,
                }
                break  # 只记录一次冲突

    # 深度合并 extended_metrics
    merged_ext = {'business_segments': [], 'moat_indicators': {}}
    for result in results:
        ext = result.get('extended_metrics')
        if ext and isinstance(ext, dict):
            segs = ext.get('business_segments', [])
            if segs:
                existing_names = {s.get('name') for s in merged_ext['business_segments']}
                for seg in segs:
                    if seg.get('name') and seg['name'] not in existing_names:
                        merged_ext['business_segments'].append(seg)
                        existing_names.add(seg['name'])
            moat = ext.get('moat_indicators', {})
            if moat:
                for k, v in moat.items():
                    if v is not None and merged_ext['moat_indicators'].get(k) is None:
                        merged_ext['moat_indicators'][k] = v
    merged['extended_metrics'] = merged_ext

    filled = sum(1 for f in _ALL_MERGE_FIELDS if merged.get(f) is not None)
    logger.info(f"[AI] 合并 {len(results)} 个块: {filled}/{len(_ALL_MERGE_FIELDS)} 个字段有值, "
                f"{len(conflicts)} 个冲突")
    if conflicts:
        logger.warning(f"[AI] 分块冲突字段: {list(conflicts.keys())}")

    return merged, conflicts


# ════════════════════════════════════════════════════════════════
#  Rate Limit 感知的 Anthropic 调用
# ════════════════════════════════════════════════════════════════

def _parse_retry_after(error) -> int:
    """从 rate limit 错误的响应头中提取 retry-after 秒数"""
    try:
        if hasattr(error, 'response') and error.response is not None:
            retry_after = error.response.headers.get('retry-after')
            if retry_after:
                return int(float(retry_after)) + 5  # 加 5 秒安全余量
    except (ValueError, TypeError, AttributeError):
        pass
    return 0


def _is_rate_limit_error(error: Exception) -> bool:
    """判断异常是否为 rate limit 相关错误"""
    type_name = type(error).__name__
    if 'RateLimitError' in type_name:
        return True
    error_msg = str(error).lower()
    return 'rate' in error_msg and 'limit' in error_msg


def _extract_with_chunks(text: str, model: str) -> Tuple[Dict, Dict]:
    """
    分块提取：将文本分割 → 逐块调用 AI → 块间等待 → 合并结果。

    Returns:
        (merged_result, conflicts)
    """
    chunks = _split_text_into_chunks(text)
    results = []

    for i, chunk in enumerate(chunks):
        chunk_tokens = _estimate_tokens(chunk) + PROMPT_TOKEN_ESTIMATE
        logger.info(f"[AI] 处理块 {i + 1}/{len(chunks)} "
                    f"({len(chunk)} 字符, ~{chunk_tokens} tokens)")

        # 每个 chunk 也有重试逻辑
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                result = _call_anthropic(chunk, model, is_chunk=True)
                results.append(result)
                break
            except Exception as e:
                if _is_rate_limit_error(e) and attempt < MAX_RATE_LIMIT_RETRIES:
                    wait = RATE_LIMIT_WAIT_SECONDS * (attempt + 1)
                    logger.warning(f"[AI] 块 {i + 1} 触发限流 (尝试 {attempt + 1}), "
                                   f"等待 {wait}s...")
                    time.sleep(wait)
                else:
                    logger.error(f"[AI] 块 {i + 1} 失败: {e}")
                    raise

        # 块间等待以遵守每分钟 token 限制
        if i < len(chunks) - 1:
            logger.info(f"[AI] 等待 {RATE_LIMIT_WAIT_SECONDS}s 再处理下一块...")
            time.sleep(RATE_LIMIT_WAIT_SECONDS)

    return _merge_chunk_results(results)


def _call_anthropic_with_retry(text: str, model: str) -> Tuple[Dict, Dict]:
    """
    带 rate limit 感知的 Anthropic 调用。
    策略：先整体尝试，失败再分 chunk。

    Returns:
        (result, conflicts) — 直接调用成功时 conflicts 为空 dict
    """
    import anthropic

    # 先尝试直接调用
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            result = _call_anthropic(text, model)
            return result, {}  # 直接成功，无冲突
        except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
            if not _is_rate_limit_error(e):
                raise  # 非限流错误直接抛出

            retry_after = _parse_retry_after(e)
            wait_time = retry_after or RATE_LIMIT_WAIT_SECONDS

            if attempt < MAX_RATE_LIMIT_RETRIES:
                logger.warning(
                    f"[AI] 触发限流 (尝试 {attempt + 1}/{MAX_RATE_LIMIT_RETRIES + 1}), "
                    f"等待 {wait_time}s 后重试..."
                )
                time.sleep(wait_time)
            else:
                # 重试耗尽，回退到分块提取
                logger.warning(f"[AI] 重试 {attempt + 1} 次后仍限流，回退到分块提取")
                return _extract_with_chunks(text, model)

    # 理论上不会到这里，但以防万一
    return _extract_with_chunks(text, model)


# ════════════════════════════════════════════════════════════════
#  AI 调用
# ════════════════════════════════════════════════════════════════

def _call_openai(text: str, model: str) -> Dict[str, Any]:
    """调用 OpenAI API"""
    from openai import OpenAI
    api_key = get_openai_key()
    if not api_key:
        raise ValueError("未配置 OPENAI_API_KEY，请在 .env 文件中添加")

    client = OpenAI(api_key=api_key)
    prompt = EXTRACTION_PROMPT + text

    logger.info(f"[AI] 调用 OpenAI {model}，文本长度 {len(text)} 字符")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    logger.info(f"[AI] OpenAI 返回 {len(content)} 字符")
    return _parse_json_response(content)


def _call_anthropic(text: str, model: str, is_chunk: bool = False) -> Dict[str, Any]:
    """调用 Anthropic Claude API。is_chunk=True 时使用分块专用提示。"""
    import anthropic
    api_key = get_anthropic_key()
    if not api_key:
        raise ValueError("未配置 ANTHROPIC_API_KEY，请在 .env 文件中添加")

    client = anthropic.Anthropic(api_key=api_key)

    if is_chunk:
        prompt = EXTRACTION_PROMPT + CHUNK_PROMPT_SUFFIX + text
    else:
        prompt = EXTRACTION_PROMPT + text

    logger.info(f"[AI] 调用 Anthropic {model}，文本长度 {len(text)} 字符"
                f"{' (分块)' if is_chunk else ''}")
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    content = message.content[0].text
    logger.info(f"[AI] Anthropic 返回 {len(content)} 字符")
    return _parse_json_response(content)


# ════════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════════

# 数值类型字段清单（对齐 FinancialData 模型 v3）
NUMERIC_FIELDS = [
    'fiscal_year',
    'revenue', 'cost_of_revenue', 'operating_income',
    'net_income', 'net_income_to_parent', 'adjusted_net_income',
    'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
    'cash_and_equivalents', 'accounts_receivable', 'inventory',
    'investments', 'accounts_payable', 'contract_liability_change_pct',
    'short_term_borrowings', 'long_term_borrowings',
    'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
    'operating_cash_flow', 'capital_expenditure',
    'shares_outstanding', 'dividends_per_share', 'nav_per_share',
]


def extract_financials(file_path: str, model: str) -> Dict[str, Any]:
    """
    主入口：从财报文件中提取结构化财务数据

    Args:
        file_path: 本地文件路径（.pdf / .htm / .html）
        model: AI 模型名称，如 'gpt-4o-mini', 'claude-haiku-3-5'

    Returns:
        包含提取字段的 dict，未找到字段值为 None。
        如果使用了分块提取且有冲突，result['_chunk_conflicts'] 包含冲突详情。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 提取文本
    text = _extract_text(file_path)
    logger.info(f"[AI] 提取文本 {len(text)} 字符，使用模型 {model}")

    # 路由到对应 AI（Anthropic 使用带 retry/chunk 的版本）
    conflicts = {}
    if model in OPENAI_MODELS:
        result = _call_openai(text, model)
    elif model in ANTHROPIC_MODELS:
        result, conflicts = _call_anthropic_with_retry(text, model)
    else:
        if model.startswith('gpt') or model.startswith('o1') or model.startswith('o3'):
            result = _call_openai(text, model)
        elif model.startswith('claude'):
            result, conflicts = _call_anthropic_with_retry(text, model)
        else:
            raise ValueError(f"未知模型: {model}，请使用 gpt-* 或 claude-* 系列")

    # 数值类型清洗
    for field in NUMERIC_FIELDS:
        val = result.get(field)
        if val is not None:
            try:
                result[field] = float(val)
                if field == 'fiscal_year':
                    result[field] = int(val)
            except (ValueError, TypeError):
                result[field] = None

    # extended_metrics 保持为 dict
    ext = result.get('extended_metrics')
    if ext is not None and not isinstance(ext, dict):
        try:
            result['extended_metrics'] = json.loads(ext) if isinstance(ext, str) else None
        except (json.JSONDecodeError, TypeError):
            result['extended_metrics'] = None

    # 如果有分块冲突，附加到结果中
    if conflicts:
        result['_chunk_conflicts'] = conflicts

    logger.info(f"[AI] 解析完成，提取到 {sum(1 for v in result.values() if v is not None)} 个非空字段"
                f"{f', {len(conflicts)} 个冲突' if conflicts else ''}")
    return result
