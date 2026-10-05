"""The strict warning set per toolchain: GCC 13 alone drops
-Wdangling-reference, whose heuristic flags a key lambda passed to a
reference-returning runtime helper (`max_key(a, b, [](...) {...})`).
Toolchain-free: the version probe is stubbed.
"""

import pytest

from tpyc import toolchain
from tpyc.toolchain import strict_warn_flags


@pytest.mark.parametrize("major, dropped", [(13, True), (14, False), (None, False)])
def test_gcc_dangling_reference_by_major(monkeypatch, major, dropped):
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: "gcc")
    monkeypatch.setattr(toolchain, "_gcc_major", lambda cxx: major)
    assert ("-Wno-dangling-reference" in strict_warn_flags(["g++"])) == dropped


def test_clang_keeps_its_set(monkeypatch):
    monkeypatch.setattr(toolchain, "_detect_compiler_family", lambda cxx: "clang")
    assert "-Wno-dangling-reference" not in strict_warn_flags(["clang++"])
