"""Unit tests for the whole-run build cache (tpyc/build_cache.py)."""

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tpyc.build_cache import (
    SCHEMA_VERSION, BuildManifest, check_up_to_date, compute_build_dir,
    dir_listing_entry, file_entry, manifest_path, write_manifest,
)
from tpyc.modules.resolver import ModuleResolver


@pytest.fixture
def world(tmp_path: Path):
    """A minimal self-consistent recorded build."""
    app = tmp_path / "app"
    lib = tmp_path / "lib"
    pkg = tmp_path / "pkg"
    build = tmp_path / "build"
    for d in (app, lib, pkg, build):
        d.mkdir()

    (app / "prog.py").write_text("import util\nimport math\n")
    (app / "util.py").write_text("def f() -> None:\n    pass\n")
    (lib / "math.py").write_text("def floor(x: float) -> int:\n    return 0\n")
    (pkg / "a.py").write_text("A = 1\n")
    binary = build / "prog"
    binary.write_text("BINARY")
    fake_cxx = tmp_path / "g++"
    fake_cxx.write_text("#!/bin/sh\n")

    files = [file_entry(str(app / "prog.py")),
             file_entry(str(app / "util.py")),
             file_entry(str(lib / "math.py"))]
    tc_entry = file_entry(str(fake_cxx))
    manifest = BuildManifest(
        options_key={"variant": "debug", "compiler": ["g++"]},
        toolchain={"resolved": str(fake_cxx), "size": tc_entry["size"],
                   "mtime_ns": tc_entry["mtime_ns"]},
        files=files,
        dir_listings=[dir_listing_entry(pkg, "*.py")],
        resolver={"base_dir": str(app), "extra_dirs": [str(lib)],
                  "extra_extensions": []},
        resolutions={"util": str(app / "util.py"),
                     "math": str(lib / "math.py"),
                     "optional_mod": None},
        must_not_exist=[str(app / "banned.py")],
        warnings=["prog.py:1: warning: something"],
        binary=file_entry(str(binary)),
    )
    write_manifest(build, manifest)
    return {"tmp": tmp_path, "app": app, "lib": lib, "pkg": pkg,
            "build": build, "binary": binary,
            "key": {"variant": "debug", "compiler": ["g++"]}}


def check(world):
    return check_up_to_date(world["build"], world["key"])


def test_hit_returns_binary_and_warnings(world):
    hit = check(world)
    assert hit is not None
    assert hit.binary == str(world["binary"])
    assert hit.warnings == ["prog.py:1: warning: something"]


def test_missing_manifest_misses(world, tmp_path):
    assert check_up_to_date(tmp_path / "nowhere", world["key"]) is None


def test_options_key_mismatch_misses(world):
    assert check_up_to_date(world["build"],
                            {"variant": "release", "compiler": ["g++"]}) is None


def test_schema_mismatch_misses(world):
    p = manifest_path(world["build"])
    data = json.loads(p.read_text())
    data["schema"] = SCHEMA_VERSION + 1
    p.write_text(json.dumps(data))
    assert check(world) is None


def test_rewrite_same_content_still_hits(world):
    # New mtime, same bytes: the stat fast path fails, the hash fallback
    # confirms the content and the run still skips. utime AFTER the write
    # (a write resets mtime), pinned so the stat mismatch is deterministic.
    src = world["app"] / "util.py"
    src.write_text(src.read_text())
    os.utime(src, ns=(12345, 12345))
    assert check(world) is not None


def test_changed_content_misses(world):
    # Same-size replacement: only the hash can catch it. Pin a different
    # mtime explicitly -- on a fast host the rewrite can land within the
    # kernel's coarse-clock tick and stat-match the recorded entry (the
    # documented same-size+same-tick blind spot), flaking the test.
    src = world["app"] / "util.py"
    src.write_text("def g() -> None:\n    pass\n")
    os.utime(src, ns=(12345, 12345))
    assert check(world) is None


def test_deleted_input_misses(world):
    (world["app"] / "util.py").unlink()
    assert check(world) is None


def test_new_shadowing_file_misses(world):
    # `math` was recorded at lib/math.py; a new app/math.py wins the
    # search order now, so resolution replay must force a rebuild.
    (world["app"] / "math.py").write_text("def floor(x: float) -> int:\n"
                                          "    return 99\n")
    assert check(world) is None


def test_recorded_miss_becoming_resolvable_misses(world):
    (world["app"] / "optional_mod.py").write_text("X = 1\n")
    assert check(world) is None


def test_must_not_exist_violated_misses(world):
    (world["app"] / "banned.py").write_text("")
    assert check(world) is None


def test_dir_listing_root_deleted_misses(world):
    import shutil
    shutil.rmtree(world["pkg"])
    assert check(world) is None


def test_dir_listing_gains_file_misses(world):
    (world["pkg"] / "b.py").write_text("B = 2\n")
    assert check(world) is None


def test_dir_listing_loses_file_misses(world):
    (world["pkg"] / "a.py").unlink()
    assert check(world) is None


def test_deleted_binary_misses(world):
    world["binary"].unlink()
    assert check(world) is None


def test_touched_toolchain_misses(world):
    os.utime(world["tmp"] / "g++", ns=(1, 1))
    assert check(world) is None


