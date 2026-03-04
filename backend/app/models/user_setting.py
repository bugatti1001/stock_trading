from sqlalchemy import Column, String
from app.models.base import BaseModel


class UserSetting(BaseModel):
    """Per-user key-value settings stored in each user's SQLite database."""
    __tablename__ = 'user_settings'

    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(String(2000), nullable=False, default='')
