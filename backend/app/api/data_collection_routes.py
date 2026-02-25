"""
Data Collection API Routes
Endpoints for fetching data from external sources
"""
import logging
from flask import Blueprint, request
from app.config.database import db_session
from app.models.stock import Stock
from app.scrapers.sec_edgar_scraper import SECEdgarScraper
from app.scrapers.financial_data_extractor import FinancialDataExtractor
from app.utils.response import success_response, error_response
from app.utils.validation import validate_symbol

logger = logging.getLogger(__name__)

bp = Blueprint('data_collection', __name__)


@bp.route('/sec/fetch/<symbol>', methods=['POST'])
def fetch_sec_data(symbol):
    """Fetch SEC EDGAR data for a stock (US stocks only)"""
    try:
        clean_symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        from app.utils.market_utils import detect_market, MARKET_US
        if detect_market(clean_symbol) != MARKET_US:
            return error_response(
                f'SEC EDGAR only supports US stocks, not {clean_symbol}', 400)

        stock = db_session.query(Stock).filter_by(symbol=clean_symbol).first()
        if not stock:
            return error_response(f'Stock {clean_symbol} not found in pool', 404)

        try:
            years = int(request.args.get('years', 3))
        except (ValueError, TypeError):
            years = 3
        years = max(1, min(years, 10))

        sec_scraper = SECEdgarScraper()
        extractor = FinancialDataExtractor(db_session)
        result = extractor.extract_all_for_stock(stock, sec_scraper, years=years)

        return success_response(symbol=clean_symbol, data=result)

    except Exception as e:
        logger.error(f"fetch_sec_data error: {e}", exc_info=True)
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/sec/batch-fetch', methods=['POST'])
def batch_fetch_sec_data():
    """Fetch SEC data for multiple stocks"""
    try:
        data = request.get_json()
        if not data:
            return error_response('Missing request body', 400)

        symbols = data.get('symbols', [])
        years = data.get('years', 3)

        if not symbols:
            return error_response('No symbols provided', 400)

        sec_scraper = SECEdgarScraper()
        extractor = FinancialDataExtractor(db_session)

        results = []
        for symbol in symbols:
            stock = db_session.query(Stock).filter_by(symbol=symbol.upper()).first()
            if not stock:
                results.append({
                    'symbol': symbol,
                    'success': False,
                    'error': 'Stock not found in pool'
                })
                continue

            result = extractor.extract_all_for_stock(stock, sec_scraper, years=years)
            result['success'] = len(result.get('errors', [])) == 0
            results.append(result)

        return success_response(total=len(symbols), results=results)

    except Exception as e:
        logger.error(f"batch_fetch_sec_data error: {e}", exc_info=True)
        db_session.rollback()
        return error_response(str(e), 500)


@bp.route('/sec/company-info/<symbol>', methods=['GET'])
def get_sec_company_info(symbol):
    """Get SEC company information including CIK"""
    try:
        clean_symbol, err = validate_symbol(symbol)
        if err:
            return error_response(err, 400)

        sec_scraper = SECEdgarScraper()
        cik = sec_scraper.get_company_cik(clean_symbol)

        if not cik:
            return error_response(f'Could not find CIK for {clean_symbol}', 404)

        filings = sec_scraper.get_company_filings(cik, "10-K", count=5)

        return success_response(
            symbol=clean_symbol,
            cik=cik,
            recent_filings=filings
        )

    except Exception as e:
        logger.error(f"get_sec_company_info error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/update-all', methods=['POST'])
def update_all_stocks():
    """Update SEC data for all stocks in pool"""
    try:
        try:
            years = int(request.args.get('years', 3))
        except (ValueError, TypeError):
            years = 3
        years = max(1, min(years, 10))

        stocks = db_session.query(Stock).filter_by(in_pool=True, is_active=True).all()
        if not stocks:
            return error_response('No stocks in pool', 404)

        sec_scraper = SECEdgarScraper()
        extractor = FinancialDataExtractor(db_session)

        results = []
        for stock in stocks:
            result = extractor.extract_all_for_stock(stock, sec_scraper, years=years)
            result['success'] = len(result.get('errors', [])) == 0
            results.append(result)

        successful = sum(1 for r in results if r['success'])

        return success_response(
            total_stocks=len(stocks),
            successful=successful,
            failed=len(stocks) - successful,
            results=results
        )

    except Exception as e:
        logger.error(f"update_all_stocks error: {e}", exc_info=True)
        db_session.rollback()
        return error_response(str(e), 500)
