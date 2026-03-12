"""
Web UI Routes
提供可视化界面的路由
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from app.config.database import db_session
from app.models.stock import Stock
from app.models.financial_data import FinancialData, ReportPeriod
from app.models.annual_report import AnnualReport
from app.models.user_principle import UserPrinciple
from app.models.conversation import Conversation
from app.services.stock_analysis_service import enrich_stock_for_display, batch_load_recent_financials
from app.utils.market_utils import get_currency_sign
from sqlalchemy import func, desc, case
from sqlalchemy.orm import joinedload
import json

bp = Blueprint('web', __name__)


@bp.route('/')
def index():
    """首页重定向到仪表盘"""
    return redirect(url_for('web.dashboard'))


@bp.route('/dashboard')
def dashboard():
    """仪表盘 - 使用聚合查询减少数据库往返"""
    # Single query for all stock counts
    pool_counts = db_session.query(
        func.count(Stock.id).label('total'),
        func.sum(case((Stock.in_pool == True, 1), else_=0)).label('in_pool'),
    ).first()

    # Single query for market cap distribution (avoid 4 separate queries)
    # market_cap 存储单位为"十亿本币"，需按汇率折算为 USD 再分桶
    pool_stocks = db_session.query(Stock.market_cap, Stock.currency).filter(
        Stock.in_pool == True
    ).all()

    # 近似汇率：本币 → USD（定期人工更新即可，无需实时精确）
    _TO_USD = {'USD': 1.0, 'CNY': 0.14, 'HKD': 0.13}

    market_cap_buckets = {'< $10B': 0, '$10B - $50B': 0, '$50B - $100B': 0, '> $100B': 0}
    for mc, currency in pool_stocks:
        if mc is None:
            continue
        mc_usd = mc * _TO_USD.get(currency or 'USD', 1.0)
        if mc_usd < 10:
            market_cap_buckets['< $10B'] += 1
        elif mc_usd < 50:
            market_cap_buckets['$10B - $50B'] += 1
        elif mc_usd < 100:
            market_cap_buckets['$50B - $100B'] += 1
        else:
            market_cap_buckets['> $100B'] += 1

    stats = {
        'stocks_in_pool': pool_counts.in_pool or 0,
        'total_stocks': pool_counts.total or 0,
        'financial_records': db_session.query(func.count(FinancialData.id)).scalar() or 0,
        'stocks_with_financials': db_session.query(func.count(func.distinct(FinancialData.stock_id))).scalar() or 0,
        'principles_count': db_session.query(func.count(UserPrinciple.id)).filter(UserPrinciple.is_active == True).scalar() or 0,
        'conversations_count': db_session.query(func.count(Conversation.id)).scalar() or 0,
    }

    # Sector distribution - single aggregated query
    sector_distribution = db_session.query(
        Stock.sector,
        func.count(Stock.id)
    ).filter(
        Stock.in_pool == True,
        Stock.sector.isnot(None)
    ).group_by(Stock.sector).all()

    sector_data = {
        'labels': [s[0] or '未分类' for s in sector_distribution],
        'values': [s[1] for s in sector_distribution]
    }
    if not sector_data['labels']:
        sector_data = {'labels': ['待分类'], 'values': [stats['stocks_in_pool']]}

    market_cap_data = {
        'labels': list(market_cap_buckets.keys()),
        'values': list(market_cap_buckets.values()),
    }

    return render_template(
        'dashboard.html',
        stats=stats,
        sector_data=sector_data,
        market_cap_data=market_cap_data
    )


@bp.route('/stocks')
def stock_pool():
    """股票池管理 -- 含 6 大类基本面指标"""
    stocks = db_session.query(Stock).filter_by(in_pool=True).order_by(
        desc(Stock.created_at)
    ).all()

    # Batch-load financials for all stocks in 1-2 queries (avoid N+1)
    stock_ids = [s.id for s in stocks]
    fins_map = batch_load_recent_financials(stock_ids, limit=3)

    for stock in stocks:
        enrich_stock_for_display(stock, preloaded_fins=fins_map.get(stock.id, []))

    return render_template('stock_pool.html', stocks=stocks)


@bp.route('/stocks/manual_upload')
def manual_upload():
    """手动上传财务数据"""
    return render_template('manual_upload.html')


@bp.route('/stocks/<symbol>')
def stock_detail(symbol: str):
    """股票详情"""
    stock = db_session.query(Stock).filter_by(symbol=symbol.upper()).first()

    if not stock:
        flash(f'未找到股票 {symbol}', 'danger')
        return redirect(url_for('web.stock_pool'))

    # Get financials ordered by year desc
    financials = db_session.query(FinancialData).filter_by(
        stock_id=stock.id
    ).order_by(desc(FinancialData.fiscal_year)).all()

    # Determine currency sign for display
    currency = 'USD'
    if financials:
        currency = financials[0].currency or 'USD'
    elif hasattr(stock, 'currency') and stock.currency:
        currency = stock.currency
    currency_sign = get_currency_sign(currency)

    return render_template(
        'stock_detail.html',
        stock=stock,
        financials=financials,
        currency_sign=currency_sign,
        currency=currency,
    )


@bp.route('/financial_report')
def financial_report():
    """财报管理页面 - 使用 eager loading 避免 N+1"""
    stocks = db_session.query(Stock).filter_by(in_pool=True).order_by(Stock.symbol).all()

    if not stocks:
        return render_template('financial_report.html', stock_reports=[])

    # Batch load all reports for all pool stocks in one query
    stock_ids = [s.id for s in stocks]
    all_reports = db_session.query(AnnualReport).filter(
        AnnualReport.stock_id.in_(stock_ids)
    ).order_by(AnnualReport.fiscal_year.desc()).all()

    # Group reports by stock_id
    reports_by_stock = {}
    for r in all_reports:
        reports_by_stock.setdefault(r.stock_id, []).append(r)

    stock_reports = []
    for stock in stocks:
        reports = reports_by_stock.get(stock.id, [])
        stock_reports.append({
            'stock': stock,
            'latest_report': reports[0] if reports else None,
            'all_reports': reports,
            'report_count': len(reports)
        })

    return render_template('financial_report.html', stock_reports=stock_reports)


@bp.route('/agent')
def agent_chat():
    """AI 投资讨论助手"""
    return render_template('agent_chat.html')


@bp.route('/principles')
def principles_page():
    """我的投资原则"""
    principles = db_session.query(UserPrinciple).order_by(
        desc(UserPrinciple.created_at)
    ).all()
    return render_template('principles.html', principles=principles)


@bp.route('/media')
def media_center():
    """新闻中心"""
    return render_template('media.html')


@bp.route('/trades')
def trade_journal():
    """交易日记"""
    return render_template('trade_journal.html')


# AJAX API endpoints for web interface
@bp.route('/api/web/stats')
def web_stats():
    """获取统计数据（AJAX）"""
    stats = {
        'stocks_in_pool': db_session.query(func.count(Stock.id)).filter(Stock.in_pool == True).scalar() or 0,
        'total_stocks': db_session.query(func.count(Stock.id)).scalar() or 0,
        'financial_records': db_session.query(func.count(FinancialData.id)).scalar() or 0,
    }
    return jsonify(stats)
