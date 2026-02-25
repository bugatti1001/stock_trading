#!/usr/bin/env python3
"""
Stock Trading Analysis System - Main Entry Point
"""
import sys
import os

# 确保 .env 从脚本所在目录加载，不受工作目录影响
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)

from app import create_app
from app.config.database import init_db, drop_db


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == 'init-db':
            print("Initializing database...")
            init_db()
            print("✓ Database initialized successfully!")
            return

        elif command == 'drop-db':
            confirm = input("⚠️  This will delete all data. Are you sure? (yes/no): ")
            if confirm.lower() == 'yes':
                drop_db()
                print("✓ Database dropped successfully!")
            else:
                print("Operation cancelled.")
            return

        elif command == 'seed':
            print("Seeding database with default data...")
            from app.utils.seed_data import seed_all
            seed_all()
            return

        elif command == 'help':
            print("""
Stock Trading Analysis System - Commands:

  python run.py              Run the Flask development server
  python run.py init-db      Initialize the database (create tables)
  python run.py seed         Seed database with default data
  python run.py drop-db      Drop all database tables (WARNING: deletes all data)
  python run.py help         Show this help message

Environment Variables:
  Set these in .env file (copy from .env.example)
  - DATABASE_URL: Database connection string
  - DEBUG: Enable debug mode (True/False)
  - SECRET_KEY: Flask secret key
  - ALPHA_VANTAGE_API_KEY: Alpha Vantage API key
  - ANTHROPIC_API_KEY: Anthropic Claude API key
            """)
            return

    # Default: Run Flask app
    app = create_app()
    port = int(os.getenv('PORT', 5002))

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║     Stock Trading Analysis System - Backend API Server      ║
╚══════════════════════════════════════════════════════════════╝

🚀 Server starting on http://localhost:{port}
📊 API Documentation: http://localhost:{port}/health

Available endpoints:
  GET    /health                       - Health check
  GET    /api/stocks                   - List all stocks
  POST   /api/stocks                   - Add new stock
  GET    /api/stocks/<symbol>          - Get stock details
  PUT    /api/stocks/<symbol>          - Update stock
  DELETE /api/stocks/<symbol>          - Remove from pool
  POST   /api/stocks/<symbol>/refresh  - Refresh data
  GET    /api/stocks/<symbol>/financials - Get financial data
  GET    /api/agent/conversations      - List AI conversations
  POST   /api/agent/conversations      - Create conversation
  POST   /api/principles               - Create investment principles
  GET    /api/principles/export        - Export principles as JSON

Press CTRL+C to stop the server
═════════════════════════════════════════════════════════════
    """)

    app.run(
        host='0.0.0.0',
        port=port,
        debug=os.getenv('DEBUG', 'True').lower() == 'true'
    )


# Module-level app instance for Gunicorn (gunicorn run:app)
app = create_app()

if __name__ == '__main__':
    main()
