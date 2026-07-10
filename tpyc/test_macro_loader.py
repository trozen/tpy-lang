"""Tests for macro_loader.validate_and_call_macro."""

import pytest

from .macro_loader import validate_and_call_macro
from .diagnostics import SemanticError


class _FakeLoc:
    """Minimal loc object for test errors."""
    def __init__(self):
        self.lineno = 1
        self.col_offset = 0
        self.end_lineno = 1
        self.end_col_offset = 0


_LOC = _FakeLoc()


def _make_macro(fn):
    fn._is_class_macro = True
    return fn


class TestValidateAndCallMacro:
    def test_unknown_kwarg(self):
        def my_macro(cls, *, x: int = 0):
            pass
        with pytest.raises(SemanticError, match="unknown keyword argument 'y'"):
            validate_and_call_macro(my_macro, None, {"y": 1}, "test.my_macro", _LOC)

    def test_unknown_kwarg_no_params(self):
        def my_macro(cls):
            pass
        with pytest.raises(SemanticError, match="takes no keyword arguments"):
            validate_and_call_macro(my_macro, None, {"x": 1}, "test.my_macro", _LOC)

    def test_missing_required_kwarg(self):
        def my_macro(cls, *, required: str):
            pass
        with pytest.raises(SemanticError, match="missing required keyword argument 'required'"):
            validate_and_call_macro(my_macro, None, {}, "test.my_macro", _LOC)

    def test_type_mismatch(self):
        def my_macro(cls, *, flag: bool = False):
            pass
        with pytest.raises(SemanticError, match="'flag' must be bool, got str"):
            validate_and_call_macro(my_macro, None, {"flag": "yes"}, "test.my_macro", _LOC)

    def test_unexpected_exception_wrapped(self):
        def my_macro(cls):
            raise ValueError("something broke")
        with pytest.raises(SemanticError, match="macro raised ValueError: something broke"):
            validate_and_call_macro(my_macro, None, {}, "test.my_macro", _LOC)

    def test_semantic_error_not_wrapped(self):
        def my_macro(cls):
            raise SemanticError("custom error", _LOC)
        with pytest.raises(SemanticError, match="custom error"):
            validate_and_call_macro(my_macro, None, {}, "test.my_macro", _LOC)

    def test_var_keyword_allows_unknown(self):
        received = {}
        def my_macro(cls, **kw):
            received.update(kw)
        validate_and_call_macro(my_macro, None, {"anything": 42}, "test.my_macro", _LOC)
        assert received == {"anything": 42}

    def test_valid_call(self):
        results = {}
        def my_macro(cls, *, frozen: bool = False, order: bool = False):
            results["frozen"] = frozen
            results["order"] = order
        validate_and_call_macro(my_macro, "fake_cls", {"frozen": True}, "test.my_macro", _LOC)
        assert results == {"frozen": True, "order": False}


class TestBuildCacheTracking:
    """MacroRegistry records what the build cache needs as manifest inputs:
    every loaded macro file, plus _resolve_macro_import probe outcomes."""

    def test_load_module_records_file(self, tmp_path):
        from pathlib import Path
        from .macro_loader import MacroRegistry
        mac = tmp_path / "mymac.py"
        mac.write_text("# tpy: macro_module\nX = 1\n")
        reg = MacroRegistry()
        reg.load_module("mymac", mac)
        assert reg.loaded_files == {"mymac": str(mac)}
        # Re-loading must not duplicate or error.
        reg.load_module("mymac", mac)
        assert reg.loaded_files == {"mymac": str(mac)}

    def test_resolve_import_records_probes(self, tmp_path):
        from .macro_loader import MacroRegistry
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        # `dep` misses in first/, exists-but-is-not-a-macro in second/.
        (second / "dep.py").write_text("X = 1\n")
        reg = MacroRegistry(search_dirs=[first, second])
        assert reg._resolve_macro_import("dep") is None
        assert reg.probe_missing == [str(first / "dep.py")]
        assert reg.probe_rejected == [str(second / "dep.py")]

    def test_resolve_import_loads_and_records_macro(self, tmp_path):
        from .macro_loader import MacroRegistry
        libdir = tmp_path / "lib"
        libdir.mkdir()
        (libdir / "helper.py").write_text("# tpy: macro_module\nY = 2\n")
        reg = MacroRegistry(search_dirs=[libdir])
        assert reg._resolve_macro_import("helper") is not None
        assert reg.loaded_files == {"helper": str(libdir / "helper.py")}
        assert reg.probe_missing == []
        assert reg.probe_rejected == []