def test_corrupt_manifest_misses(world):
    manifest_path(world["build"]).write_text("{not json")
    assert check(world) is None


def test_compute_build_dir_matches_buildlayout(tmp_path):
    # compute_build_dir mirrors BuildLayout (which the warm path must not
    # import); this pins the two together.
    from tpyc.compiler import BuildLayout
    for flat in (False, True):
        for variant in ("debug", "release"):
            layout = BuildLayout(tmp_path, "prog", build_variant=variant,
                                 flat=flat)
            assert compute_build_dir(tmp_path, "prog", variant,
                                     flat=flat) == layout.build_dir


def test_options_key_sensitive_to_each_flag(tmp_path):
    # Guards the missing-field failure mode: every output-affecting option
    # must land in the key (whole-dict comparison handles the rest).
    from argparse import Namespace

    from tpyc.cli import _cache_options_key
    from tpyc.toolchain import CppCompilerConfig

    def make_key(entry="/x/prog.py", lib_dirs=(), compiler=("g++",), **over):
        flags = dict(debug=False, default_int="Int32", pch=True,
                     no_main=False, emit_source=False,
                     pcre2="bundled", mbedtls="bundled", date="bundled")
        flags.update(over)
        return _cache_options_key(
            Namespace(**flags), Path(entry), [Path(d) for d in lib_dirs],
            CppCompilerConfig(compiler=list(compiler)))

    base = make_key()
    assert make_key() == base
    variants = [dict(debug=True), dict(default_int="BigInt"),
                dict(pch=False), dict(no_main=True), dict(emit_source=True),
                dict(pcre2="system"),
                dict(mbedtls="none"), dict(date="auto"),
                dict(entry="/x/other.py"), dict(lib_dirs=("/L",)),
                dict(compiler=("clang++",))]
    for over in variants:
        assert make_key(**over) != base, over


def test_resolver_logs_hits_and_misses(tmp_path):
    (tmp_path / "util.py").write_text("")
    resolver = ModuleResolver(tmp_path)
    assert resolver.resolve("util") is not None
    assert resolver.resolve("nope") is None
    assert resolver.resolution_log == {"util": str(tmp_path / "util.py"),
                                       "nope": None}


# --- read-time input capture (the mid-build-edit staleness fix) ---------
#
# The manifest must certify the content the compiler READ, not the on-disk
# state when the manifest is written after the C++ build -- a file edited
# in between must MISS on the next run (rebuild), never warm-hit the stale
# binary. These drive the real Compiler front-end (no C++ toolchain) and
# then record through the same helper the CLI uses.

MACRO_SRC = ("# tpy: macro_module\n"
             "from tpyc.macro_api import ClassInfo, class_macro\n"
             "\n"
             "\n"
             "@class_macro\n"
             "def tag(cls: ClassInfo) -> None:\n"
             "    pass\n")


def _compile_and_record(tmp_path: Path, with_macro: bool = False,
                        edit_before_record: Callable[[Path], None] | None = None):
    from types import SimpleNamespace

    from tpyc import get_lib_dir, get_runtime_dir
    from tpyc.cli import _record_build_manifest
    from tpyc.compiler import Compiler
    from tpyc.toolchain import CppCompilerConfig

    app = tmp_path / "app"
    app.mkdir()
    if with_macro:
        (app / "tagmac.py").write_text(MACRO_SRC)
        (app / "prog.py").write_text(
            "from tagmac import tag\n"
            "\n"
            "\n"
            "@tag\n"
            "class Thing:\n"
            "    x: int\n"
            "\n"
            "\n"
            "print(1)\n")
    else:
        (app / "util.py").write_text('def f() -> None:\n    print("A")\n')
        (app / "prog.py").write_text("import util\n\nutil.f()\n")

    compiler = Compiler(app / "prog.py", lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()

    if edit_before_record is not None:
        edit_before_record(app)  # simulates the mid-C++-build edit

    build = tmp_path / "build"
    build.mkdir()
    binary = build / "prog"
    binary.write_text("BINARY")
    key = {"k": 1}
    _record_build_manifest(
        key, build, app / "prog.py", modules, compiler,
        CppCompilerConfig(compiler=["sh"]),  # any stat-able binary
        runtime_dir=get_runtime_dir(), runtime_cpp_sources=[],
        third_party_plan=SimpleNamespace(c_sources=[], extra_include_dirs=[]),
        warning_messages=[], binary_path=binary)
    return build, key


def _edit(path: Path, text: str) -> None:
    path.write_text(text)
    os.utime(path, ns=(999, 999))  # deterministic stat mismatch


def test_record_after_compile_no_edit_hits(tmp_path):
    build, key = _compile_and_record(tmp_path)
    assert check_up_to_date(build, key) is not None


def test_module_edited_after_compile_misses(tmp_path):
    build, key = _compile_and_record(
        tmp_path,
        edit_before_record=lambda app: _edit(
            app / "util.py", 'def f() -> None:\n    print("B")\n'))
    assert check_up_to_date(build, key) is None


def test_macro_edited_after_compile_misses(tmp_path):
    build, key = _compile_and_record(
        tmp_path, with_macro=True,
        edit_before_record=lambda app: _edit(
            app / "tagmac.py", MACRO_SRC + "\n# changed\n"))
    assert check_up_to_date(build, key) is None
