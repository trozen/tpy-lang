"""Unit tests for the --dep-mode harness option (conftest._parse_dep_modes)
and the dep-mode-aware stdlib-cache plumbing."""

import json
from pathlib import Path

import pytest

import conftest


def test_parse_dep_modes_valid() -> None:
    assert conftest._parse_dep_modes([]) == {}
    assert conftest._parse_dep_modes(["pcre2=system"]) == {"pcre2": "system"}
    # comma-separated and repeated specs both accumulate
    assert conftest._parse_dep_modes(["pcre2=system,mbedtls=system"]) == {
        "pcre2": "system", "mbedtls": "system",
    }
    assert conftest._parse_dep_modes(["pcre2=system", "mbedtls=bundled"]) == {
        "pcre2": "system", "mbedtls": "bundled",
    }
    # whitespace and empty items tolerated; later spec wins
    assert conftest._parse_dep_modes([" pcre2 = system , ", "pcre2=bundled"]) == {
        "pcre2": "bundled",
    }


def test_parse_dep_modes_invalid() -> None:
    # "none" and "auto" are rejected although tpyc accepts them: none can
    # only crash the run (the stdlib prewarm always resolves every declared
    # dep), and auto's future probe outcome would evade the cache key.
    for bad in ("pcre2", "pcre2=", "=system", "nosuchlib=system", "pcre2=fast",
                "pcre2=none", "pcre2=auto"):
        with pytest.raises(ValueError, match="--dep-mode"):
            conftest._parse_dep_modes([bad])


def test_merge_link_flags() -> None:
    """Case-own flags keep their order and win the dedup; cache flags append."""
    assert conftest.merge_link_flags([], []) == []
    assert conftest.merge_link_flags(["-la"], []) == ["-la"]
    assert conftest.merge_link_flags([], ["-lb"]) == ["-lb"]
    assert conftest.merge_link_flags(["-la"], ["-lb", "-la"]) == ["-la", "-lb"]


def test_stdlib_cache_metadata_link_flags_roundtrip(tmp_path: Path) -> None:
    """link_flags persists through metadata.json; legacy metadata without the
    key (necessarily bundled-mode) degrades to the empty default."""
    meta = {"objects": [], "cpp_relpaths": [], "output_hash": "h",
            "link_flags": ["-lpcre2-8"]}
    (tmp_path / "metadata.json").write_text(json.dumps(meta))
    cache = conftest._load_persistent_stdlib_cache(tmp_path)
    assert cache is not None and cache.link_flags == ["-lpcre2-8"]

    del meta["link_flags"]
    (tmp_path / "metadata.json").write_text(json.dumps(meta))
    legacy = conftest._load_persistent_stdlib_cache(tmp_path)
    assert legacy is not None and legacy.link_flags == []


def test_stdlib_cache_key_folds_dep_modes() -> None:
    """A dep-mode override must re-key the stdlib .o cache (the .o set
    differs: bundled third-party .c compiled in vs. left to the system lib),
    while the default (empty) mode keeps the historical key.

    DEP_MODES is live session state (the suite itself may run under
    --dep-mode, e.g. the nightly sysdeps config), so snapshot and RESTORE it
    -- clearing it would silently flip the rest of this worker's cases back
    to bundled mode."""
    saved = dict(conftest.DEP_MODES)
    try:
        conftest.DEP_MODES.clear()
        conftest._stdlib_cache_key.cache_clear()
        default_key = conftest._stdlib_cache_key()

        conftest.DEP_MODES["pcre2"] = "system"
        conftest._stdlib_cache_key.cache_clear()
        assert conftest._stdlib_cache_key() != default_key
    finally:
        conftest.DEP_MODES.clear()
        conftest.DEP_MODES.update(saved)
        conftest._stdlib_cache_key.cache_clear()
