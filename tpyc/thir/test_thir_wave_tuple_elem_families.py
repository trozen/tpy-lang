"""Pins for the five tuple-ELEMENT families this wave opened.

Each is a distinct C++ duality that `_element_is_pointer_repr` deliberately
excludes (a union borrows as a variant, a recursive wrapper as `X&` -- neither
is a bare `T*`), so none routes through tuple_to_pointer / tuple_to_storage
and each needs its own key:

  - UNION element (`tuple[Dog | Cat, Int32]`) -- borrow form spells the CONST
    ptr-variant, and the whole-tuple `tuple_value_to_borrow` is its only
    boundary conversion;
  - `Own[A | B]` element -- Own pushes the union to its VALUE variant, so the
    tuple is storage form and an unpack lifts PER ELEMENT via to_ptr_variant;
  - `Own[P] | None` element -- STORAGE `std::optional<P>`, read out by a plain
    value copy (never the opt_ptr pointer the `P | None` element takes);
  - wrapper-REFERENCE element (`tuple[Tree[Int32], Int32]`) -- a live `X&`
    member the unpack re-binds through unwrap_ref;
  - MIXED own+borrow tuple CALL results as a rebind source -- the hybrid
    render IS the local's form, so the sinks are plain assigns.

The boundary units hold the neighbours that must keep their own rows: an
ALL-VALUE union element (value-repr, not ptr-variant), the plain `P | None`
element (pointer-repr, opt_ptr target), an `Own[...]`-marked wrapper element
(storage form, moved out -- never the `X&` member), and a non-scalar sibling
element beside a wrapper (record / str), which keeps rejecting.
"""

from __future__ import annotations

from .testutil import (_assert_rejects_at, _assert_routes_byte_identical,
                       _lower_ctx_witnessed, _thir_ctx)

_PETS = (
    "from tpy import Int32, nocopy\n"
    "@nocopy\n"
    "class Dog:\n"
    "    bark: Int32\n"
    "    def __init__(self, b: Int32) -> None:\n"
    "        self.bark = b\n"
    "@nocopy\n"
    "class Cat:\n"
    "    meow: Int32\n"
    "    def __init__(self, m: Int32) -> None:\n"
    "        self.meow = m\n"
)


