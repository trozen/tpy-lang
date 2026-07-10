"""Unit tests for the whole-run build cache (tpyc/build_cache.py)."""

import json
import os
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
        flags = dict(release=False, default_int="Int32", pch=True,
                     no_main=False, emit_source=False, thir_codegen=False,
                     pcre2="bundled", mbedtls="bundled", date="bundled")
        flags.update(over)
        return _cache_options_key(
            Namespace(**flags), Path(entry), [Path(d) for d in lib_dirs],
            CppCompilerConfig(compiler=list(compiler)))

    base = make_key()
    assert make_key() == base
    variants = [dict(release=True), dict(default_int="BigInt"),
                dict(pch=False), dict(no_main=True), dict(emit_source=True),
                dict(thir_codegen=True), dict(pcre2="system"),
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
