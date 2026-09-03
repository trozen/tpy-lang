"""A HOISTED pointer-slot global's initializing write.

`scope_tracker.check_escape` hoists the SOURCE of a `g2 = g1` binding, which
makes that global's slot re-assignable: the plain `static T __global_slot_N =
init;` decl becomes a function-top `static std::optional<T> __global_slot_N;`
and the write lifts through it (`g = &*(__global_slot_N = init);`).
"""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at, _assert_byte_identical, _top_level,
                      _reject_tally)

PRELUDE = (
    "from tpy import Int32\n"
    "class Box:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)

_RECORD = PRELUDE + (
    "V = Box(2)\n"
    "S: Box = V\n"
    "def main() -> None:\n"
    "    print(V.n, S.n)\n"
    "main()\n"
)


class TestHoistedRecordGlobal:
    def test_routes_and_witnesses(self):
        top, w, fallback = _top_level(_RECORD)
        assert top is not None
        assert not fallback
        assert w.get("top_level.global_hoist_slot", 0) >= 1
        # The bound name is the ordinary pointer-copy write, unchanged.
        assert w.get("top_level.global_ptr_copy", 0) >= 1

    def test_byte_identical(self):
        _assert_byte_identical(_RECORD)


class TestHoistedContainerGlobal:
    """The container shapes share the tail: only the SLOT flavor changes, so
    each keeps the init lowering its non-hoisted sibling uses."""

    LIST = PRELUDE + (
        "V = [Box(1), Box(2)]\n"
        "S: list[Box] = V\n"
        "def main() -> None:\n"
        "    print(V[0].n, S[1].n)\n"
        "main()\n"
    )
    COMP = (
        "from tpy import Int32\n"
        "V = [i * 2 for i in range(3)]\n"
        "S: list[Int32] = V\n"
        "def main() -> None:\n"
        "    print(len(S), V[1])\n"
        "main()\n"
    )

    def test_record_element_list_routes(self):
        top, w, fallback = _top_level(self.LIST)
        assert top is not None
        assert not fallback
        assert w.get("top_level.global_hoist_slot", 0) >= 1

    def test_comprehension_init_routes(self):
        top, w, fallback = _top_level(self.COMP)
        assert top is not None
        assert not fallback
        # The comprehension still renders its stmt-expr through the shared
        # container arm; hoisting only swaps the slot flavor around it.
        assert w.get("top_level.global_slot_comp", 0) >= 1
        assert w.get("top_level.global_hoist_slot", 0) >= 1

    def test_list_byte_identical(self):
        _assert_byte_identical(self.LIST)

    def test_comp_byte_identical(self):
        _assert_byte_identical(self.COMP)


class TestHoistedGlobalBoundaries:
    def test_second_rvalue_write_stays_ast(self):
        # The hoisted slot is an OPTIONAL, so its reuse render is
        # `&*(slot = ..)`; GLOBAL_REBIND spells the plain `&(slot = ..)` and
        # must not claim it.
        src = PRELUDE + (
            "V = Box(2)\n"
            "S: Box = V\n"
            "V = Box(5)\n"
            "def main() -> None:\n"
            "    print(V.n, S.n)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "top_level:stmt.var_decl:top_level.global_hoist_shape")

    def test_unhoisted_global_keeps_the_plain_slot(self):
        # The same global with no second binding takes the plain
        # `static Box __global_slot_1 = Box(2);` decl -- the flag must not
        # leak into the unhoisted path.
        src = PRELUDE + (
            "V = Box(2)\n"
            "def main() -> None:\n"
            "    print(V.n)\n"
            "main()\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not fallback
        assert w.get("top_level.global_slot", 0) >= 1
        assert w.get("top_level.global_hoist_slot", 0) == 0
        _assert_byte_identical(src)

    def test_hoisted_local_is_unaffected(self):
        # A hoisted module-init LOCAL keeps the RECORD_HOISTED decl (it
        # declares its own `Box* s`), a different node entirely.
        src = PRELUDE + (
            "def main() -> None:\n"
            "    v = Box(2)\n"
            "    s: Box = v\n"
            "    print(v.n, s.n)\n"
            "main()\n"
        )
        top, w, fallback = _top_level(src)
        assert top is not None
        assert not fallback
        assert w.get("top_level.global_hoist_slot", 0) == 0
        _assert_byte_identical(src)
