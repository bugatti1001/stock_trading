"""
财报管理 API
提供 10-K 年报的获取、下载、查询功能
"""
import os
import re
import logging
from flask import Blueprint, request
from app.config.database import db_session
from app.config.settings import RATE_LIMIT_WAIT_SECONDS
from app.models.stock import Stock
from app.models.annual_report import AnnualReport
from app.scrapers.sec_edgar_scraper import SECEdgarScraper
from app.utils.response import success_response, error_response
from app.utils.validation import validate_symbol
from datetime import datetime, date


def _parse_period_end_date(primary_document: str) -> date:
    """
    从文件名中解析财报截止日期
    例: aapl-20251227.htm → 2025-12-27
        msft-20251231.htm → 2025-12-31
        goog-20250930.htm → 2025-09-30
    """
    match = re.search(r'(\d{8})', primary_document)
    if match:
        try:
            return datetime.strptime(match.group(1), '%Y%m%d').date()
        except ValueError:
            pass
    return None

logger = logging.getLogger(__name__)

bp = Blueprint('reports', __name__, url_prefix='/api/reports')

DOWNLOAD_BASE_DIR = os.path.join(os.path.dirname(__file__), '../../data/sec_filings')


def _get_scraper(symbol: str) -> SECEdgarScraper:
    """为指定股票创建 scraper，下载目录按 symbol 分组"""
    ticker_dir = os.path.join(DOWNLOAD_BASE_DIR, symbol.upper())
    os.makedirs(ticker_dir, exist_ok=True)
    return SECEdgarScraper(download_dir=ticker_dir)


@bp.route('/<symbol>', methods=['GET'])
def list_reports(symbol):
    """获取指定股票的财报列表"""
    symbol, sym_err = validate_symbol(symbol)
    if sym_err:
        return error_response(sym_err, 400)
    stock = db_session.query(Stock).filter_by(symbol=symbol).first()
    if not stock:
        return error_response('Stock not found', 404)

    reports = db_session.query(AnnualReport).filter_by(
        stock_id=stock.id
    ).order_by(AnnualReport.fiscal_year.desc()).all()

    return success_response(
        symbol=symbol.upper(),
        reports=[_report_to_dict(r) for r in reports]
    )


