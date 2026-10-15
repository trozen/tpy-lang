"""The build target as one fact: `CppCompilerConfig.target_platform`, the
platform table, and the two outputs that keep a "platform" filter as a
condition instead of deciding it -- include guards and sources.cmake."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest

from tpyc import get_runtime_dir
from tpyc import toolchain
from tpyc.build.third_party import get_lib
from tpyc.codegen_cpp.generator import _write_settings_include
from tpyc.compiler import BuildLayout, admitted_names
from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import FrontendDirectives, FrontendModule
from tpyc.toolchain import PLATFORMS, CppCompilerConfig, host_os, platform_matches


@pytest.fixture
def probe(monkeypatch) -> list[tuple[str, ...]]:
    """Stub the `-dumpmachine` probe: a command's triple is the value of its
    `--target=` / `-target` flag, else the one its compiler name maps to."""
    seen: list[tuple[str, ...]] = []
    triples = {"osx-clang++": "arm64-apple-darwin24", "lx-g++": "x86_64-linux-gnu",
               "wasm-cc": "wasm32-unknown-unknown", "broken-cc": ""}

    def fake(cmd: tuple[str, ...]) -> str:
        seen.append(cmd)
        for i, arg in enumerate(cmd):
            if arg.startswith("--target="):
                return arg.removeprefix("--target=")
            if arg == "-target":
                return cmd[i + 1]
        return triples[cmd[0]]

    monkeypatch.setattr(toolchain, "compiler_dumpmachine", fake)
    return seen


def test_target_platform_from_the_compiler_triple(probe) -> None:
    assert CppCompilerConfig(compiler=["osx-clang++"]).target_platform == "darwin"
    assert CppCompilerConfig(compiler=["lx-g++"]).target_platform == "linux"


def test_target_platform_falls_back_to_the_host(probe) -> None:
    """A failed probe or a triple no `PLATFORMS` key names is the host."""
    assert CppCompilerConfig(compiler=["broken-cc"]).target_platform == host_os()
    assert CppCompilerConfig(compiler=["wasm-cc"]).target_platform == host_os()


def test_target_platform_reads_a_target_in_the_compiler_command(probe) -> None:
    """A `--target=` on the compiler command itself reaches the probe."""
    config = CppCompilerConfig(compiler=["lx-g++", "--target=x86_64-pc-windows-msvc"])
    assert config.target_platform == "win32"


def test_target_platform_is_probed_once(probe) -> None:
    config = CppCompilerConfig(compiler=["osx-clang++"])
    assert config.target_platform == config.target_platform == "darwin"
    assert len(probe) == 1


def test_use_compiler_re_decides_the_target(probe) -> None:
    config = CppCompilerConfig(compiler=["osx-clang++"])
    config.use_compiler(["lx-g++"])
    assert config.compiler == ["lx-g++"]
    assert config.target_platform == "linux"


def test_host_os_is_a_platform_key() -> None:
    assert host_os() in PLATFORMS


def test_platform_matches_the_given_target() -> None:
    """A filter admits the build target it names, whatever machine runs
    tpyc; an unfiltered entry admits every target."""
    assert platform_matches(None, "linux") and platform_matches(None, "win32")
    assert platform_matches("darwin", "darwin")
    assert not platform_matches("darwin", "linux")
    assert platform_matches("win32", "win32")
    assert not platform_matches("linux", "freebsd")


def _includes(entries: list[tuple[str, str | None]]) -> str:
    out = io.StringIO()
    emitted: set[tuple[str, str | None]] = set()
    for header, platform in entries:
        _write_settings_include(out, header, platform, emitted)
    return out.getvalue()


def test_include_guard() -> None:
    assert _includes([("w.hpp", "win32"), ("<x.hpp>", None), ("<m.h>", "darwin")]) == (
        '#if defined(TPY_PLATFORM_WIN32)\n#include "w.hpp"\n#endif\n'
        '#include <x.hpp>\n'
        '#if defined(TPY_PLATFORM_DARWIN)\n#include <m.h>\n#endif\n')


def test_include_dedup() -> None:
    """One header under two platforms keeps both guards; a repeat is
    dropped; an unconditional include makes later guarded ones redundant."""
    assert _includes([("a.hpp", "linux"), ("a.hpp", "darwin"), ("a.hpp", "linux")]) == (
        '#if defined(TPY_PLATFORM_LINUX)\n#include "a.hpp"\n#endif\n'
        '#if defined(TPY_PLATFORM_DARWIN)\n#include "a.hpp"\n#endif\n')
    assert _includes([("a.hpp", None), ("a.hpp", "linux")]) == '#include "a.hpp"\n'
    assert _includes([("a.hpp", "linux"), ("a.hpp", None), ("a.hpp", "darwin")]) == (
        '#if defined(TPY_PLATFORM_LINUX)\n#include "a.hpp"\n#endif\n#include "a.hpp"\n')


def _cmake(tmp_path: Path, link_libs: list[tuple[str, str | None]],
           third_party: list[tuple[str, str | None]] = ()) -> str:
    layout = BuildLayout(tmp_path, "app")
    layout.include_dir.mkdir(parents=True)
    layout.src_dir.mkdir(parents=True)
    cpp = layout.src_dir / "app.cpp"
    cpp.write_text("// generated")
    runtime_cpp = get_runtime_dir() / "cpp"
    return layout.generate_cmake(
        runtime_include_dir=runtime_cpp / "include", cpp_files=[cpp],
        link_libs=link_libs, bundle_runtime=False,
        third_party_libs=[(get_lib(name, runtime_cpp), platform)
                          for name, platform in third_party]).read_text()


def test_cmake_plain_library_conditions(tmp_path: Path) -> None:
    """A filtered library is appended under its platform's CMake system
    name; a repeated name is appended at each entry and deduplicated after
    the appends."""
    text = _cmake(tmp_path, [("m", None), ("ws2_32", "win32"),
                             ("dl", "linux"), ("dl", "freebsd"),
                             ("pthread", "linux"), ("pthread", None)])
    assert "set(TPYC_LIBRARIES\n    m\n)\n" in text
    assert ('if(CMAKE_SYSTEM_NAME STREQUAL "Windows")\n'
            '    list(APPEND TPYC_LIBRARIES ws2_32)\nendif()\n') in text
    assert ('if(CMAKE_SYSTEM_NAME STREQUAL "Linux")\n'
            '    list(APPEND TPYC_LIBRARIES dl)\nendif()\n'
            'if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")\n'
            '    list(APPEND TPYC_LIBRARIES dl)\nendif()\n') in text
    assert ('endif()\nlist(APPEND TPYC_LIBRARIES pthread)\n'
            'list(REMOVE_DUPLICATES TPYC_LIBRARIES)\n') in text


def test_cmake_without_repeats_does_not_deduplicate(tmp_path: Path) -> None:
    assert "REMOVE_DUPLICATES" not in _cmake(tmp_path, [("m", None), ("dl", "linux")])


_REPEATED_LINKS = [("foo", "win32"), ("bar", None), ("foo", "linux"),
                   ("baz", "linux"), ("bar", "darwin"), ("baz", None)]


@pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake")
@pytest.mark.parametrize("target", sorted(PLATFORMS))
def test_cmake_link_order_matches_the_direct_build(
        tmp_path: Path, target: str) -> None:
    """A repeated library name separated by another library links where
    the direct build puts it on every target: its first ADMITTED entry
    (`foo(win32), bar, foo(linux)` is `bar;foo` on Linux, `foo;bar` on
    Windows)."""
    _cmake(tmp_path, _REPEATED_LINKS)
    script = tmp_path / "print.cmake"
    script.write_text(
        f'set(CMAKE_SYSTEM_NAME "{PLATFORMS[target].cmake_system}")\n'
        f'include("{tmp_path / "app.d" / "sources.cmake"}")\n'
        'message("${TPYC_LIBRARIES}")\n')
    run = subprocess.run(["cmake", "-P", str(script)], capture_output=True,
                         text=True, check=True)
    linked = run.stderr.strip().split(";")
    assert linked == admitted_names(_REPEATED_LINKS, target)
    # The literal lists, so a fault shared by both sides still fails.
    if target == "linux":
        assert linked[:2] == ["bar", "foo"]
    elif target == "win32":
        assert linked[:2] == ["foo", "bar"]


_REPEATED_MANAGED = [("zlib", "win32"), ("pcre2", None), ("zlib", "linux")]


@pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake")
@pytest.mark.parametrize("target", sorted(PLATFORMS))
@pytest.mark.parametrize("preset", [False, True])
def test_cmake_managed_link_order_matches_the_direct_build(
        tmp_path: Path, target: str, preset: bool) -> None:
    """A repeated managed library links at its first admitted entry, also
    when its target variable is already set before the include (a preset,
    or another tpyc output's sources.cmake included first)."""
    _cmake(tmp_path, [], _REPEATED_MANAGED)
    runtime_cpp = get_runtime_dir() / "cpp"
    libs = {name: get_lib(name, runtime_cpp) for name, _ in _REPEATED_MANAGED}
    script = tmp_path / "print.cmake"
    # The selectors' configure-time commands are not scriptable; stubs
    # leave every library on its bundled target.
    script.write_text(
        "function(add_subdirectory)\nendfunction()\n"
        f'set(CMAKE_SYSTEM_NAME "{PLATFORMS[target].cmake_system}")\n'
        + ("set(TPY_ZLIB_TARGET preset)\n" if preset else "")
        + f'include("{tmp_path / "app.d" / "sources.cmake"}")\n'
        'message("${TPYC_LIBRARIES}")\n')
    run = subprocess.run(["cmake", "-P", str(script)], capture_output=True,
                         text=True, check=True)
    expected = [libs[name].bundled_static_target
                for name in admitted_names(_REPEATED_MANAGED, target)]
    assert run.stderr.strip().split(";") == expected
    if target == "linux":
        assert expected == [libs["pcre2"].bundled_static_target,
                            libs["zlib"].bundled_static_target]


def test_cmake_libraries_keep_declaration_order(tmp_path: Path) -> None:
    """Link order matters to a static link: sources.cmake appends in the
    order the entries are declared, as the direct build passes them."""
    text = _cmake(tmp_path, [("foo", "linux"), ("bar", None), ("baz", "win32"), ("qux", None)])
    assert ('set(TPYC_LIBRARIES\n)\n'
            'if(CMAKE_SYSTEM_NAME STREQUAL "Linux")\n'
            '    list(APPEND TPYC_LIBRARIES foo)\nendif()\n'
            'list(APPEND TPYC_LIBRARIES bar)\n'
            'if(CMAKE_SYSTEM_NAME STREQUAL "Windows")\n'
            '    list(APPEND TPYC_LIBRARIES baz)\nendif()\n'
            'list(APPEND TPYC_LIBRARIES qux)\n') in text


def test_cmake_managed_library_repeat_configures_once(tmp_path: Path) -> None:
    """A repeated managed library keeps declaration order; a later entry's
    selector runs only when no earlier admitted one set its target, so the
    library is configured once, at its first admitted entry."""
    text = _cmake(tmp_path, [], [("zlib", "win32"), ("pcre2", None),
                                 ("zlib", "linux"), ("zlib", None)])
    win = text.index('if(CMAKE_SYSTEM_NAME STREQUAL "Windows")\n    # --- zlib ')
    pcre2 = text.index("\n# --- pcre2 ")
    linux = text.index('if((CMAKE_SYSTEM_NAME STREQUAL "Linux") AND '
                       'NOT DEFINED TPYC_ZLIB_SELECTED)\n    # --- zlib ')
    anywhere = text.index('if(NOT DEFINED TPYC_ZLIB_SELECTED)\n    # --- zlib ')
    unset = text.index("\nunset(TPYC_ZLIB_SELECTED)\n")
    assert win < pcre2 < linux < anywhere < unset
    # The marker is this file's own: set by each selector that runs.
    assert text.count("    set(TPYC_ZLIB_SELECTED TRUE)\nendif()\n") == 3
    assert "TPYC_PCRE2_SELECTED" not in text


def test_cmake_managed_library_condition(tmp_path: Path) -> None:
    """A filtered managed library's whole selector sits under the
    condition; an unfiltered one stays at top level."""
    text = _cmake(tmp_path, [], [("zlib", "darwin"), ("pcre2", None)])
    assert 'if(CMAKE_SYSTEM_NAME STREQUAL "Darwin")\n    # --- zlib ' in text
    assert "\n    list(APPEND TPYC_LIBRARIES ${TPY_ZLIB_TARGET})\nendif()\n" in text
    assert "\n# --- pcre2 " in text
    assert "\nlist(APPEND TPYC_LIBRARIES ${TPY_PCRE2_TARGET})\n" in text


def test_plugin_platform_filter_is_validated() -> None:
    """A plugin's directive platform is checked against the same table
    `__tpy__` uses, so a typo is a located plugin error, not a filter
    that never matches."""
    ok = lower_module(FrontendModule(
        qname="m", directives=FrontendDirectives(
            cpp_includes=(("w.hpp", "win32"),), link_libs=(("dl", "freebsd"),))), "p")
    assert ok.module is not None, ok.diagnostics
    bad = lower_module(FrontendModule(
        qname="m", directives=FrontendDirectives(link_libs=(("ws2", "windows"),))), "p")
    assert bad.module is None
    assert "'windows' is not one of 'linux', 'darwin', 'win32', 'freebsd'" in (
        bad.diagnostics[0].diagnostic.message)


# Each simulated target: the predefined macros its compiler sets (on top of
# the host's, which are cleared), TARGET_OS_IPHONE for an Apple target, the
# TPY_PLATFORM_* macro target_platform.hpp must define (None: none at all)
# and the sys.platform platform.hpp must give (None: an #error).
_SIMULATED_TARGETS = [
    (["-D__linux__"], None, "TPY_PLATFORM_LINUX", '"linux"'),
    (["-D__linux__", "-D__ANDROID__"], None, None, None),
    (["-D__APPLE__"], 0, "TPY_PLATFORM_DARWIN", '"darwin"'),
    (["-D__APPLE__"], 1, None, None),
    (["-D_WIN32"], None, "TPY_PLATFORM_WIN32", '"win32"'),
    (["-D__FreeBSD__=14"], None, "TPY_PLATFORM_FREEBSD", '"freebsd" "14"'),
]


@pytest.mark.parametrize("defines, iphone, macro, sys_platform", _SIMULATED_TARGETS)
def test_target_platform_macro_is_decided_once(
    tmp_path: Path, defines: list[str], iphone: int | None,
    macro: str | None, sys_platform: str | None,
) -> None:
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        pytest.skip("no C++ compiler")
    include = get_runtime_dir() / "cpp" / "include"
    if iphone is not None:
        (tmp_path / "TargetConditionals.h").write_text(f"#define TARGET_OS_IPHONE {iphone}\n")
    # Only the macro logic is under test: empty standard headers keep the
    # host's libc from reacting to the faked target macros.
    for header in ("bit", "string_view"):
        (tmp_path / header).write_text("")
    # Every macro target_platform.hpp tests, so the result does not depend on
    # the host OS (a macOS host predefines __APPLE__).
    cleared = ["-U__linux__", "-U__gnu_linux__", "-Ulinux", "-U__unix__", "-U__unix",
               "-U__APPLE__", "-U__MACH__", "-U_WIN32", "-U__FreeBSD__", "-U__ANDROID__"]
    base = [cxx, "-std=c++23", "-E", "-x", "c++", "-nostdinc", "-nostdinc++",
            *cleared, *defines, f"-I{tmp_path}", f"-I{include}"]
    macros = subprocess.run(
        [*base, "-dM", str(include / "tpy" / "target_platform.hpp")],
        capture_output=True, text=True, check=True).stdout
    defined = sorted(line.split()[1] for line in macros.splitlines()
                     if line.startswith("#define TPY_PLATFORM_"))
    assert defined == ([macro] if macro else [])
    result = subprocess.run([*base, str(include / "tpy" / "platform.hpp")],
                            capture_output=True, text=True)
    if sys_platform is None:
        assert result.returncode != 0 and "#error" in result.stderr
    else:
        assert f"sys_platform = {sys_platform};" in result.stdout
