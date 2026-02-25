#!/usr/bin/env python3
"""
CLI tool for testing stock trading system
"""
import sys
import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:5001"


def print_response(response):
    """Pretty print API response"""
    print(f"\nStatus: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2))
    except:
        print(response.text)


def add_stock(symbol, name=None):
    """Add a stock to the pool"""
    url = f"{BASE_URL}/api/stocks"
    payload = {"symbol": symbol.upper()}
    if name:
        payload["name"] = name

    response = requests.post(url, json=payload)
    print_response(response)


def list_stocks():
    """List all stocks in pool"""
    url = f"{BASE_URL}/api/stocks"
    response = requests.get(url)
    print_response(response)


def get_stock(symbol):
    """Get stock details"""
    url = f"{BASE_URL}/api/stocks/{symbol.upper()}"
    response = requests.get(url)
    print_response(response)


def refresh_stock(symbol):
    """Refresh stock data"""
    url = f"{BASE_URL}/api/stocks/{symbol.upper()}/refresh"
    response = requests.post(url)
    print_response(response)


def remove_stock(symbol):
    """Remove stock from pool"""
    url = f"{BASE_URL}/api/stocks/{symbol.upper()}"
    response = requests.delete(url)
    print_response(response)


def health_check():
    """Check API health"""
    url = f"{BASE_URL}/health"
    response = requests.get(url)
    print_response(response)


def fetch_sec_data(symbol):
    """Fetch SEC EDGAR data for a stock"""
    url = f"{BASE_URL}/api/data/sec/fetch/{symbol.upper()}"
    response = requests.post(url)
    print_response(response)


def list_criteria():
    """List all screening criteria"""
    url = f"{BASE_URL}/api/screening/criteria"
    response = requests.get(url)
    print_response(response)


def run_screening(criteria_name):
    """Run stock screening"""
    url = f"{BASE_URL}/api/screening/run"
    payload = {"criteria_name": criteria_name}
    response = requests.post(url, json=payload)
    print_response(response)


def main():
    if len(sys.argv) < 2:
        print("""
Stock Trading CLI Tool

Usage:
  python cli.py <command> [arguments]

Commands:
  health                    - Check API health
  list                      - List all stocks in pool
  add <symbol> [name]       - Add stock to pool
  get <symbol>              - Get stock details
  refresh <symbol>          - Refresh stock data from Yahoo Finance
  remove <symbol>           - Remove stock from pool
  fetch-sec <symbol>        - Fetch SEC EDGAR financial data
  criteria                  - List all screening criteria
  screen <criteria_name>    - Run stock screening

Examples:
  python cli.py health
  python cli.py add AAPL "Apple Inc."
  python cli.py list
  python cli.py get AAPL
  python cli.py refresh AAPL
  python cli.py fetch-sec AAPL
  python cli.py criteria
  python cli.py screen "Value Investing (Buffett Style)"
  python cli.py remove AAPL
        """)
        return

    command = sys.argv[1].lower()

    try:
        if command == "health":
            health_check()
        elif command == "list":
            list_stocks()
        elif command == "add":
            if len(sys.argv) < 3:
                print("Error: symbol required")
                return
            symbol = sys.argv[2]
            name = sys.argv[3] if len(sys.argv) > 3 else None
            add_stock(symbol, name)
        elif command == "get":
            if len(sys.argv) < 3:
                print("Error: symbol required")
                return
            get_stock(sys.argv[2])
        elif command == "refresh":
            if len(sys.argv) < 3:
                print("Error: symbol required")
                return
            refresh_stock(sys.argv[2])
        elif command == "remove":
            if len(sys.argv) < 3:
                print("Error: symbol required")
                return
            remove_stock(sys.argv[2])
        elif command == "fetch-sec":
            if len(sys.argv) < 3:
                print("Error: symbol required")
                return
            fetch_sec_data(sys.argv[2])
        elif command == "criteria":
            list_criteria()
        elif command == "screen":
            if len(sys.argv) < 3:
                print("Error: criteria name required")
                return
            criteria_name = ' '.join(sys.argv[2:])
            run_screening(criteria_name)
        else:
            print(f"Unknown command: {command}")
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Error: Could not connect to {BASE_URL}")
        print("Make sure the server is running: python run.py")
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()
