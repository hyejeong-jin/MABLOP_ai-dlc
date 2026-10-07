"""Smoke test: backend modules import."""


def test_handler_imports():
    import handler

    assert hasattr(handler, "handler")
