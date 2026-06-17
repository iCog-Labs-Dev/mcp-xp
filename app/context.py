from contextvars import ContextVar
from typing import Optional

current_api_key: ContextVar[str] = ContextVar("current_api_key")
current_user_email: ContextVar[Optional[str]] = ContextVar("current_user_email", default=None)