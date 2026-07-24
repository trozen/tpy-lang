"""Toolchain capability preflight: probe, cache, auto-detect skip, floors."""

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

from .toolchain import (
    CppCompilerConfig, ToolchainUnsupportedError,
    _auto_detect_compiler, _probe_toolchain, toolchain_is_viable,
)


def _write_fake_compiler(bin_dir: Path, name: str, exit_code: int) -> Path:
    """A PATH-visible compiler stub that logs each invocation to <path>.count."""
    path = bin_dir / name
    path.write_text(
        "#!/bin/sh\n"
        f'echo run >> "$0.count"\n'
        + ('echo "error: no member named expected" >&2\n' if exit_code else "")
        + f"exit {exit_code}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _invocations(fake: Path) -> int:
    count = Path(str(fake) + ".count")
    return len(count.read_text().splitlines()) if count.exists() else 0


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolated PATH + probe cache so no real toolchain or cache leaks in."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("CXX", raising=False)
    return bin_dir


def test_auto_detect_skips_nonviable_gxx(sandbox):
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _write_fake_compiler(sandbox, "clang++-19", exit_code=0)
    assert _auto_detect_compiler() == ["clang++-19"]


def test_auto_detect_falls_through_to_zig(sandbox):
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _write_fake_compiler(sandbox, "zig", exit_code=0)
    compiler = _auto_detect_compiler()
    assert os.path.basename(compiler[0]) == "zig"
    assert compiler[1] == "c++"


def test_auto_detect_errors_when_only_nonviable(sandbox):
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _write_fake_compiler(sandbox, "g++-10", exit_code=1)
    with pytest.raises(ToolchainUnsupportedError) as exc:
        _auto_detect_compiler()
    msg = str(exc.value)
    assert "C++23" in msg
    assert "g++-11" in msg
    assert "g++-10" in msg          # also-rejected list
    assert "tpy-lang[bundled]" in msg


def test_explicit_cxx_warns_and_proceeds(sandbox, capsys):
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    config = CppCompilerConfig.from_env(cxx="g++-11")
    assert config.compiler[0] == "g++-11"      # explicit choice honored
    err = capsys.readouterr().err
    assert "C++23" in err and "Proceeding with the explicit selection" in err


def test_env_cxx_warns_and_proceeds(sandbox, monkeypatch, capsys):
    fake = _write_fake_compiler(sandbox, "weird++", exit_code=1)
    monkeypatch.setenv("CXX", str(fake))
    config = CppCompilerConfig.from_env(cxx="auto")
    assert config.compiler[0] == str(fake)
    assert "C++23" in capsys.readouterr().err


def test_probe_result_is_cached(sandbox):
    fake = _write_fake_compiler(sandbox, "g++-14", exit_code=0)
    assert toolchain_is_viable(["g++-14"])
    assert toolchain_is_viable(["g++-14"])
    assert _invocations(fake) == 1

    bad = _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    assert not toolchain_is_viable(["g++-11"])
    assert not toolchain_is_viable(["g++-11"])
    assert _invocations(bad) == 1


def test_replaced_binary_reprobes(sandbox):
    fake = _write_fake_compiler(sandbox, "g++-14", exit_code=1)
    assert not toolchain_is_viable(["g++-14"])
    # Same path, new content/size -- e.g. a fixed compiler install.
    fake.write_text("#!/bin/sh\necho run >> \"$0.count\"\nexit 0\n# longer\n")
    assert toolchain_is_viable(["g++-14"])


def test_zig_exempt_from_probe(sandbox):
    fake = _write_fake_compiler(sandbox, "zig", exit_code=1)  # would fail if run
    assert toolchain_is_viable([str(fake), "c++"])
    assert _invocations(fake) == 0


def test_probe_failure_writes_log(sandbox):
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    viable, log = _probe_toolchain(["g++-11"])
    assert not viable
    assert log is not None and "expected" in log.read_text()


def test_list_compilers_sections_nonviable(sandbox, capsys):
    from .toolchain import list_compilers
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _write_fake_compiler(sandbox, "clang++-19", exit_code=0)
    list_compilers()
    out = capsys.readouterr().out
    available, _, unsupported = out.partition("Unsupported (cannot compile")
    assert "clang++-19" in available
    assert "g++-11" not in available
    assert "g++-11" in unsupported
    assert "Default (--cxx auto): clang++-19" in out
    assert "Probe results cached in:" in out


def test_list_compilers_none_viable(sandbox, capsys):
    from .toolchain import list_compilers
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    list_compilers()
    out = capsys.readouterr().out
    assert "Default (--cxx auto): none viable" in out


def test_list_compilers_probe_notice_only_uncached(sandbox, capsys):
    from .toolchain import list_compilers
    _write_fake_compiler(sandbox, "g++-14", exit_code=0)
    list_compilers()
    assert "Probing C++ toolchains" in capsys.readouterr().out
    list_compilers()
    assert "Probing C++ toolchains" not in capsys.readouterr().out


def test_family_alias_picks_best_viable(sandbox):
    _write_fake_compiler(sandbox, "g++-14", exit_code=1)
    _write_fake_compiler(sandbox, "g++-13", exit_code=0)
    config = CppCompilerConfig.from_env(cxx="gcc")
    assert config.compiler[0] == "g++-13"


def test_family_alias_none_viable_warns_on_best(sandbox, capsys):
    _write_fake_compiler(sandbox, "g++-14", exit_code=1)
    config = CppCompilerConfig.from_env(cxx="gcc")
    assert config.compiler[0] == "g++-14"      # fallback: best-versioned
    assert "C++23" in capsys.readouterr().err


def test_repl_detect_backend_warns_and_honors_choice(sandbox, capsys):
    from .repl_backends import detect_backend
    _write_fake_compiler(sandbox, "g++-12", exit_code=1)
    _write_fake_compiler(sandbox, "clang++-19", exit_code=0)
    temp_dir = sandbox.parent / "t"
    (temp_dir / "build").mkdir(parents=True)
    backend = detect_backend("gcc-12", temp_dir, "m")
    err = capsys.readouterr().err
    assert "cannot build TurboPython output" in err
    assert "g++-12" in backend.name            # explicit choice honored


def test_repl_auto_detect_uses_probed_chain(sandbox, capsys):
    from .repl_backends import detect_backend
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _write_fake_compiler(sandbox, "clang++-19", exit_code=0)
    temp_dir = sandbox.parent / "t_auto"
    (temp_dir / "build").mkdir(parents=True)
    backend = detect_backend("auto", temp_dir, "m")
    assert "clang++-19" in backend.name


def test_repl_auto_detect_exits_when_only_nonviable(sandbox, capsys):
    from .repl_backends import detect_backend
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    with pytest.raises(SystemExit):
        detect_backend("auto", sandbox.parent / "t_none", "m")
    assert "C++23" in capsys.readouterr().err


def test_info_reports_unsupported_toolchain(sandbox, capsys):
    from .cli import _print_info
    _write_fake_compiler(sandbox, "g++-11", exit_code=1)
    _print_info("tpy")
    assert "found but unsupported" in capsys.readouterr().out


def test_env_cxx_multi_token(sandbox, monkeypatch, capsys):
    fake = _write_fake_compiler(sandbox, "weird++", exit_code=1)
    monkeypatch.setenv("CXX", f"{fake} -m32")
    config = CppCompilerConfig.from_env(cxx="auto")
    assert config.compiler[:2] == [str(fake), "-m32"]
    assert "C++23" in capsys.readouterr().err


def test_family_alias_no_binaries_is_not_found(sandbox):
    from .toolchain import CompilerNotFoundError
    with pytest.raises(CompilerNotFoundError):
        CppCompilerConfig.from_env(cxx="gcc")


def test_unwritable_cache_degrades_to_uncached_probe(sandbox, tmp_path,
                                                     monkeypatch):
    fake = _write_fake_compiler(sandbox, "g++-14", exit_code=0)
    ro = tmp_path / "ro-cache"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setenv("XDG_CACHE_HOME", str(ro))
    try:
        assert toolchain_is_viable(["g++-14"])
        assert toolchain_is_viable(["g++-14"])
        assert _invocations(fake) == 2      # no cache -> re-probed, no crash
    finally:
        ro.chmod(0o755)


def test_python_floor_message(monkeypatch):
    from .cli import _require_python_floor
    monkeypatch.setattr(sys, "version_info", (3, 11, 9))
    with pytest.raises(SystemExit, match="requires Python 3.12"):
        _require_python_floor()


def test_probe_covers_runtime_cxx23_includes():
    # The probe must exercise every C++23-era header the runtime includes,
    # or a compiler that has some-but-not-all (g++-12: <expected> yes,
    # <format> no) probes viable and then dies mid-build.
    import re
    from . import get_runtime_dir
    from .toolchain import _PROBE_SOURCE
    cxx23_headers = {
        "expected", "format", "print", "generator", "stacktrace",
        "spanstream", "flat_map", "flat_set", "mdspan",
    }
    used: set[str] = set()
    for f in Path(get_runtime_dir()).joinpath("cpp").rglob("*.[ch]pp"):
        used.update(
            h for h in re.findall(r"#include <(\w+)>", f.read_text())
            if h in cxx23_headers)
    missing = {h for h in used if f"#include <{h}>" not in _PROBE_SOURCE}
    assert not missing, f"probe TU missing runtime C++23 headers: {missing}"


def test_entry_modules_parse_on_python_311():
    # The floor guard in cli.py is only reachable if every module imported
    # before it PARSES under the old interpreter -- pin the entry chain to
    # pre-PEP-695 syntax.
    root = Path(__file__).parent
    for mod in ["__init__.py", "cli.py", "toolchain.py"]:
        src = (root / mod).read_text()
        ast.parse(src, filename=mod, feature_version=(3, 11))
