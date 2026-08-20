"""The `Optional[container]` FIELD at its narrowed READ and its ctor WRITE.

One shape, two sites: `for k in self.d:` under `if self.d is not None:`
(which unwraps to `(*this->d)` and takes begin()/end() off the container
inside), and the member-init-list entry that seeds the field from a
container or str literal.
"""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_routes_byte_identical, _lower_ctx_witnessed,
    _thir_ctx, _thir_ctx_witnessed,
)


def _holder(payload: str, init: str, loop_var: str = "x",
            body: str = "n += 1") -> str:
    return ("from tpy import Int32\n"
            "class H:\n"
            f"    f: {payload} | None\n"
            "    def __init__(self) -> None:\n"
            f"        self.f = {init}\n"
            "    def total(self) -> Int32:\n"
            "        n = 0\n"
            "        if self.f is not None:\n"
            f"            for {loop_var} in self.f:\n"
            f"                {body}\n"
            "        return n\n"
            "def main() -> None:\n"
            "    print(H().total())\n"
            "main()\n")


class TestNarrowedOptContainerFieldForEach:
    def test_dict_field_routes_with_begin_end(self):
        src = _holder("dict[str, Int32]", "{\"a\": Int32(1)}", "k",
                      "n += len(k)")
        _, faces = _lower_ctx_witnessed(src)
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "auto& __obj_0 = (*this->f);" in out
        assert "__obj_0.begin();" in out

    def test_list_field_routes_with_single_unwrap(self):
        src = _holder("list[Int32]", "[Int32(1), Int32(2)]", "x", "n += x")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "auto& __obj_0 = (*this->f);" in out
        # Exactly ONE unwrap: a doubled deref would deref the container.
        assert "(*(*this->f))" not in out

    def test_set_field_routes(self):
        src = _holder("set[Int32]", "{Int32(1)}", "x", "n += x")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __obj_0 = (*this->f);" in hpp + cpp

    def test_sync_for_head_prechecks_the_field(self):
        # The sync route lowers its field iterable with field_prechecked, so
        # the read never reaches the result ladder -- the ROUTE's own family
        # test is the gate here, and the read row's face belongs to the
        # resumable seam (pinned there).
        src = _holder("list[Int32]", "[Int32(1)]", "x", "n += x")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert not faces.get("field.narrowed_opt_container_iterable")
        assert faces["mil.optional_container_literal"] >= 1

    def test_universal_iter_loop_does_not_claim_the_shape(self):
        # The ordering hazard this cell was built around: the iter-proto
        # route's FIELD leg has no is_native_iterable exclusion (unlike its
        # call sibling), so if the container route stops claiming the
        # narrowed Optional the proto route silently emits the universal
        # `::tpy::__iter__` loop instead of begin()/end() -- byte-different,
        # and invisible while the READ still rejects.
        src = _holder("dict[str, Int32]", "{\"a\": Int32(1)}", "k",
                      "n += len(k)")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "::tpy::__iter__" not in out
        assert "auto& __src_0" not in out

    def test_optional_pointer_receiver_routes(self):
        # The receiver `_field_decl_type` unwraps: a narrowed `H | None`
        # param renders `p->f`, and the field's own narrowing still owes
        # exactly one unwrap (`(*p->f)`).
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    f: list[Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1)]\n"
               "def g(p: H | None) -> Int32:\n"
               "    n = 0\n"
               "    if p is not None:\n"
               "        if p.f is not None:\n"
               "            for x in p.f:\n"
               "                n += x\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(g(H()))\n"
               "main()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "auto& __obj_0 = (*p->f);" in hpp + cpp


