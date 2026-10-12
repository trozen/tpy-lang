# Unit tests for the exec-pass cache key (compute_exec_fingerprint).
# The key must depend on compiler OUTPUT (generated C++ + build env), not
# compiler SOURCE -- so a tpyc/lib edit that leaves the emitted C++ identical
# reuses the cache, while a change to generated content, runtime headers, or
# the toolchain re-keys it.

import os
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


def _stdlib_build(root: Path, cpp_text: str = "int x;\n") -> tuple[list[Path], list[list[str]]]:
    """A generated stdlib tree under *root* and the compile recipe over it."""
    (root / "src").mkdir(parents=True)
    cpp = root / "src" / "re.cpp"
    cpp.write_text(cpp_text)
    return [cpp], [["c++", "-I", str(root / "include"), str(cpp)]]


def test_stdlib_output_hash_ignores_the_build_dir(tmp_path):
    """The stdlib cache regenerates into a fresh dir on every rebuild; the same
    C++ must keep every exec marker valid."""
    hashes = []
    for name in ("a", "b"):
        gen, recipe = _stdlib_build(tmp_path / name)
        hashes.append(conftest._stdlib_output_hash(gen, tmp_path / name, recipe))
    assert hashes[0] == hashes[1]

    gen, recipe = _stdlib_build(tmp_path / "c", "int y;\n")
    assert conftest._stdlib_output_hash(gen, tmp_path / "c", recipe) != hashes[0]


def test_stdlib_output_hash_ignores_the_checkout(tmp_path, monkeypatch):
    """Exec markers are shared across worktrees."""
    gen, _ = _stdlib_build(tmp_path / "build")
    hashes = []
    for name in ("wt1", "wt2"):
        root = tmp_path / name
        monkeypatch.setattr(conftest, "PROJECT_ROOT", root)
        recipe = [["cc", "-I", str(root / "runtime/cpp/include"), str(root / "rt/socket.cpp")]]
        hashes.append(conftest._stdlib_output_hash(gen, tmp_path / "build", recipe))
    assert hashes[0] == hashes[1]


def test_stdlib_output_hash_with_the_build_dir_inside_the_checkout(tmp_path, monkeypatch):
    """A shared cache dir pointed into the checkout nests the build dir in the
    checkout root; it must hash as a build dir anywhere else does."""
    root = tmp_path / "wt"
    monkeypatch.setattr(conftest, "PROJECT_ROOT", root)
    hashes = []
    for build in (root / "cache" / "build", tmp_path / "cache" / "build"):
        gen, recipe = _stdlib_build(build)
        hashes.append(conftest._stdlib_output_hash(gen, build, recipe))
    assert hashes[0] == hashes[1]


def test_stdlib_output_hash_folds_the_compile_recipe(tmp_path):
    """A flag or source-list edit in tpyc/build/ or the harness rebuilds the
    objects every case links without changing any generated C++."""
    gen, recipe = _stdlib_build(tmp_path)
    base = conftest._stdlib_output_hash(gen, tmp_path, recipe)
    flagged = [[*recipe[0][:1], "-DA", *recipe[0][1:]]]
    assert conftest._stdlib_output_hash(gen, tmp_path, flagged) != base
    added = [*recipe, ["c++", "runtime/cpp/src/socket.cpp"]]
    assert conftest._stdlib_output_hash(gen, tmp_path, added) != base


def test_exec_marker_hit_refreshes_the_marker(tmp_path, monkeypatch):
    # the cache sweep ages markers by mtime, so a hit must count as a use
    monkeypatch.setattr(conftest, "_exec_results_dir", lambda: tmp_path)
    marker = tmp_path / "abc"
    marker.write_text("case\n")
    os.utime(marker, (0, 0))
    assert conftest.exec_pass_is_cached("abc")
    assert marker.stat().st_mtime > 0
    assert not conftest.exec_pass_is_cached("missing")
    # a marker that cannot be refreshed (a read-only cache) is still a hit
    def read_only(*args, **kwargs):
        raise PermissionError(args[0])
    monkeypatch.setattr(conftest.os, "utime", read_only)
    assert conftest.exec_pass_is_cached("abc")
