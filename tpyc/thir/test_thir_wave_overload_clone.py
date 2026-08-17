"""Pins for the unemitted @overload-clone skip.

An `@auto_readonly` method that is ALSO an @overload impl expands into two
clones, only one of which `_collect_method_overload_groups` registers; the
AST emits the registered one once per stub and never names the twin. THIR
must not attempt the twin -- and must still attempt both halves of an
ordinary auto_readonly pair, which is the dangerous direction: an
over-broad skip loses routing SILENTLY (no fallback is tallied, so the
ratchet cannot see it, and the AST re-emits the body byte-identically).
"""

from __future__ import annotations

from .lower import unemitted_overload_clones
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
)

_PRELUDE = "from typing import overload\nfrom tpy import Int32, Span, readonly, auto_readonly\n"


def _clone_ids(source: str):
    """(dead-clone id set, every same-named method) for the entry module."""
    compiler, modules = _compile(source)
    entry = _entry(modules)
    return (unemitted_overload_clones(entry.ast, entry.analyzer),
            entry.ast.records)


_OVERLOAD_SRC = _PRELUDE + (
    "class Box[T]:\n"
    "    _data: list[T]\n"
    "    def __init__(self) -> None:\n"
    "        self._data = []\n"
    "    def add(self, item: T) -> None:\n"
    "        self._data.append(item)\n"
    "    @overload\n"
    "    @auto_readonly\n"
    "    def __getitem__(self, index: Int32) -> T: ...\n"
    "    @overload\n"
    "    def __getitem__(self, index: slice) -> Span[readonly[T]]: ...\n"
    "    def __getitem__(self, index: Int32 | slice) -> T | Span[readonly[T]]:\n"
    "        if isinstance(index, slice):\n"
    "            return self._data[0:1]\n"
    "        return self._data[index]\n"
    "def main() -> None:\n"
    "    b = Box[Int32]()\n"
    "    b.add(1)\n"
    "    print(b[0])\n"
    "main()\n"
)


class TestOverloadCloneSkipped:
    def test_overloaded_auto_readonly_routes(self):
        _assert_routes_byte_identical(_OVERLOAD_SRC)

    def test_exactly_one_clone_is_dead(self):
        """The registered impl keeps its group; only its twin is skipped."""
        dead, records = _clone_ids(_OVERLOAD_SRC)
        impls = [m for r in records for m in r.methods
                 if m.name == "__getitem__" and not m.is_overload_stub]
        assert len(impls) == 2
        assert len([m for m in impls if id(m) in dead]) == 1


class TestOrdinaryClonePairStillAttempted:
    """The over-skip direction. An auto_readonly pair with NO overloads must
    keep BOTH halves attempted -- a skip there loses routing with no fallback
    tallied, so neither the ratchet nor the byte-diff would notice."""

    _SRC = _PRELUDE + (
        "class Holder:\n"
        "    _v: Int32\n"
        "    def __init__(self) -> None:\n"
        "        self._v = 7\n"
        "    @auto_readonly\n"
        "    def get(self) -> Int32:\n"
        "        return self._v\n"
        "def main() -> None:\n"
        "    h = Holder()\n"
        "    print(h.get())\n"
        "main()\n"
    )

    def test_no_clone_marked_dead(self):
        dead, _ = _clone_ids(self._SRC)
        assert dead == set()

    def test_both_halves_route(self):
        _assert_routes_byte_identical(self._SRC)


class TestPlainOverloadSetUnaffected:
    """An @overload set with no auto_readonly has one impl and nothing to
    skip -- the registered-name lookup must not sweep it in."""

    _SRC = _PRELUDE + (
        "class Pick:\n"
        "    @overload\n"
        "    def at(self, i: Int32) -> Int32: ...\n"
        "    @overload\n"
        "    def at(self, i: bool) -> Int32: ...\n"
        "    def at(self, i: Int32 | bool) -> Int32:\n"
        "        return 1\n"
        "def main() -> None:\n"
        "    p = Pick()\n"
        "    print(p.at(2))\n"
        "main()\n"
    )

    def test_no_clone_marked_dead(self):
        dead, _ = _clone_ids(self._SRC)
        assert dead == set()