class TestNarrowedOptContainerFieldForEachBoundaries:
    def test_array_field_keeps_rejecting_at_the_read(self):
        # BOUNDARY: `Array` is outside the iterable row's family test (the
        # bare-container sibling excludes it too), so the for-head keeps
        # falling back -- while the MIL row below DOES claim its literal.
        src = ("from tpy import Int32, Array\n"
               "class H:\n"
               "    f: Array[Int32, 3] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1), Int32(2), Int32(3)]\n"
               "    def total(self) -> Int32:\n"
               "        n = 0\n"
               "        if self.f is not None:\n"
               "            for x in self.f:\n"
               "                n += x\n"
               "        return n\n"
               "def main() -> None:\n"
               "    print(H().total())\n"
               "main()\n")
        _ctx, fb = _thir_ctx(src)
        assert fb == {"body:stmt.for_each:field.result_type": 1}, fb
        _assert_byte_identical(src)

    def test_chain_receiver_keeps_rejecting(self):
        # BOUNDARY: the for-head route admits a plain record/self receiver
        # only. `_narrowed_opt_field_read` is receiver-shape-blind (a chain
        # read is legitimately narrowed), so the ROUTE's receiver gate is
        # what fences the chain -- and must keep doing so.
        src = ("from tpy import Int32\n"
               "class I:\n"
               "    f: list[Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1)]\n"
               "class O:\n"
               "    inner: I\n"
               "    def __init__(self) -> None:\n"
               "        self.inner = I()\n"
               "def g(o: O) -> Int32:\n"
               "    n = 0\n"
               "    if o.inner.f is not None:\n"
               "        for x in o.inner.f:\n"
               "            n += x\n"
               "    return n\n"
               "def main() -> None:\n"
               "    print(g(O()))\n"
               "main()\n")
        _ctx, fb = _thir_ctx(src)
        assert fb == {"body:stmt.for_each:foreach.field_parent": 1}, fb
        _assert_byte_identical(src)

    def test_borrow_bind_local_does_not_reach_the_row(self):
        # BOUNDARY: the row is ITERABLE-only. A local bound to the narrowed
        # field routes through the decl arm's own precheck, so admitting
        # BORROW_BIND here would ship an arm no position reaches.
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    f: list[Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1), Int32(2)]\n"
               "    def grow(self) -> Int32:\n"
               "        if self.f is not None:\n"
               "            xs = self.f\n"
               "            xs.append(Int32(9))\n"
               "        if self.f is not None:\n"
               "            return Int32(len(self.f))\n"
               "        return Int32(0)\n"
               "def main() -> None:\n"
               "    print(H().grow())\n"
               "main()\n")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert not faces.get("field.narrowed_opt_container_iterable")
        _assert_routes_byte_identical(src)


