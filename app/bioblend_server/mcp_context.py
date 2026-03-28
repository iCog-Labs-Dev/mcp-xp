from contextvars import ContextVar

current_api_key_server: ContextVar[str] = ContextVar("current_api_key_server", default=None)