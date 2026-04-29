"""
Per-user SQLite database routing.

Each user gets an independent .db file. The global `db_session` (scoped_session)
is routed to the correct engine via a custom scopefunc that reads the current
Flask session username. All existing code that imports `db_session` continues
to work without modification.
"""
import os
import threading
import logging
from typing import Dict, List
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve the data directory (absolute path so sqlite:/// works correctly)
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR = os.path.join(_BASE_DIR, 'data')
_DEBUG_ECHO = os.getenv('DEBUG', 'False').lower() == 'true'

# ---------------------------------------------------------------------------
# Parse user list from AUTH_USERS env (same source as auth.py)
# ---------------------------------------------------------------------------

def _parse_usernames() -> List[str]:
    """Return list of usernames from AUTH_USERS env var."""
    auth_users_env = os.getenv('AUTH_USERS', '').strip()
    usernames = []
    if auth_users_env:
        for pair in auth_users_env.split(','):
            pair = pair.strip()
            if ':' in pair:
                u = pair.split(':', 1)[0].strip()
                if u:
                    usernames.append(u)
    if not usernames:
        usernames.append(os.getenv('AUTH_USERNAME', 'admin'))
    return usernames


def _db_path_for_user(username: str) -> str:
    return os.path.join(_DATA_DIR, f'stock_trading_{username}.db')


def _db_url_for_user(username: str) -> str:
    return f'sqlite:///{_db_path_for_user(username)}'


# ---------------------------------------------------------------------------
# Per-user engine & session factory cache
# ---------------------------------------------------------------------------
_engines: Dict[str, object] = {}
_session_factories: Dict[str, sessionmaker] = {}
_lock = threading.Lock()


def _get_engine(username: str):
    """Get or create engine for a user (thread-safe, cached)."""
    if username in _engines:
        return _engines[username]
    with _lock:
        if username not in _engines:
            url = _db_url_for_user(username)
            eng = create_engine(
                url,
                echo=_DEBUG_ECHO,
                pool_pre_ping=True,
                connect_args={'check_same_thread': False},
            )
            _engines[username] = eng
            _session_factories[username] = sessionmaker(
                autocommit=False, autoflush=False, bind=eng,
            )
            logger.debug(f"Created engine for user '{username}': {url}")
        return _engines[username]


def _get_session_factory(username: str) -> sessionmaker:
    _get_engine(username)  # ensure initialized
    return _session_factories[username]


# ---------------------------------------------------------------------------
# Thread-local storage for current user binding
# ---------------------------------------------------------------------------
_current_user = threading.local()
_DEFAULT_USER = 'admin'


def _scopefunc():
    """Custom scopefunc for scoped_session: returns (thread_id, username)."""
    username = getattr(_current_user, 'username', _DEFAULT_USER)
    return (threading.get_ident(), username)


# ---------------------------------------------------------------------------
# Global scoped_session — routes to the correct user DB automatically
# ---------------------------------------------------------------------------
# Initialize default user engine eagerly
_get_engine(_DEFAULT_USER)

db_session = scoped_session(
    _get_session_factory(_DEFAULT_USER),
    scopefunc=_scopefunc,
)

# Backwards compat: expose engine (points to default/admin for migrations at import time)
engine = _engines[_DEFAULT_USER]


def bind_user_session(username: str):
    """Bind current thread/request to a user's database.
    Call this in before_request after authentication."""
    _current_user.username = username
    _get_engine(username)
    # Reconfigure scoped session: remove stale session, rebind to user's engine
    db_session.remove()
    db_session.configure(bind=_get_engine(username))


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_all_engines() -> Dict[str, object]:
    """Return {username: engine} for all configured users.
    Useful for migrations and scheduler."""
    for u in _parse_usernames():
        _get_engine(u)
    return dict(_engines)


def get_all_usernames() -> List[str]:
    """Return list of all configured usernames."""
    return _parse_usernames()


def create_user_session(username: str):
    """Create an independent session for a specific user.
    Useful for scheduler jobs that run outside request context."""
    factory = _get_session_factory(username)
    return factory()


def get_current_db_path() -> str:
    """Return the SQLite path for the user bound to the current thread."""
    username = getattr(_current_user, 'username', _DEFAULT_USER)
    return _db_path_for_user(username)


def get_db():
    """Dependency for getting database session (uses current user's DB)."""
    username = getattr(_current_user, 'username', _DEFAULT_USER)
    factory = _get_session_factory(username)
    s = factory()
    try:
        yield s
    finally:
        s.close()


def init_db():
    """Initialize all user databases — create tables."""
    from app.models.base import Base
    import app.models  # noqa: F401 — register all models

    for username in _parse_usernames():
        eng = _get_engine(username)
        Base.metadata.create_all(bind=eng)
        print(f"  Database initialized for user '{username}': {_db_path_for_user(username)}")
    print("All databases initialized successfully!")


def drop_db():
    """Drop all tables in all user databases."""
    from app.models.base import Base
    import app.models  # noqa: F401

    for username in _parse_usernames():
        eng = _get_engine(username)
        Base.metadata.drop_all(bind=eng)
        print(f"  Database dropped for user '{username}'")
    print("All databases dropped successfully!")
