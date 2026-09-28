# Unit tests for the shared-PCH cache key (_pch_cache_key).
# The cache root is machine-wide, so the key must separate two checkouts of
# byte-identical runtime headers whenever the .gch cannot survive a different
# -I root -- otherwise checkout B force-includes checkout A's .gch, reaches
# the same headers through its own -I, and dies on `redefinition of` errors.

from pathlib import Path

import conftest
from conftest import _pch_cache_key


def _key(monkeypatch, runtime_dir: Path, path_sensitive: bool) -> str:
    monkeypatch.setattr(conftest, "RUNTIME_DIR", runtime_dir)
    # _runtime_hash is session-memoized from RUNTIME_DIR: computed here first,
    # it would pin the fake dir's hash for the rest of the worker's session.
    monkeypatch.setattr(conftest, "_runtime_hash", lambda: "RUNTIME")
    monkeypatch.setattr(conftest, "pch_is_path_sensitive", lambda cfg: path_sensitive)
    _pch_cache_key.cache_clear()
    try:
        return _pch_cache_key()
    finally:
        _pch_cache_key.cache_clear()


def test_path_sensitive_family_separates_checkouts(tmp_path, monkeypatch):
    a = tmp_path / "checkout-a" / "include"
    b = tmp_path / "checkout-b" / "include"
    assert _key(monkeypatch, a, True) != _key(monkeypatch, b, True)


def test_path_sensitive_family_stable_for_one_checkout(tmp_path, monkeypatch):
    """The inverse: same checkout must keep hitting its own cached PCH."""
    a = tmp_path / "checkout-a" / "include"
    assert _key(monkeypatch, a, True) == _key(monkeypatch, a, True)


def test_key_probe_leaves_the_session_runtime_hash_alone(tmp_path, monkeypatch):
    """A worker whose first runtime-hash call came from a fake RUNTIME_DIR
    keyed its PCH, stdlib .o cache and exec fingerprints on an empty runtime."""
    conftest._runtime_hash.cache_clear()
    _key(monkeypatch, tmp_path / "checkout-a" / "include", False)
    monkeypatch.undo()
    assert conftest._runtime_hash() == conftest._runtime_hash.__wrapped__()


def test_path_agnostic_family_shares_across_checkouts(tmp_path, monkeypatch):
    """GCC dedupes guarded headers by content, so worktrees keep sharing one
    .gch (and with it their ccache entries for every PCH-using compile)."""
    a = tmp_path / "checkout-a" / "include"
    b = tmp_path / "checkout-b" / "include"
    assert _key(monkeypatch, a, False) == _key(monkeypatch, b, False)