class TestNarrowedOptContainerFieldResumable:
    def test_generator_self_field_routes(self):
        # The resumable seam: the skeleton owns the narrowed unwrap
        # (`_maybe_unwrap_narrowed_optional` over the leaf), so the leaf
        # hands over the member read with its own unwrap stripped -- keeping
        # it would emit `(*(*__self.f))`.
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "class H:\n"
               "    f: list[Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1), Int32(2)]\n"
               "    def each(self) -> Iterator[Int32]:\n"
               "        if self.f is not None:\n"
               "            for x in self.f:\n"
               "                yield x\n"
               "                yield x\n"
               "def main() -> None:\n"
               "    print(sum(H().each()))\n"
               "main()\n")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert faces["res.for_narrowed_opt_field_src"] >= 1
        assert faces["field.narrowed_opt_container_iterable"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "((*__self.f)).begin()" in out
        assert "(*(*__self.f))" not in out

    def test_async_self_field_routes(self):
        src = ("import asyncio\n"
               "from tpy import Int32\n"
               "class H:\n"
               "    f: list[Int32] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1), Int32(2)]\n"
               "    async def total(self) -> Int32:\n"
               "        n = 0\n"
               "        if self.f is not None:\n"
               "            for x in self.f:\n"
               "                await asyncio.sleep(0)\n"
               "                n += x\n"
               "        return n\n"
               "def main() -> None:\n"
               "    print(asyncio.run(H().total()))\n"
               "main()\n")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert faces["res.for_narrowed_opt_field_src"] >= 1
        assert faces["field.narrowed_opt_container_iterable"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "((*__self.f)).begin()" in hpp + cpp


class TestOptionalFieldMilLiterals:
    def test_list_literal_self_describes(self):
        # The Optional target cannot deduce a bare brace-init, so the AST
        # prefixes the container spelling -- the non-Optional sibling emits
        # the bare `f({1, 2})`.
        src = _holder("list[Int32]", "[Int32(1), Int32(2)]", "x", "n += x")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "f(std::vector<int32_t>{1, 2})" in hpp + cpp

    def test_dict_and_set_literals_stay_bare(self):
        # dict/set literals already spell their own container type, so the
        # typed-brace prefix is ARRAY-literal-only (the emit's startswith
        # guard would drop it anyway; keeping the gate explicit mirrors the
        # AST, where only `_gen_array_literal` carries the Optional branch).
        for payload, init, cpp_frag in (
                ("dict[str, Int32]",
                 "{\"a\": Int32(1)}",
                 "f(::tpy::ordered_map<std::string, int32_t>("),
                ("set[Int32]", "{Int32(1)}",
                 "f(::tpy::ordered_set<int32_t>(")):
            src = _holder(payload, init, "k", "n += 1")
            hpp, cpp = _assert_routes_byte_identical(src)
            assert cpp_frag in hpp + cpp, payload

    def test_array_literal_routes_with_typed_brace(self):
        src = ("from tpy import Int32, Array\n"
               "class H:\n"
               "    f: Array[Int32, 3] | None\n"
               "    def __init__(self) -> None:\n"
               "        self.f = [Int32(1), Int32(2), Int32(3)]\n"
               "    def first(self) -> Int32:\n"
               "        if self.f is not None:\n"
               "            return self.f[0]\n"
               "        return Int32(0)\n"
               "def main() -> None:\n"
               "    print(H().first())\n"
               "main()\n")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert faces["mil.optional_container_literal"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "f(std::array<int32_t, 3>{1, 2, 3})" in hpp + cpp

    def test_str_literal_lands_bare(self):
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    s: str | None\n"
               "    def __init__(self) -> None:\n"
               "        self.s = \"xy\"\n"
               "    def size(self) -> Int32:\n"
               "        if self.s is not None:\n"
               "            return Int32(len(self.s))\n"
               "        return Int32(0)\n"
               "def main() -> None:\n"
               "    print(H().size())\n"
               "main()\n")
        _, faces, fb = _thir_ctx_witnessed(src)
        assert fb == {}, fb
        assert faces["mil.optional_str_literal"] >= 1
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "s(\"xy\")" in hpp + cpp


class TestOptionalFieldMilBoundaries:
    def test_bytes_literal_keeps_rejecting(self):
        # BOUNDARY: `_value_opt_view` covers str AND bytes, but an owned
        # bytes field needs the view->owned copy the bare literal render
        # omits -- so the str row is keyed on the str family alone.
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    p: bytes | None\n"
               "    def __init__(self) -> None:\n"
               "        self.p = b\"hi\"\n"
               "    def size(self) -> Int32:\n"
               "        if self.p is not None:\n"
               "            return Int32(len(self.p))\n"
               "        return Int32(0)\n"
               "def main() -> None:\n"
               "    print(H().size())\n"
               "main()\n")
        _ctx, fb = _thir_ctx(src)
        assert fb == {"ctor:ctor.mil_field.optional.bytesliteral": 1}, fb
        _assert_byte_identical(src)

    def test_strview_inner_keeps_rejecting(self):
        # BOUNDARY: a VIEW inner (`optional<string_view>`) takes the AST's
        # arg-split shim, not the bare literal.
        src = ("from tpy import StrView\n"
               "class H:\n"
               "    s: StrView | None\n"
               "    def __init__(self) -> None:\n"
               "        self.s = \"xy\"\n"
               "    def has(self) -> bool:\n"
               "        return self.s is not None\n"
               "def main() -> None:\n"
               "    print(H().has())\n"
               "main()\n")
        _ctx, fb = _thir_ctx(src)
        assert fb == {"ctor:ctor.mil_field.optional.strliteral": 1}, fb
        _assert_byte_identical(src)

    def test_bytearray_native_call_keeps_rejecting(self):
        # BOUNDARY: the container-literal row admits LITERAL sources only; a
        # `bytearray(...)` rvalue is a native call with its own render.
        src = ("from tpy import Int32\n"
               "class H:\n"
               "    b: bytearray | None\n"
               "    def __init__(self) -> None:\n"
               "        self.b = bytearray(b\"ab\")\n"
               "    def size(self) -> Int32:\n"
               "        if self.b is not None:\n"
               "            return Int32(len(self.b))\n"
               "        return Int32(0)\n"
               "def main() -> None:\n"
               "    print(H().size())\n"
               "main()\n")
        _ctx, fb = _thir_ctx(src)
        assert fb == {"ctor:ctor.mil_field.optional.native_call": 1}, fb
        _assert_byte_identical(src)
