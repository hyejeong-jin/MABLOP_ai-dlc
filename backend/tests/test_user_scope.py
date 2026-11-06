"""user_scope tests: bind/reset, fail-closed when unbound, immune to s3store reload (Req 8.1, 8.2)."""
import contextvars
import importlib

import pytest

import user_scope
from errors import MablopError

UID_A = "u_0123456789abcdef"
UID_B = "u_fedcba9876543210"


def _in_fresh_context(fn):
    """Run fn in an empty context so a fixture-bound user cannot leak in."""
    return contextvars.Context().run(fn)


def test_unbound_current_user_id_raises_internal_error():
    def run():
        with pytest.raises(MablopError) as exc:
            user_scope.current_user_id()
        assert exc.value.code == "internal_error"
        assert exc.value.status == 500

    _in_fresh_context(run)


def test_bind_makes_user_id_visible_inside_block_only():
    def run():
        with user_scope.bind(UID_A):
            assert user_scope.current_user_id() == UID_A
        with pytest.raises(MablopError):
            user_scope.current_user_id()

    _in_fresh_context(run)


def test_bind_resets_after_exception():
    def run():
        with pytest.raises(RuntimeError):
            with user_scope.bind(UID_A):
                raise RuntimeError("boom")
        with pytest.raises(MablopError):
            user_scope.current_user_id()

    _in_fresh_context(run)


def test_nested_bind_restores_outer_user():
    def run():
        with user_scope.bind(UID_A):
            with user_scope.bind(UID_B):
                assert user_scope.current_user_id() == UID_B
            assert user_scope.current_user_id() == UID_A

    _in_fresh_context(run)


@pytest.mark.parametrize(
    "bad",
    ["", "u_123", "U_0123456789abcdef", "u_0123456789ABCDEF", "u_0123456789abcdeg", "_auth", "u_0123456789abcdef\n", None, 123],
)
def test_bind_rejects_malformed_user_id(bad):
    def run():
        with pytest.raises(MablopError) as exc:
            with user_scope.bind(bad):
                pytest.fail("block must not run")
        assert exc.value.code == "internal_error"
        with pytest.raises(MablopError):
            user_scope.current_user_id()

    _in_fresh_context(run)


def test_binding_survives_reload_of_s3store(monkeypatch):
    monkeypatch.setenv("MABLOP_BUCKET", "mablop-test")
    import s3store

    def run():
        with user_scope.bind(UID_A):
            importlib.reload(s3store)
            assert user_scope.current_user_id() == UID_A
            assert s3store.user_key("metadata/x.json") == f"users/{UID_A}/metadata/x.json"

    _in_fresh_context(run)


def test_binding_is_isolated_between_contexts():
    seen = {}

    def worker():
        with user_scope.bind(UID_B):
            seen["inner"] = user_scope.current_user_id()

    def run():
        with user_scope.bind(UID_A):
            contextvars.Context().run(worker)
            seen["outer"] = user_scope.current_user_id()

    _in_fresh_context(run)
    assert seen == {"inner": UID_B, "outer": UID_A}
