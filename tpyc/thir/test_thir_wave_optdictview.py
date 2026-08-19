"""A PROVEN-narrowed pointer-repr `Optional[container]` NAME under a
dict-view call (`for v in items.values():` at `dict | None`) unwraps to its
inner before the view's element predicates -- the AST derefs the pointer
local bare (`::tpy::dict_values((*items))`) into the same view render. Plus
the protocol ladder's pointer-repr Optional arg row (`s.total(d)` -> `&(d)`),
the record ladder's row on the protocol family."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)


class TestNarrowedOptDictViewIter:
    def test_values_routes_and_witnesses(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> Int32:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        for v in d.values():\n"
               "            n += v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}), f(None))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces["iter.narrowed_opt_container_view"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto __obj_0 = ::tpy::dict_values((*d));" in hpp + cpp
        # The bare deref is the whole render: no null fence, no address-of,
        # no move.
        assert "deref_check" not in hpp + cpp

    def test_keys_routes(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> int:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        for k in d.keys():\n"
               "            n += len(k)\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}), f(None))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_items_tuple_unpack_routes(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> int:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        for k, v in d.items():\n"
               "            n += v + len(k)\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}), f(None))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_comprehension_source_routes(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> int:\n"
               "    if d is not None:\n"
               "        xs = [v for v in d.values()]\n"
               "        return len(xs)\n"
               "    return 0\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}), f(None))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_record_values_mutated_through_loop_var_routes(self):
        # Alias-sensitive: the view aliases the pointee, so the mutation is
        # visible to the caller on both paths.
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n"
               "        self.x = x\n"
               "def f(d: dict[str, P] | None) -> Int32:\n"
               "    n = 0\n"
               "    if d is not None:\n"
               "        for p in d.values():\n"
               "            p.x += 1\n"
               "            n += p.x\n"
               "    return n\n"
               "def main() -> None:\n"
               "    m = {\"a\": P(1)}\n"
               "    print(f(m), m[\"a\"].x)\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_assert_narrowing_routes(self):
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> Int32:\n"
               "    assert d is not None\n"
               "    n = 0\n"
               "    for v in d.values():\n"
               "        n += v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_unproven_receiver_stays_ast(self):
        # `needs_optional_runtime_check` is the fence: the AST's unproven
        # flavor takes the deref_check method face, a different render.
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32] | None) -> Int32:\n"
               "    n = 0\n"
               "    for v in d.values():\n"
               "        n += v\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(f({\"a\": Int32(1)}))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_field_receiver_stays_ast(self):
        # The FIELD-receiver flavor is off at the for-head callers (its
        # loop-var registration is name-receiver-keyed); the unwrap rides
        # the NAME leg only.
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    m: dict[str, Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.m = {\"a\": Int32(1)}\n"
               "    def total(self) -> Int32:\n"
               "        n = 0\n"
               "        if self.m is not None:\n"
               "            for v in self.m.values():\n"
               "                n += v\n"
               "        return n\n"
               "def main() -> None:\n"
               "    print(H().total())\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "total") is None
        _assert_byte_identical(src)


class TestProtocolMethodOptionalPtrArg:
    def test_dyn_protocol_ptr_opt_arg_routes(self):
        src = ("from typing import Protocol\n"
               "from tpy import dynamic, Int32\n"
               "from tplib import Box\n"
               "@dynamic\n"
               "class S(Protocol):\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32: ...\n"
               "class C(S):\n"
               "    base: Int32\n"
               "    def __init__(self, b: Int32) -> None:\n"
               "        self.base = b\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32:\n"
               "        return self.base\n"
               "def use(s: Box[S], d: dict[str, Int32]) -> Int32:\n"
               "    return s.get().total(d)\n"
               "def main() -> None:\n"
               "    print(use(Box(C(1)), {\"a\": Int32(2)}))\n"
               "main()\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces["method.protocol_optional_ptr"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "total(&(d))" in hpp + cpp

    def test_none_literal_arg_routes(self):
        src = ("from typing import Protocol\n"
               "from tpy import dynamic, Int32\n"
               "from tplib import Box\n"
               "@dynamic\n"
               "class S(Protocol):\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32: ...\n"
               "class C(S):\n"
               "    base: Int32\n"
               "    def __init__(self, b: Int32) -> None:\n"
               "        self.base = b\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32:\n"
               "        return self.base\n"
               "def use(s: Box[S]) -> Int32:\n"
               "    return s.get().total(None)\n"
               "def main() -> None:\n"
               "    print(use(Box(C(1))))\n"
               "main()\n")
        _assert_routes_byte_identical(src)

    def test_structural_protocol_ptr_opt_arg_routes(self):
        src = ("from typing import Protocol\n"
               "from tpy import Int32\n"
               "class S(Protocol):\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32: ...\n"
               "class C:\n"
               "    base: Int32\n"
               "    def __init__(self, b: Int32) -> None:\n"
               "        self.base = b\n"
               "    def total(self, items: dict[str, Int32] | None = None"
               ") -> Int32:\n"
               "        return self.base\n"
               "def use(s: S, d: dict[str, Int32]) -> Int32:\n"
               "    return s.total(d)\n"
               "def main() -> None:\n"
               "    print(use(C(1), {\"a\": Int32(2)}))\n"
               "main()\n")
        _assert_routes_byte_identical(src)