class TestUnionElementBorrowTuple:
    def test_read_arg_and_literal_route(self):
        src = _PETS + (
            "def read_second(pair: tuple[Dog | Cat, Int32]) -> Int32:\n"
            "    return pair[1]\n"
            "def passthrough(pair: tuple[Dog | Cat, Int32]) -> Int32:\n"
            "    return read_second(pair)\n"
            "def main() -> None:\n"
            "    print(read_second((Dog(7), 2)))\n"
            "    print(passthrough((Cat(9), 3)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        # The sibling VALUE slot reads bare -- the union element only
        # changes its OWN slot spelling.
        assert "return std::get<1>(pair);" in out
        assert "return read_second(pair);" in out
        # The literal arg: CONST ptr-variant destination, VALUE-variant
        # source; both spellings come from the union type itself.
        assert ("::tpy::tuple_value_to_borrow<std::tuple<std::variant<"
                "const Cat*, const Dog*>, int32_t>>(std::tuple<std::variant<"
                "Cat, Dog>, int32_t>{Dog(7), 2})") in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["subscript.union_elem_tuple"] >= 1
        assert faces["call.union_elem_tuple_arg"] >= 2
        assert faces["btuple.elem_ptr_union"] >= 1

    def test_value_union_element_is_not_this_family(self):
        # BOUNDARY: an ALL-VALUE union element is value-repr
        # (`is_ptr_variant_union` False), so it has no borrow/storage split
        # and keeps its own value-tuple rows -- the ptr-variant key must not
        # claim it.
        src = (
            "from tpy import Int32\n"
            "def read_second(pair: tuple[Int32 | float, Int32]) -> Int32:\n"
            "    return pair[1]\n"
            "def main() -> None:\n"
            "    print(read_second((Int32(7), 2)))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("subscript.union_elem_tuple")
        assert not faces.get("btuple.elem_ptr_union")


class TestOwnUnionElementTuple:
    _AB = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class B:\n"
        "    y: Int32\n"
        "    def __init__(self, y: Int32) -> None:\n"
        "        self.y = y\n"
    )

    def test_return_literal_and_unpack_lift_route(self):
        src = self._AB + (
            "def pair() -> tuple[Own[A | B], Int32]:\n"
            "    return (A(42), Int32(99))\n"
            "def borrow(u: A | B) -> Int32:\n"
            "    if isinstance(u, A):\n"
            "        return u.x\n"
            "    return Int32(0)\n"
            "def main() -> None:\n"
            "    p, n = pair()\n"
            "    print(borrow(p))\n"
            "    print(n)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        # Own pushes the element to the VALUE variant, so the return is the
        # spelled storage brace-init...
        assert ("return std::tuple<std::variant<A, B>, int32_t>{A(42), 99};"
                in out)
        # ... and the unpack lifts PER ELEMENT (not a whole-capture wrap).
        assert "auto __tup_1 = pair();" in out
        assert ("std::variant<A*, B*> p = ::tpy::to_ptr_variant("
                "std::get<0>(__tup_1));") in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["stmt.tuple_unpack.ptr_variant_target"] >= 1

    def test_value_union_element_unpack_keeps_rejecting(self):
        # BOUNDARY: the target bind is keyed on the PTR-VARIANT union. A
        # value-repr union element has no to_ptr_variant lift and stays on
        # its own (unrouted) rung.
        src = (
            "from tpy import Int32\n"
            "def remake() -> tuple[Int32 | float, Int32]:\n"
            "    return (Int32(1), Int32(2))\n"
            "def use() -> None:\n"
            "    a, b = remake()\n"
            "    print(b)\n"
            "use()\n"
        )
        _ctx, fell = _thir_ctx(src)
        assert fell == {"body:stmt.tuple_unpack": 1}, fell


class TestOwnOptionalStorageElementTuple:
    _P = (
        "from tpy import Int32, Own\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
    )

    def test_unpack_and_literal_arg_route(self):
        src = self._P + (
            "def take_opt(t: tuple[Own[P] | None, Own[P] | None]) -> Int32:\n"
            "    a, b = t\n"
            "    if a is not None:\n"
            "        return a.x\n"
            "    return Int32(0)\n"
            "def main() -> None:\n"
            "    a = P(7)\n"
            "    print(take_opt((a, None)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "const auto& __tup_1 = t;" in out
        # STORAGE element -> the plain value copy, and the RECORD-kind
        # registration gives the has_value test + `(*a)` deref.
        assert "std::optional<P> a = std::get<0>(__tup_1);" in out
        assert "return (*a).x;" in out
        # The literal arg COPIES the local: an `Own[P] | None` slot is the
        # REF capture mode, so the AST's slot_owned move never fires.
        assert ("take_opt(std::tuple<std::optional<P>, std::optional<P>>"
                "{a, std::nullopt})") in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["stmt.tuple_unpack.value_opt_record_target"] >= 1

    def test_plain_optional_record_element_keeps_the_pointer_target(self):
        # BOUNDARY: without the Own the element is POINTER-repr, so the
        # target is the nullable `const P*` opt_ptr bind off the borrow
        # tuple -- a different render entirely.
        src = self._P + (
            "def take_opt(t: tuple[P | None, P | None]) -> Int32:\n"
            "    a, b = t\n"
            "    if a is not None:\n"
            "        return a.x\n"
            "    return Int32(0)\n"
            "def main() -> None:\n"
            "    a = P(7)\n"
            "    print(take_opt((a, None)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "const P* a = std::get<0>(__tup_1);" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["stmt.tuple_unpack.opt_ptr_target"] >= 1
        assert not faces.get("stmt.tuple_unpack.value_opt_record_target")


class TestWrapperRefElementTuple:
    def test_call_source_and_unwrap_ref_target_route(self):
        src = (
            "from tpy import Int32\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "def count(t: Tree[Int32]) -> Int32:\n"
            "    match t:\n"
            "        case list() as branches:\n"
            "            n = 0\n"
            "            for c in branches:\n"
            "                n += count(c)\n"
            "            return n\n"
            "        case _:\n"
            "            return 1\n"
            "def pair(t: Tree[Int32]) -> tuple[Tree[Int32], Int32]:\n"
            "    return (t, 0)\n"
            "def main() -> None:\n"
            "    seed: Tree[Int32] = [1, [2, 3]]\n"
            "    a, n = pair(seed)\n"
            "    print(count(a) + n)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "auto __tup_1 = pair(seed);" in out
        # unwrap_ref -- NOT the `(*std::get..)` pointer deref, and not the
        # `auto&& = unwrap_ref(tuple_elem_ref(..))` borrow-tuple alias.
        assert ("Tree<int32_t>& a = ::tpy::unwrap_ref(std::get<0>(__tup_1));"
                in out)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["call.wrapper_ref_tuple_ret"] >= 1
        assert faces["stmt.tuple_unpack.unwrap_ref_target"] >= 1

    def test_own_wrapper_element_keeps_the_storage_family(self):
        # BOUNDARY: an `Own[...]`-marked wrapper element pushes the whole
        # tuple to STORAGE form -- a by-value `Tree<int32_t>` member moved
        # out of the capture, never the `Tree<int32_t>&` member the
        # reference row aliases. The own-storage rows own this shape.
        src = (
            "from tpy import Int32, Own\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "def count(t: Tree[Int32]) -> Int32:\n"
            "    match t:\n"
            "        case list() as branches:\n"
            "            n = 0\n"
            "            for c in branches:\n"
            "                n += count(c)\n"
            "            return n\n"
            "        case _:\n"
            "            return 1\n"
            "def pair() -> tuple[Own[Tree[Int32]], Int32]:\n"
            "    t: Tree[Int32] = [1, [2, 3]]\n"
            "    return (t, 0)\n"
            "def main() -> None:\n"
            "    a, n = pair()\n"
            "    print(count(a) + n)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert ("return std::tuple<Tree<int32_t>, int32_t>{std::move(t), 0};"
                in out)
        assert ("Tree<int32_t> a = std::move(std::get<0>(__tup_1));" in out)
        assert "unwrap_ref" not in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ret.tuple_own_elem"] >= 1
        assert faces["stmt.tuple_unpack.own_target"] >= 1
        assert not faces.get("ret.wrapper_ref_tuple")
        assert not faces.get("call.wrapper_ref_tuple_ret")
        assert not faces.get("stmt.tuple_unpack.unwrap_ref_target")

    def test_non_scalar_sibling_element_keeps_rejecting(self):
        # BOUNDARY: every non-wrapper element must be a value SCALAR. A
        # record element (borrow form `Dog*`, a pointer -- not the wrapper's
        # `Tree&`) and a str element (view form) each render a slot the
        # reference-tuple rows do not spell, so both keep rejecting. Dropping
        # the scalar guard admits the str shape outright and makes the record
        # shape build an arg temp under a non-flushable position.
        pre = (
            "from tpy import Int32\n"
            "type Tree[T] = T | list[Tree[T]]\n"
        )
        record_src = pre + (
            "class Dog:\n"
            "    bark: Int32\n"
            "    def __init__(self, b: Int32) -> None:\n"
            "        self.bark = b\n"
            "def pair(t: Tree[Int32], d: Dog) -> tuple[Tree[Int32], Dog]:\n"
            "    return (t, d)\n"
            "def main() -> None:\n"
            "    seed: Tree[Int32] = [1, [2, 3]]\n"
            "    a, b = pair(seed, Dog(3))\n"
            "    print(b.bark)\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(record_src)
        _assert_rejects_at(fell, "body:stmt.return", "return.slot_type")
        _assert_rejects_at(fell, "body:expr.call", "call.ret_type.tuple")
        str_src = pre + (
            "def pair(t: Tree[Int32], s: str) -> tuple[Tree[Int32], str]:\n"
            "    return (t, s)\n"
            "def main() -> None:\n"
            "    seed: Tree[Int32] = [1, [2, 3]]\n"
            "    a, b = pair(seed, \"hi\")\n"
            "    print(b)\n"
            "main()\n"
        )
        _ctx, fell = _thir_ctx(str_src)
        assert fell == {"body:stmt.return:return.slot_type": 1,
                        "body:stmt.tuple_unpack": 1}, fell


class TestMixedOwnTupleCallSources:
    _BOX = (
        "from tpy import Int32, Own\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
        "    return (Box(1), b)\n"
    )

    def test_reseat_is_a_plain_assign(self):
        src = self._BOX + (
            "def rebind(b: Box, c: Box) -> Int32:\n"
            "    p = make_mixed(b)\n"
            "    p[1].val = 11\n"
            "    p = make_mixed(c)\n"
            "    return p[0].val\n"
            "def main() -> None:\n"
            "    print(rebind(Box(7), Box(8)))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::tuple<Box, Box*> p = make_mixed(b);" in out
        # No lift, no rebind slot -- the hybrid render IS the local's form.
        assert "p = make_mixed(c);" in out
        assert "std::get<1>(p)->val = 11;" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["btuple.reseat_mixed_call"] >= 1

    def test_if_hoist_predecls_and_keeps_the_element_arrow(self):
        src = self._BOX + (
            "def branch_hoisted(b: Box, c: Box, pick: bool) -> Int32:\n"
            "    if pick:\n"
            "        p = make_mixed(b)\n"
            "    else:\n"
            "        p = make_mixed(c)\n"
            "    p[1].val = 33\n"
            "    return p[0].val\n"
            "def main() -> None:\n"
            "    print(branch_hoisted(Box(7), Box(8), True))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "std::tuple<Box, Box*> p;" in out
        assert "p = make_mixed(b);" in out
        # The predecl carries the registration the decl arm would have made,
        # so the borrowed element still reads through `->`.
        assert "std::get<1>(p)->val = 33;" in out
        assert "return std::get<0>(p).val;" in out
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["if.hoist_mixed_own_tuple"] >= 1

    def test_owning_call_source_keeps_the_emplace(self):
        # BOUNDARY: an ALL-Own tuple return is STORAGE form, so its reseat
        # still goes through the rebind slot's emplace -- the mixed row must
        # not capture it. `_renders_own_borrow_tuple` reads `is_mixed_own`
        # off the callee's declared return, which is the discriminator.
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, val: Int32) -> None:\n"
            "        self.val = val\n"
            "def make_pair(n: Int32) -> tuple[Own[Box], Own[Box]]:\n"
            "    return (Box(n), Box(n + 1))\n"
            "def rebind(flag: bool) -> Int32:\n"
            "    if flag:\n"
            "        t = make_pair(9)\n"
            "    else:\n"
            "        t = make_pair(3)\n"
            "    return t[0].val\n"
            "def main() -> None:\n"
            "    print(rebind(True))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("btuple.reseat_mixed_call")
        assert not faces.get("if.hoist_mixed_own_tuple")


class TestGenericOpenElementSlots:
    def test_ctor_open_slot_and_tuple_copy_route(self):
        src = (
            "from tpy import Own, copy\n"
            "class Bare[U]:\n"
            "    x: U\n"
            "    def __init__(self, x: U) -> None:\n"
            "        self.x = x\n"
            "class Pair[A, B]:\n"
            "    p: tuple[A, B]\n"
            "    def __init__(self, p: Own[tuple[A, B]]) -> None:\n"
            "        self.p = p\n"
            "def use_bare[T](src: list[T]) -> Own[Bare[T]]:\n"
            "    b = Bare(src[0])\n"
            "    return b\n"
            "def use_pair[T](src: list[tuple[T, int]]) -> Own[Pair[T, int]]:\n"
            "    p = Pair(copy(src[0]))\n"
            "    return p\n"
            "def main() -> None:\n"
            "    nums: list[int] = [7, 8]\n"
            "    print(use_bare(nums).x)\n"
            "    rows: list[tuple[int, int]] = [(1, 2)]\n"
            "    pair = use_pair(rows)\n"
            "    print(pair.p[0])\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        # The bare element read binds the still-open `const U&` ctor slot.
        assert "Bare<T> b = Bare<T>(::tpy::__getitem__(src, 0));" in out
        # The generic copy tail spelled off the whole TUPLE slot.
        assert ("Pair<T, ::tpy::BigInt> p = Pair<T, ::tpy::BigInt>("
                "std::tuple<T, ::tpy::BigInt>(::tpy::__getitem__(src, 0)));"
                in out)
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces["ctor.generic_open_slot_elem"] >= 1
        assert faces["ctor.copy_open_tuple_elem"] >= 1
        assert faces["subscript.open_tuple_elem"] >= 1
