import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from dotenv import load_dotenv

# Load environment variables (override=True: 确保 .env 文件中的值覆盖系统环境中的空值)
load_dotenv(override=True)

# Get database URL from environment
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///../../data/stock_trading.db')

# Create engine
engine = create_engine(
    DATABASE_URL,
    echo=os.getenv('DEBUG', 'False').lower() == 'true',
    pool_pre_ping=True,
    connect_args={'check_same_thread': False} if 'sqlite' in DATABASE_URL else {}
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create scoped session for thread safety
db_session = scoped_session(SessionLocal)


def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database - create all tables"""
    from app.models.base import Base
    import app.models  # Import all models

    Base.metadata.create_all(bind=engine)
    print("Database initialized successfully!")


def drop_db():
    """Drop all tables - use with caution!"""
    from app.models.base import Base
    import app.models

    Base.metadata.drop_all(bind=engine)
    print("Database dropped successfully!")
