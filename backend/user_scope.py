"""Acting-user scope (user-auth-admin D6, Req 8.1, 8.2).

Holds the verified userId for the duration of one dispatch in a ContextVar.
Kept apart from s3store on purpose: tests call importlib.reload(s3store), and a
reload must not discard the bound user. Reading the variable while unbound
raises internal_error, so a component that runs outside dispatch cannot touch a
shared key (fail closed).
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar

from errors import MablopError

USER_ID_PATTERN = r"^u_[0-9a-f]{16}$"
_USER_ID_RE = re.compile(USER_ID_PATTERN)

_current = ContextVar("mablop_user_id", default=None)


@contextmanager
def bind(user_id):
    """Bind user_id for the with-block; reset on exit (also on error).

    A userId never comes from user input, so a malformed one is internal_error.
    """
    if not isinstance(user_id, str) or _USER_ID_RE.fullmatch(user_id) is None:
        raise MablopError("internal_error")
    token = _current.set(user_id)
    try:
        yield
    finally:
        _current.reset(token)


def current_user_id() -> str:
    """Return the bound userId; raise internal_error when unbound."""
    user_id = _current.get()
    if user_id is None:
        raise MablopError("internal_error")
    return user_id