@bp.route('/<symbol>/fetch', methods=['POST'])
def fetch_report_metadata(symbol):
    """
    从 SEC EDGAR 获取最新季报（10-Q，外国公司用 20-F）元数据，存入数据库（不下载文件）
    """
    symbol, sym_err = validate_symbol(symbol)
    if sym_err:
        return error_response(sym_err, 400)
    stock = db_session.query(Stock).filter_by(symbol=symbol).first()
    if not stock:
        return error_response('Stock not found', 404)

    try:
        scraper = _get_scraper(symbol)
        filing = scraper.get_latest_quarterly_filing(symbol)
        if not filing:
            return error_response(f'No quarterly report (10-Q/20-F) found for {symbol} on SEC EDGAR', 404)

        # 实际申报类型（10-K 或 20-F）
        form_type = filing.get('form_type', filing.get('form_type', '10-K'))

        # 解析申报日期
        filing_date = datetime.strptime(filing['filing_date'], '%Y-%m-%d').date()
        fiscal_year = filing_date.year  # 以申报年份作为财年

        # 检查是否已存在（按 accession_number 去重）
        existing = db_session.query(AnnualReport).filter_by(
            accession_number=filing['accession_number']
        ).first()

        if existing:
            return success_response(
                message=f'{symbol} 最新{form_type}元数据已存在（{fiscal_year}年）',
                report=_report_to_dict(existing),
                already_exists=True
            )

        # 从文件名解析财报截止日期（如 aapl-20251227.htm → 2025-12-27）
        period_end = _parse_period_end_date(filing.get('primary_document', ''))

        # 创建新记录
        report = AnnualReport(
            stock_id=stock.id,
            fiscal_year=fiscal_year,
            report_type=form_type,
            filing_date=filing_date,
            period_end_date=period_end,
            accession_number=filing['accession_number'],
            filing_url=filing.get('viewer_url') or filing.get('filing_url'),
            is_downloaded=False,
            is_processed=False
        )

        db_session.add(report)
        db_session.commit()
        db_session.refresh(report)

        logger.info(f"[报告] 获取 {symbol} {form_type} 元数据成功: {filing['accession_number']}, 截至: {period_end}")

        return success_response(
            message=f'成功获取 {symbol} 最新{form_type}信息（{fiscal_year}年）',
            report=_report_to_dict(report),
            filing_info={
                'accession_number': filing['accession_number'],
                'filing_date': filing['filing_date'],
                'primary_document': filing['primary_document'],
                'viewer_url': filing.get('viewer_url'),
            }
        )

    except Exception as e:
        logger.error(f"[报告] 获取 {symbol} 元数据失败: {e}")
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>/download', methods=['POST'])
def download_report(symbol):
    """
    下载指定股票最新 10-K 到本地
    需要先调用 /fetch 获取元数据
    """
    symbol, sym_err = validate_symbol(symbol)
    if sym_err:
        return error_response(sym_err, 400)
    stock = db_session.query(Stock).filter_by(symbol=symbol).first()
    if not stock:
        return error_response('Stock not found', 404)

    # 找到最新的未下载报告，或指定的 accession_number
    try:
        req_data = request.get_json(silent=True) or {}
    except Exception:
        req_data = {}
    acc_num = req_data.get('accession_number')

    if acc_num:
        report = db_session.query(AnnualReport).filter_by(
            accession_number=acc_num,
            stock_id=stock.id
        ).first()
    else:
        # 优先查季报（10-Q），其次 20-F，最后 10-K
        from sqlalchemy import or_
        report = db_session.query(AnnualReport).filter(
            AnnualReport.stock_id == stock.id,
            or_(
                AnnualReport.report_type == '10-Q',
                AnnualReport.report_type == '20-F',
                AnnualReport.report_type == '10-K'
            )
        ).order_by(AnnualReport.fiscal_year.desc()).first()

    if not report:
        # 自动先 fetch 元数据再下载
        logger.info(f"[报告] {symbol} 无元数据，自动获取...")
        try:
            scraper_auto = _get_scraper(symbol)
            filing_auto = scraper_auto.get_latest_quarterly_filing(symbol)
            if not filing_auto:
                return error_response(f'无法在SEC EDGAR找到 {symbol} 的季报', 404)
            filing_date_auto = datetime.strptime(filing_auto['filing_date'], '%Y-%m-%d').date()
            fiscal_year_auto = filing_date_auto.year
            form_type_auto = filing_auto.get('form_type', '10-K')
            period_end_auto = _parse_period_end_date(filing_auto.get('primary_document', ''))
            report = AnnualReport(
                stock_id=stock.id,
                fiscal_year=fiscal_year_auto,
                report_type=form_type_auto,
                filing_date=filing_date_auto,
                period_end_date=period_end_auto,
                accession_number=filing_auto['accession_number'],
                filing_url=filing_auto.get('viewer_url') or filing_auto.get('filing_url'),
                is_downloaded=False,
                is_processed=False
            )
            db_session.add(report)
            db_session.commit()
            db_session.refresh(report)
        except Exception as e_auto:
            db_session.rollback()
            return error_response(f'自动获取元数据失败: {e_auto}', 500)

    if report.is_downloaded and report.file_path and os.path.exists(report.file_path):
        return success_response(
            message=f'{symbol} {report.report_type} 已下载',
            file_path=report.file_path,
            file_size=report.file_size,
            file_size_mb=round(report.file_size / 1024 / 1024, 2) if report.file_size else None,
            already_downloaded=True
        )

    try:
        # 需要重新查询 SEC 以获取 primary_document 信息
        scraper = _get_scraper(symbol)
        cik = scraper.get_company_cik(symbol)
        if not cik:
            return error_response(f'无法找到 {symbol} 的 CIK', 500)

        # 按实际申报类型获取（10-K 或 20-F）
        actual_type = report.report_type or '10-K'
        filings = scraper.get_company_filings(cik, actual_type, count=3)
        matched_filing = None
        for f in filings:
            if f['accession_number'] == report.accession_number:
                matched_filing = f
                break
        if not matched_filing and filings:
            matched_filing = filings[0]

        if not matched_filing:
            return error_response('无法从SEC获取申报文件信息', 500)

        # 下载文件
        file_path = scraper.download_filing(
            cik=cik,
            accession_number=matched_filing['accession_number'],
            primary_document=matched_filing['primary_document'],
            ticker=symbol.upper()
        )

        if not file_path:
            return error_response('下载文件失败', 500)

        # 更新数据库记录
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        report.file_path = file_path
        report.file_size = file_size
        report.is_downloaded = True
        db_session.commit()

        logger.info(f"[报告] {symbol} 10-K 下载成功: {file_path} ({file_size} bytes)")

        return success_response(
            message=f'{symbol} 10-K 下载成功',
            file_path=file_path,
            file_size=file_size,
            file_size_mb=round(file_size / 1024 / 1024, 2)
        )

    except Exception as e:
        logger.error(f"[报告] 下载 {symbol} 10-K 失败: {e}")
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>/upload', methods=['POST'])
def upload_report(symbol):
    """
    手动上传财报文件（PDF 或 HTML），用于无法自动下载的股票（如 NIO）
    接受 multipart/form-data，字段：
      - file: 文件（必须，.pdf / .htm / .html）
      - report_type: 报告类型（可选，默认 'manual'）
      - fiscal_year: 财年（可选，默认当前年）
      - period_end_date: 截止日期 YYYY-MM-DD（可选）
    """
    import time as _time
    from werkzeug.utils import secure_filename

    symbol, sym_err = validate_symbol(symbol)
    if sym_err:
        return error_response(sym_err, 400)
    stock = db_session.query(Stock).filter_by(symbol=symbol).first()
    if not stock:
        return error_response('Stock not found', 404)

    if 'file' not in request.files:
        return error_response('未找到上传文件', 400)

    file = request.files['file']
    if not file or file.filename == '':
        return error_response('未选择文件', 400)

    # 文件类型验证（从原始文件名取扩展名，因为 secure_filename 会丢弃中文字符）
    original_ext = os.path.splitext(file.filename or '')[1].lower()
    if original_ext not in ('.pdf', '.htm', '.html'):
        return error_response(f'不支持的文件类型 {original_ext}，仅支持 .pdf / .htm / .html', 400)
    safe_name = secure_filename(file.filename)
    # secure_filename 可能把中文名过滤成空或无扩展名，兜底处理
    if not safe_name or '.' not in safe_name:
        safe_name = f"upload_{int(__import__('time').time())}{original_ext}"
    filename = safe_name

    # 读取可选表单字段
    _VALID_REPORT_TYPES = {'10-K', '10-Q', '20-F', 'manual'}
    report_type = request.form.get('report_type', 'manual').strip() or 'manual'
    if report_type not in _VALID_REPORT_TYPES:
        return error_response(f'不支持的报告类型: {report_type}，仅支持 {_VALID_REPORT_TYPES}', 400)
    try:
        fiscal_year = int(request.form.get('fiscal_year', 0)) or datetime.now().year
    except (ValueError, TypeError):
        fiscal_year = datetime.now().year
    current_year = datetime.now().year
    if not (2000 <= fiscal_year <= current_year + 1):
        return error_response(f'fiscal_year 必须在 2000-{current_year + 1} 之间', 400)

    period_end = None
    period_end_str = request.form.get('period_end_date', '').strip()
    if period_end_str:
        try:
            period_end = datetime.strptime(period_end_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    try:
        # 保存到 data/sec_filings/<SYMBOL>/
        sym_upper = symbol.upper()
        save_dir = os.path.join(DOWNLOAD_BASE_DIR, sym_upper)
        os.makedirs(save_dir, exist_ok=True)

        save_filename = f"{sym_upper}_manual_{filename}"
        save_path = os.path.join(save_dir, save_filename)
        file.save(save_path)
        file_size = os.path.getsize(save_path)

        # 生成唯一 accession_number（避免唯一约束冲突）
        acc_num = f"manual_{int(_time.time())}"

        # 检查该股票是否已有 manual 记录（同文件名），如有则更新
        existing = db_session.query(AnnualReport).filter_by(
            stock_id=stock.id,
            file_path=save_path
        ).first()

        if existing:
            existing.report_type = report_type
            existing.fiscal_year = fiscal_year
            existing.period_end_date = period_end
            existing.file_size = file_size
            existing.is_downloaded = True
            db_session.commit()
            db_session.refresh(existing)
            report = existing
            logger.info(f"[报告] 更新 {sym_upper} 手动上传记录: {save_path}")
        else:
            report = AnnualReport(
                stock_id=stock.id,
                fiscal_year=fiscal_year,
                report_type=report_type,
                filing_date=datetime.now().date(),
                period_end_date=period_end,
                accession_number=acc_num,
                filing_url=None,
                file_path=save_path,
                file_size=file_size,
                is_downloaded=True,
                is_processed=False
            )
            db_session.add(report)
            db_session.commit()
            db_session.refresh(report)
            logger.info(f"[报告] {sym_upper} 手动上传成功: {save_path} ({file_size} bytes)")

        return success_response(
            message=f'{sym_upper} 财报上传成功（{round(file_size / 1024 / 1024, 2)} MB）',
            report=_report_to_dict(report),
            file_size_mb=round(file_size / 1024 / 1024, 2)
        )

    except Exception as e:
        logger.error(f"[报告] {symbol} 上传失败: {e}")
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/<symbol>/analyze', methods=['POST'])
def analyze_report(symbol):
    """
    使用 AI 模型（OpenAI 或 Anthropic）解析已上传/下载的财报文件，
    自动提取财务数据并写入 FinancialData 表，更新 AnnualReport 日期信息。

    请求体 JSON：
      - model: str  AI 模型名（如 "gpt-4o-mini", "claude-haiku-3-5"）
      - report_id: int（可选，默认取最新已下载记录）
    """
    from app.services import ai_extractor
    from app.models.financial_data import FinancialData, ReportPeriod
    from sqlalchemy import or_

    symbol, sym_err = validate_symbol(symbol)
    if sym_err:
        return error_response(sym_err, 400)
    stock = db_session.query(Stock).filter_by(symbol=symbol).first()
    if not stock:
        return error_response('Stock not found', 404)

    req_data = request.get_json(silent=True) or {}
    model = req_data.get('model', 'claude-sonnet-4-5').strip()

    # Validate model name
    from app.config.settings import OPENAI_MODELS, ANTHROPIC_MODELS
    if model not in (OPENAI_MODELS | ANTHROPIC_MODELS):
        return error_response(f'不支持的模型: {model}', 400)

    report_id = req_data.get('report_id')

    # 找目标报告记录
    if report_id:
        report = db_session.query(AnnualReport).filter_by(
            id=report_id, stock_id=stock.id
        ).first()
    else:
        report = db_session.query(AnnualReport).filter(
            AnnualReport.stock_id == stock.id,
            AnnualReport.is_downloaded == True
        ).order_by(AnnualReport.id.desc()).first()

    if not report:
        return error_response(f'{symbol} 尚无已下载的财报，请先上传或下载', 404)

    if not report.file_path or not os.path.exists(report.file_path):
        return error_response(f'文件不存在: {report.file_path}', 404)

    try:
        # ---- 调用 AI 提取 ----
        logger.info(f"[AI] 开始解析 {symbol} 财报，模型: {model}")
        extracted = ai_extractor.extract_financials(report.file_path, model)

        # 提取分块冲突信息（如有）
        chunk_conflicts = extracted.pop('_chunk_conflicts', {})

        # ---- 更新 AnnualReport 日期/类型信息 ----
        if extracted.get('period_end_date'):
            try:
                report.period_end_date = datetime.strptime(
                    extracted['period_end_date'], '%Y-%m-%d'
                ).date()
            except ValueError:
                pass
        if extracted.get('fiscal_year'):
            report.fiscal_year = int(extracted['fiscal_year'])
        if extracted.get('report_type'):
            report.report_type = extracted['report_type']
        report.is_processed = True
        # 如果有分块冲突，记录警告；否则清除之前的错误
        if chunk_conflicts:
            conflict_fields = ', '.join(chunk_conflicts.keys())
            report.analysis_error = f"CHUNKED_CONFLICT: {conflict_fields}"
        else:
            report.analysis_error = None

        # ---- 写入 / 更新 FinancialData ----
        fiscal_year = report.fiscal_year or (
            report.period_end_date.year if report.period_end_date else datetime.now().year
        )

        # 确定 period 枚举值
        period_str = (extracted.get('period') or 'ANNUAL').upper()
        period_map = {
            'ANNUAL': ReportPeriod.ANNUAL,
            'Q1': ReportPeriod.Q1, 'Q2': ReportPeriod.Q2,
            'Q3': ReportPeriod.Q3, 'Q4': ReportPeriod.Q4,
        }
        period_enum = period_map.get(period_str, ReportPeriod.ANNUAL)

        # 查找已有记录（同一 stock + fiscal_year + period），有则更新，无则新建
        fd = db_session.query(FinancialData).filter_by(
            stock_id=stock.id,
            fiscal_year=fiscal_year,
            period=period_enum
        ).first()

        # ---- 单位自动修正 ----
        # AI 可能返回百万单位的值（如 SEC 报告中 "in millions"），需要放大到实际值
        # 阈值判断：如果 revenue 存在且 < 10亿（1e9），很可能是百万单位需要 ×1e6
        _monetary_fields = [
            'revenue', 'cost_of_revenue', 'operating_income',
            'net_income', 'net_income_to_parent', 'adjusted_net_income',
            'selling_expense', 'admin_expense', 'rd_expense', 'finance_cost',
            'cash_and_equivalents', 'accounts_receivable', 'inventory',
            'investments', 'accounts_payable',
            'short_term_borrowings', 'long_term_borrowings',
            'total_assets', 'total_equity', 'non_current_assets', 'current_liabilities',
            'operating_cash_flow', 'capital_expenditure',
        ]
        rev = extracted.get('revenue')
        if rev is not None and isinstance(rev, (int, float)) and 0 < rev < 1e9:
            # 大概率是百万单位，自动放大
            scale = 1_000_000
            logger.info(f"[AI] 检测到 {symbol} 财报数值可能为百万单位 (revenue={rev})，自动 ×{scale}")
            for mf in _monetary_fields:
                val = extracted.get(mf)
                if val is not None and isinstance(val, (int, float)):
                    extracted[mf] = val * scale

        # v3: 对齐 Raw 表的原始数据字段
        fd_float_fields = [
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

        if fd is None:
            fd = FinancialData(
                stock_id=stock.id,
                fiscal_year=fiscal_year,
                period=period_enum,
                report_date=report.period_end_date or report.filing_date,
            )
            db_session.add(fd)

        # 数据来源标签
        ai_src_name = 'Manual Upload' if report.report_type == 'manual' else 'SEC Report'

        # 写入提取到的 Float 字段（仅覆盖 AI 有值的字段，其余保留原值）
        ai_written_fields = set()
        for field in fd_float_fields:
            val = extracted.get(field)
            if val is not None:
                setattr(fd, field, val)
                ai_written_fields.add(field)

        # 写入 String 字段 (currency, report_name)
        for str_field in ('currency', 'report_name'):
            val = extracted.get(str_field)
            if val is not None:
                setattr(fd, str_field, str(val))
                ai_written_fields.add(str_field)

        # data_source: 新记录直接设置；已有记录仅当原来为空时设置
        if fd.data_source is None:
            fd.data_source = ai_src_name

        # 字段级合并 field_sources —— AI 覆盖的字段标记新来源，其余保留原来源
        ext = fd.extended_metrics_dict or {}
        existing_field_sources = ext.get('field_sources', {})
        for field in ai_written_fields:
            existing_field_sources[field] = ai_src_name
        ext['field_sources'] = existing_field_sources

        # 合并 extended_metrics（AI 提取的 key 覆盖，原有 key 保留）
        ext_metrics = extracted.get('extended_metrics')
        if ext_metrics and isinstance(ext_metrics, dict):
            for k, v in ext_metrics.items():
                ext[k] = v

        if chunk_conflicts:
            ext['chunk_conflicts'] = chunk_conflicts
        fd.extended_metrics_dict = ext

        # ---- 回填可计算字段 ----
        from app.services.kpi_calculator import backfill_nav_per_share
        backfill_nav_per_share(fd)

        db_session.commit()
        db_session.refresh(report)
        db_session.refresh(fd)

        logger.info(f"[AI] {symbol} 财报解析完成，fiscal_year={fiscal_year}, period={period_str}")

        # 构建摘要返回给前端（v3: 使用 KPI 计算器）
        from app.services.kpi_calculator import compute_single_period_kpis
        single_kpis = compute_single_period_kpis(fd)
        summary = {
            'period_end_date': extracted.get('period_end_date'),
            'fiscal_year': fiscal_year,
            'period': period_str,
            'report_type': report.report_type,
            'data_source': fd.data_source,
            'currency': fd.currency,
            'revenue': fd.revenue,
            'net_income': fd.net_income,
            'net_income_to_parent': fd.net_income_to_parent,
            'adjusted_net_income': fd.adjusted_net_income,
            'gross_margin_pct': round(single_kpis['gross_margin'] * 100, 1)
                if single_kpis.get('gross_margin') is not None else None,
            'operating_margin_pct': round(single_kpis['operating_margin'] * 100, 1)
                if single_kpis.get('operating_margin') is not None else None,
            'net_margin_pct': round(single_kpis['net_margin'] * 100, 1)
                if single_kpis.get('net_margin') is not None else None,
            'net_cash': single_kpis.get('net_cash'),
            'parent_to_net_ratio': round(single_kpis['parent_to_net_ratio'], 3)
                if single_kpis.get('parent_to_net_ratio') is not None else None,
            'nav_per_share': fd.nav_per_share,
        }

        return success_response(
            message=f'{symbol} 财报解析成功（{model}）'
                    + (f'，{len(chunk_conflicts)} 个字段有分块冲突需人工审核' if chunk_conflicts else ''),
            report=_report_to_dict(report),
            financial_data_id=fd.id,
            summary=summary,
            extracted_fields={k: v for k, v in extracted.items() if v is not None},
            chunk_conflicts=chunk_conflicts if chunk_conflicts else None,
        )

    except Exception as e:
        logger.error(f"[AI] {symbol} 解析失败: {e}", exc_info=True)
        db_session.rollback()

        # 判断是否为 rate limit 错误
        error_msg = str(e)
        error_lower = error_msg.lower()
        is_rate_limit = ('rate' in error_lower and 'limit' in error_lower) or \
                        'RateLimitError' in type(e).__name__

        # 持久化解析失败状态到数据库
        try:
            if report:
                report.analysis_error = error_msg[:500]
                db_session.commit()
        except Exception:
            db_session.rollback()

        if is_rate_limit:
            return error_response(
                error_msg, 429,
                error_type='rate_limit',
                retry_after=RATE_LIMIT_WAIT_SECONDS,
            )
        return error_response(error_msg, 500)


    # v3: 衍生比率不再存入 FinancialData，全部由 kpi_calculator 实时计算


@bp.route('/reset-all-status', methods=['POST'])
def reset_all_report_status():
    """
    重置所有财报的解析状态（is_processed → False, analysis_error → None）。
    用于表结构变更后需要重新 AI 解析的场景。
    不会删除已下载的文件。
    """
    try:
        updated = db_session.query(AnnualReport).filter(
            (AnnualReport.is_processed == True) | (AnnualReport.analysis_error != None)
        ).update({
            AnnualReport.is_processed: False,
            AnnualReport.analysis_error: None,
        }, synchronize_session='fetch')

        db_session.commit()
        logger.info(f"[财报重置] 已重置 {updated} 条财报的解析状态")
        return success_response(
            message=f'已重置 {updated} 条财报的解析状态',
            reset_count=updated,
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"[财报重置] 失败: {e}", exc_info=True)
        return error_response(str(e), 500)


def _report_to_dict(report: AnnualReport) -> dict:
    """将 AnnualReport 对象转为字典"""
    return {
        'id': report.id,
        'stock_id': report.stock_id,
        'fiscal_year': report.fiscal_year,
        'report_type': report.report_type,
        'filing_date': report.filing_date.isoformat() if report.filing_date else None,
        'accession_number': report.accession_number,
        'filing_url': report.filing_url,
        'file_path': report.file_path,
        'file_size': report.file_size,
        'file_size_mb': round(report.file_size / 1024 / 1024, 2) if report.file_size else None,
        'is_downloaded': report.is_downloaded,
        'is_processed': report.is_processed,
        'analysis_error': report.analysis_error,
    }
