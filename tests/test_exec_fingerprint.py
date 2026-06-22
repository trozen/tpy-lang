# Unit tests for the exec-pass cache key (compute_exec_fingerprint).
# The key must depend on compiler OUTPUT (generated C++ + build env), not
# compiler SOURCE -- so a tpyc/lib edit that leaves the emitted C++ identical
# reuses the cache, while a change to generated content, runtime headers, or
# the toolchain re-keys it.

from pathlib import Path

import conftest
from conftest import compute_exec_fingerprint


def _modules(tmp_path: Path, hpp_text: str, cpp_text: str):
    """One local + one stdlib module, both with generated files on disk."""
    local_cpp = tmp_path / "main.cpp"
    local_cpp.write_text(cpp_text)
    std_hpp = tmp_path / "re.hpp"
    std_hpp.write_text(hpp_text)
    return [
        ("main", None, local_cpp, True),
        ("re", std_hpp, None, False),  # stdlib module: is_local=False
    ]


def _fp(tmp_path: Path, modules) -> str:
    return compute_exec_fingerprint(tmp_path, modules, [], [])


def test_stable_when_compiler_source_hash_changes(tmp_path, monkeypatch):
    """A tpyc/libtpy edit that doesn't change emitted C++ must not re-key.

    Regression guard against re-folding _stdlib_cache_key (which carries
    _tpyc_hash / _libtpy_hash) back into the exec marker.
    """
    modules = _modules(tmp_path, "// hpp\n", "// cpp\n")
    before = _fp(tmp_path, modules)

    monkeypatch.setattr(conftest, "_tpyc_hash", lambda: "DIFFERENT_TPYC")
    monkeypatch.setattr(conftest, "_libtpy_hash", lambda: "DIFFERENT_LIBTPY")
    assert _fp(tmp_path, modules) == before


def test_changes_when_generated_cpp_changes(tmp_path):
    a = _modules(tmp_path, "// hpp\n", "// cpp v1\n")
    fp_a = _fp(tmp_path, a)
    (tmp_path / "main.cpp").write_text("// cpp v2\n")
    assert _fp(tmp_path, a) != fp_a


def test_changes_when_stdlib_header_changes(tmp_path):
    """The stdlib module's generated .hpp is hashed even though is_local=False."""
    modules = _modules(tmp_path, "// hpp v1\n", "// cpp\n")
    fp1 = _fp(tmp_path, modules)
    (tmp_path / "re.hpp").write_text("// hpp v2\n")
    assert _fp(tmp_path, modules) != fp1


def test_changes_when_runtime_or_toolchain_changes(tmp_path, monkeypatch):
    modules = _modules(tmp_path, "// hpp\n", "// cpp\n")
    before = _fp(tmp_path, modules)

    monkeypatch.setattr(conftest, "_runtime_hash", lambda: "DIFFERENT_RUNTIME")
    after_runtime = _fp(tmp_path, modules)
    assert after_runtime != before

    monkeypatch.undo()
    monkeypatch.setattr(conftest.CPP_CONFIG, "std", "c++26")
    assert _fp(tmp_path, modules) != before


def test_changes_when_stdlib_output_hash_changes(tmp_path):
    """A change in the full linked stdlib set re-keys even an unimported case.

    Every case binary links the whole stdlib .o set with no dead-stripping, so
    a module the case doesn't import still determines the binary.
    """
    modules = _modules(tmp_path, "// hpp\n", "// cpp\n")
    fp_a = compute_exec_fingerprint(tmp_path, modules, [], [], stdlib_output_hash="A")
    fp_b = compute_exec_fingerprint(tmp_path, modules, [], [], stdlib_output_hash="B")
    assert fp_a != fp_b
