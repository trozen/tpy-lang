"""Optional[container] at the ctor-arg and field-write seams, plus the
CONST twin of the storage-opt loop-var registration.

A ptr-repr `Optional[container]` param is a `T*`, so a container LITERAL at
that slot hoists a spelled temp and passes its address, and a NARROWED read
derefs before a plain container field copies it. The loop-var half is the
same duality one level down: iterating `list[P | None]` binds
`[const] std::optional<P>&`, and a lift-decl off it inherits the source's
const-ness.
"""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _reject_tally,
    _assert_byte_identical, _assert_routes_byte_identical, _compile, _entry,
    _lower_ctx_witnessed,
)


def _gen_witnessed(source: str):
    """Whole-module generation (bodies AND constructors) plus the face
    witnesses -- `_lower_ctx_witnessed` lowers function bodies only, so a
    ctor-only face reads as zero there."""
    compiler, modules = _compile(source)
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False))
    return hpp + cpp, dict(compiler._thir_face_witnesses)


class TestOptionalContainerCtorAndFieldWrite:
    _SRC = (
        "from tpy import Int32\n"
        "from typing import Optional\n"
        "class Holder:\n"
        "    items: list[Int32]\n"
        "    byk: dict[str, Int32]\n"
        "    def __init__(self, items: Optional[list[Int32]],\n"
        "                 byk: Optional[dict[str, Int32]]) -> None:\n"
        "        if items is not None:\n"
        "            self.items = items\n"
        "        else:\n"
        "            self.items = []\n"
        "        if byk is not None:\n"
        "            self.byk = byk\n"
        "        else:\n"
        "            self.byk = {}\n"
        "def main() -> None:\n"
        "    h1 = Holder([10, 20], {\"a\": 1})\n"
        "    h2 = Holder(None, None)\n"
        "    print(len(h1.items), len(h2.byk))\n"
        "main()\n"
    )

    def test_literal_hoists_at_the_decl_and_narrowed_read_derefs(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        out, wit = _gen_witnessed(self._SRC)
        assert wit.get("optptr.container_temp", 0) == 2
        assert wit.get("field_write.container_narrowed_optptr", 0) == 2
        # The DECL is a flush position, so the literal's temp lands ahead of
        # the record line (the arg itself is the address-of lift).
        assert ("std::vector<int32_t> __tmp_1 = "
                "std::vector<int32_t>{10, 20};") in out
        assert "Holder h1 = Holder(&(__tmp_1), &(__tmp_2));" in out
        assert "this->items = (*items);" in out
        assert "this->byk = (*byk);" in out

    def test_optional_container_field_keeps_rejecting(self):
        # BOUNDARY: an `Optional[container]` FIELD stores `std::optional<T>`
        # and needs the `ptr_to_optional` lift -- the deref copy would drop
        # the null case entirely.
        src = (
            "from tpy import Int32\n"
            "from typing import Optional\n"
            "class Holder:\n"
            "    n: Int32\n"
            "    opt: list[Int32] | None\n"
            "    def __init__(self, opt: Optional[list[Int32]]) -> None:\n"
            "        if opt is not None:\n"
            "            self.n = Int32(len(opt))\n"
            "        else:\n"
            "            self.n = 0\n"
            "        self.opt = opt\n"
            "def main() -> None:\n"
            "    h = Holder(None)\n"
            "    print(h.opt is None)\n"
            "main()\n"
        )
        fell = _reject_tally(src)
        assert "ctor:stmt.assign:assign.field_write_shape" in fell, fell


class TestConstStorageOptLoopVar:
    _SRC = (
        "from tpy import Int32, readonly\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "class Holder:\n"
        "    pairs: list[P | None]\n"
        "    def __init__(self) -> None:\n"
        "        self.pairs = [P(Int32(1)), None]\n"
        "    @readonly\n"
        "    def const_loop(self) -> Int32:\n"
        "        for it in self.pairs:\n"
        "            first = it\n"
        "            if first is not None:\n"
        "                return first.x\n"
        "        return Int32(-1)\n"
        "    def mutating_loop(self) -> Int32:\n"
        "        self.pairs.clear()\n"
        "        for it in self.pairs:\n"
        "            first = it\n"
        "            if first is not None:\n"
        "                return first.x\n"
        "        return Int32(-1)\n"
        "def main() -> None:\n"
        "    h = Holder()\n"
        "    print(h.const_loop(), h.mutating_loop())\n"
        "main()\n"
    )

    def test_const_and_mutable_loop_vars_split_their_lift(self):
        hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("foreach.storage_opt_const_elem", 0) == 1
        assert wit.get("decl.storage_opt_name_lift", 0) == 2
        out = hpp + cpp
        assert "const P* first = ::tpy::optional_to_ptr(it);" in out
        assert "P* first = ::tpy::optional_to_ptr(it);" in out

    def test_reassigned_target_reseats_through_the_lift(self):
        # BOUNDARY: the reseat is the SAME lift, never the bare pointer copy
        # a same-declared-type Optional NAME source would take -- a
        # storage-opt binding is `std::optional<P>`, not a `T*`.
        src = (
            "from tpy import Int32, readonly\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
            "class Holder:\n"
            "    pairs: list[P | None]\n"
            "    def __init__(self) -> None:\n"
            "        self.pairs = [P(Int32(1)), None]\n"
            "    @readonly\n"
            "    def probe(self) -> Int32:\n"
            "        first: P | None = None\n"
            "        for it in self.pairs:\n"
            "            first = it\n"
            "            if first is not None:\n"
            "                return first.x\n"
            "        return Int32(-1)\n"
            "def main() -> None:\n"
            "    print(Holder().probe())\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        _thir, wit = _lower_ctx_witnessed(src)
        assert wit.get("reseat.storage_opt_name_lift", 0) == 1
        out = hpp + cpp
        assert "first = ::tpy::optional_to_ptr(it);" in out
        assert "const P* first = nullptr;" in out


class TestOptionalContainerLiteralSlots:
    """A container LITERAL at the two `Optional[container]` STORAGE seams:
    the pointer-repr local slot (`T __slot_N = T{..}; T* x = &__slot_N;`) and
    the storage-form `std::optional<C>` field (`this->f = C{..};`).

    Both thread the container INNER (the AST's own Optional unwrap in
    `_gen_array_literal`) and self-describe a bare list brace -- neither
    `optional<C>`'s converting ctor nor a nested element target can deduce a
    type from `{10, 20}`. An EMPTY literal at either seam is a sema error
    (un-inferable element type), so no unit exists for it.
    """

    _SRC = (
        "from tpy import Int32, Array\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
        "class Bag:\n"
        "    li: list[Int32] | None\n"
        "    di: dict[str, Int32] | None\n"
        "    se: set[Int32] | None\n"
        "    nested: list[list[Int32]] | None\n"
        "    recs: list[P] | None\n"
        "    arr: Array[Int32, 3] | None\n"
        "    def __init__(self) -> None:\n"
        "        self.li = None\n"
        "        self.di = None\n"
        "        self.se = None\n"
        "        self.nested = None\n"
        "        self.recs = None\n"
        "        self.arr = None\n"
        "    def fill(self) -> None:\n"
        "        self.li = [1, 2]\n"
        "        self.di = {\"k\": 3}\n"
        "        self.se = {4, 5}\n"
        "        self.nested = [[1], [2, 3]]\n"
        "        self.recs = [P(7)]\n"
        "        self.arr = [1, 2, 3]\n"
        "def decls() -> None:\n"
        "    a: list[Int32] | None = [1, 2]\n"
        "    b: dict[str, Int32] | None = {\"k\": 3}\n"
        "    c: set[Int32] | None = {4, 5}\n"
        "    d: list[list[Int32]] | None = [[1], [2, 3]]\n"
        "    e: list[P] | None = [P(7)]\n"
        "    if a is not None and b is not None and c is not None:\n"
        "        print(len(a), len(b), len(c))\n"
        "    if d is not None and e is not None:\n"
        "        print(len(d), e[0].x)\n"
        "def main() -> None:\n"
        "    decls()\n"
        "    g = Bag()\n"
        "    g.fill()\n"
        "    if g.li is not None and g.arr is not None:\n"
        "        print(len(g.li), len(g.arr))\n"
        "main()\n"
    )

    def test_slot_and_field_literals_route(self):
        _assert_routes_byte_identical(self._SRC)
        out, wit = _gen_witnessed(self._SRC)
        assert wit.get("decl.opt_slot_container_literal", 0) == 5
        assert wit.get("field_write.opt_container_lit", 0) == 6
        # The slot line self-spells the list brace; dict/set literals already
        # spell their container ctor.
        assert ("std::vector<int32_t> __slot_1 = "
                "std::vector<int32_t>{1, 2};") in out
        assert "std::vector<int32_t>* a = &__slot_1;" in out
        assert ("::tpy::ordered_map<std::string, int32_t> __slot_2 = "
                "::tpy::ordered_map<std::string, int32_t>({{\"k\", 3}});") in out
        assert ("::tpy::ordered_set<int32_t> __slot_3 = "
                "::tpy::ordered_set<int32_t>({4, 5});") in out
        assert "this->li = std::vector<int32_t>{1, 2};" in out
        assert ("this->di = ::tpy::ordered_map<std::string, int32_t>"
                "({{\"k\", 3}});") in out
        assert "this->se = ::tpy::ordered_set<int32_t>({4, 5});" in out
        assert "this->recs = std::vector<P>{P(7)};" in out
        assert "this->arr = std::array<int32_t, 3>{1, 2, 3};" in out

    def test_nested_literal_threads_the_inner_not_the_optional(self):
        # The discriminator for threading the INNER: targeting the Optional
        # leaves the nested element with no container target and the brace
        # picks up an extra level (`{{{1}, {2, 3}}}`).
        out, _wit = _gen_witnessed(self._SRC)
        assert ("std::vector<std::vector<int32_t>> __slot_4 = "
                "std::vector<std::vector<int32_t>>{{1}, {2, 3}};") in out
        assert ("this->nested = std::vector<std::vector<int32_t>>"
                "{{1}, {2, 3}};") in out

    def test_literal_decl_with_rvalue_reseat_keeps_rejecting(self):
        # BOUNDARY: an rvalue-reassigned name owes a function-top
        # `std::optional<T>` rebind pre-decl and reseats through
        # `&*(__slot_N = ..)`; the slot render this arm emits would drop the
        # pre-decl. The METHOD-call reseat is itself routable, so this is a
        # real discriminator, not a shape masked by a later fallback.
        src = (
            "from tpy import Int32, Own\n"
            "class F:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "    def make(self) -> Own[list[Int32]]:\n"
            "        return [self.n, self.n]\n"
            "def go(f: F) -> None:\n"
            "    a: list[Int32] | None = [1, 2]\n"
            "    if a is not None:\n"
            "        print(len(a))\n"
            "    a = f.make()\n"
            "    if a is not None:\n"
            "        print(len(a))\n"
            "def main() -> None:\n"
            "    go(F(3))\n"
            "main()\n"
        )
        fell = _reject_tally(src)
        assert fell == {"body:stmt.var_decl:decl.opt_slot_source": 1}, fell
