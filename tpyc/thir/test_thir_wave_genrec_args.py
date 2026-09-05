"""Genrec track cell B: the call/ctor arg rows and the decl converting-ctor
rows rekeyed on `_wrapper_union_like`.

- `_ru_wrapper_arg_slot` admits the generic instance (Ref-peeled: unlike
  the non-generic wrapper, `Tree[int]` is not a value type so its param
  slot arrives Ref-wrapped); members read via `_wrapper_like_members`.
- The generic-call arg tail gains the wrapper rows (`leaf_count(t)` at a
  `Tree[T]` slot resolved `Tree[int]`: a same-wrapper NAME binds bare).
- A scalar/str literal at a genrec slot hoists the typed temp in calls
  (`eat(5)` -> `Tree<::tpy::BigInt> __tmp_N = 5;`) and takes the plain
  spelled copy at decls (`leaf: Tree[int] = 9`).
- The ru-literal decl rows now REGISTER the declared binding (the missed
  `declared[name]` that kept every later use rejecting).
"""

from .testutil import (
    _assert_rejects_at, _assert_routes_byte_identical,
                      _reject_tally)

_TREE = (
    "from tpy import Int32\n"
    "type Tree[T] = T | list[Tree[T]]\n"
)


class TestGenrecArgAndDeclRows:
    SRC = (
        _TREE
        + "def leaf_count[T](t: Tree[T]) -> Int32:\n"
        + "    match t:\n"
        + "        case list() as branches:\n"
        + "            total = 0\n"
        + "            for child in branches:\n"
        + "                total += leaf_count(child)\n"
        + "            return total\n"
        + "        case _:\n"
        + "            return 1\n"
        + "def eat(t: Tree[int]) -> Int32:\n"
        + "    return 1\n"
        + "def main() -> None:\n"
        + "    t: Tree[int] = [1, [2, 3], 4]\n"
        + "    print(leaf_count(t))\n"
        + "    leaf: Tree[int] = 9\n"
        + "    print(leaf_count(leaf))\n"
        + "    print(eat(5))\n"
        + "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        # The genrec NAME binds the instantiated generic call bare...
        assert "leaf_count<::tpy::BigInt>(t)" in cpp
        # ...the literal decl takes the plain spelled copy...
        assert "Tree<::tpy::BigInt> leaf = 9;" in cpp
        # ...and the literal ARG hoists the typed wrapper temp.
        assert "Tree<::tpy::BigInt> __tmp_1 = 5;" in cpp
        assert "eat(__tmp_1)" in cpp


class TestGenrecCallRvalueArg:
    # CONVERTED FENCE (the argtemp.ru_wrapper_call row): an Own[genrec]-
    # returning free call at the same-wrapper slot hoists the typed temp
    # (`Tree<int32_t> __tmp_N = make_leaf();`) and passes the bare name --
    # the AST's _gen_union_arg hoist, admitted at the gate + the STORAGE
    # result family (call.genrec_own_ret).
    SRC = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def make_leaf() -> Own[Tree[Int32]]:\n"
        "    return Int32(7)\n"
        "def eat(t: Tree[Int32]) -> Int32:\n"
        "    return 1\n"
        "def main() -> None:\n"
        "    print(eat(make_leaf()))\n"
        "main()\n"
    )

    def test_call_rvalue_arg_hoists_temp(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "Tree<int32_t> __tmp_1 = make_leaf();" in cpp
        assert "eat(__tmp_1)" in cpp

    def test_method_call_source_hoists_temp(self):
        # CONVERTED FENCE (the protocol_method wave): an Own[genrec]-
        # returning METHOD call rides the same argtemp hoist -- the
        # record-receiver flavor here, the protocol-receiver flavor via
        # the corpus case (its result gate row is
        # method.protocol_genrec_storage_ret).
        src = (
            "from tpy import Int32, Own\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "class Factory:\n"
            "    def __init__(self) -> None:\n"
            "        pass\n"
            "    def make(self) -> Own[Tree[Int32]]:\n"
            "        return Int32(7)\n"
            "def eat(t: Tree[Int32]) -> Int32:\n"
            "    return 1\n"
            "def main() -> None:\n"
            "    f = Factory()\n"
            "    print(eat(f.make()))\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "Tree<int32_t> __tmp_1 = f.make();" in cpp
        assert "eat(__tmp_1)" in cpp


class TestNonGenericOwnCallArg:
    # The NON-generic wrapper flavor (`eat(make_json())` on a plain
    # recursive union) at a MUTABLE slot: the call result is already the
    # expanded UnionType (already_union), so both paths take the default
    # bare render -- the _ru_wrapper_value_call_arg row (formerly a fence:
    # before that row the body fell back byte-identically).
    SRC = (
        "from tpy import Int32, Own\n"
        "type Json = None | bool | Int32 | str | list[Json]\n"
        "def make_json() -> Own[Json]:\n"
        "    return Int32(3)\n"
        "def eat(j: Json) -> Int32:\n"
        "    return 2\n"
        "def main() -> None:\n"
        "    print(eat(make_json()))\n"
        "main()\n"
    )

    def test_nongeneric_own_call_arg_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self.SRC)
        _thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("arg.ru_wrapper_value_call", 0) >= 1
        assert "eat(make_json())" in cpp


class TestGenrecComparePair:
    # The same-instance wrapper compare pair (`a == b` on two `Tree[int]`
    # locals): the wrapper struct's own operator, bare render.
    SRC = (
        "from tpy import Int32\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def main() -> None:\n"
        "    a: Tree[int] = [1, 2]\n"
        "    b: Tree[int] = [1, 2]\n"
        "    print(a == b)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert "(a == b)" in cpp

    def test_nongeneric_wrapper_pair_stays_ast(self):
        # BOUNDARY: the NON-generic wrapper pair has no corpus witness --
        # the arm keys RecursiveAliasInstanceType only.
        src = (
            "from tpy import Int32\n"
            "type Json = None | bool | Int32 | str | list[Json]\n"
            "def main() -> None:\n"
            "    a: Json = Int32(1)\n"
            "    b: Json = Int32(1)\n"
            "    print(a == b)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")


class TestGenrecOwnTupleRows:
    # The Own[genrec] tuple slots: a literal element at the RETURN slot
    # takes the ru-instance spelled render inside the spelled tuple
    # brace-init; the standalone unpack captures the call rvalue and moves
    # the genrec element out.
    SRC = (
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "def make_pair() -> tuple[Own[Tree[Int32]], Int32]:\n"
        "    return ([1, 2], 0)\n"
        "def main() -> None:\n"
        "    t, n = make_pair()\n"
        "    print(n)\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        assert ("return std::tuple<Tree<int32_t>, int32_t>"
                "{std::vector<Tree<int32_t>>{1, 2}, 0};") in cpp
        assert "auto __tup_1 = make_pair();" in cpp
        assert "Tree<int32_t> t = std::move(std::get<0>(__tup_1));" in cpp

    def test_own_tuple_decl_slot_stays_ast(self):
        # BOUNDARY: the Own-genrec-tuple LOCAL decl slot has no bare-copy
        # read arm (the TODO-43 fence) -- keeps rejecting.
        src = (
            "from tpy import Int32, Own\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "def make_pair() -> tuple[Own[Tree[Int32]], Int32]:\n"
            "    return ([1, 2], 0)\n"
            "def main() -> None:\n"
            "    pair = make_pair()\n"
            "    print(1)\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")


class TestProtocolMethodGenrecRows:
    # The structural-protocol receiver lane's genrec rows, pinned directly
    # (not only via the corpus flip): the wrapper NAME arg row
    # (`s.absorb(t)`), the Own[genrec] STORAGE result row
    # (`method.protocol_genrec_storage_ret`), and the method-source
    # argtemp hoist off the protocol receiver.
    SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, Own\n"
        "type Tree[T] = T | list[Tree[T]]\n"
        "class TreeSink(Protocol):\n"
        "    def absorb(self, t: Tree[Int32]) -> Int32: ...\n"
        "    def sprout(self) -> Own[Tree[Int32]]: ...\n"
        "class Counter:\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def absorb(self, t: Tree[Int32]) -> Int32:\n"
        "        return 1\n"
        "    def sprout(self) -> Own[Tree[Int32]]:\n"
        "        return Int32(5)\n"
        "def leaf_count(t: Tree[Int32]) -> Int32:\n"
        "    return 1\n"
        "def use(s: TreeSink, t: Tree[Int32]) -> Int32:\n"
        "    return s.absorb(t) + leaf_count(s.sprout())\n"
        "def main() -> None:\n"
        "    c = Counter()\n"
        "    seed: Tree[Int32] = [1, 2]\n"
        "    print(use(c, seed))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        from .testutil import _compile, _entry
        from ..codegen_cpp import CodeGenOptions
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        out = hpp + cpp
        assert "Tree<int32_t> __tmp_1 = s.sprout();" in out
        assert "s.absorb(t)" in out
        assert "leaf_count(__tmp_1)" in out
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert compiler._thir_face_witnesses.get(
            "method.protocol_genrec_storage_ret")

    def test_name_elem_at_own_tuple_return_routes(self):
        # A NAME source at the Own[genrec] tuple-return element moves in
        # (the movable-local last use) -- converted from the cell's dualgen
        # probe.
        src = (
            "from tpy import Int32, Own\n"
            "type Tree[T] = T | list[Tree[T]]\n"
            "def name_elem() -> tuple[Own[Tree[Int32]], Int32]:\n"
            "    t: Tree[Int32] = [3, 4]\n"
            "    return (t, 1)\n"
            "def main() -> None:\n"
            "    tree, n = name_elem()\n"
            "    print(n)\n"
            "main()\n"
        )
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "std::move(t)" in cpp


class TestGenrecDictViewIteration:
    # The genrec-element dict-view iteration (`for v in d.values():` over
    # `dict[K, DictTree[K, V]]` -- `_container_genrec_elem` + the open-K
    # key admission, the view render being key/element-family-blind).
    SRC = (
        "from tpy import Int32\n"
        "type DictTree[K, V] = V | dict[K, DictTree[K, V]]\n"
        "def leaf_count[K, V](t: DictTree[K, V]) -> Int32:\n"
        "    match t:\n"
        "        case dict() as d:\n"
        "            acc = 0\n"
        "            for v in d.values():\n"
        "                acc += leaf_count(v)\n"
        "            return acc\n"
        "        case _:\n"
        "            return 1\n"
        "def main() -> None:\n"
        "    t: DictTree[str, Int32] = {\"a\": 1, \"b\": {\"c\": 2}}\n"
        "    print(leaf_count(t))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC, comments=False)
        out = hpp + cpp
        assert "auto __obj_0 = ::tpy::dict_values(d);" in out
        assert "auto&& v = *__beg_0;" in out

    def test_items_unpack_stays_ast(self):
        # BOUNDARY: an items() unpack whose targets are genrec-typed rides
        # the tuple-unpack REF-target branch -- unmirrored, falls back.
        src = (
            "from tpy import Int32\n"
            "type DictTree[K, V] = V | dict[K, DictTree[K, V]]\n"
            "def pairs[K, V](t: DictTree[K, V]) -> Int32:\n"
            "    match t:\n"
            "        case dict() as d:\n"
            "            n = 0\n"
            "            for _k, _v in d.items():\n"
            "                n += 1\n"
            "            return n\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    t: DictTree[str, Int32] = {\"a\": 1}\n"
            "    print(pairs(t))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:tuple.ref_target")


class TestGenrecDictViewBoundaries:
    # The open-K key admission is scoped to the genrec-ELEMENT predicate:
    # keys() over the same genrec-value dict routes (the key loop var is
    # the open K), while an open-K dict with a PLAIN open-V value stays
    # out of this family's admission entirely.
    def test_keys_over_open_k_routes(self):
        src = (
            "from tpy import Int32\n"
            "type DictTree[K, V] = V | dict[K, DictTree[K, V]]\n"
            "def count_keys[K, V](t: DictTree[K, V]) -> Int32:\n"
            "    match t:\n"
            "        case dict() as d:\n"
            "            n = 0\n"
            "            for _k in d.keys():\n"
            "                n += 1\n"
            "            return n\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    t: DictTree[str, Int32] = {\"a\": 1}\n"
            "    print(count_keys(t))\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        assert "::tpy::dict_keys(d)" in hpp + cpp

    def test_plain_open_value_dict_view_stays_ast(self):
        # BOUNDARY: `d.values()` over `dict[K, V]` with a PLAIN open V --
        # the genrec elem predicate rejects (elem not a genrec instance),
        # and no other family admits the open pair; the body falls back.
        src = (
            "from tpy import Int32\n"
            "def count_vals[K, V](d: dict[K, V]) -> Int32:\n"
            "    n = 0\n"
            "    for _v in d.values():\n"
            "        n += 1\n"
            "    return n\n"
            "def main() -> None:\n"
            "    print(count_vals({\"a\": 1}))\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:iter.method_call_shape")


class TestGenericOwnListLiteralArg:
    """A list literal at a substituted `Own[list[T]]` slot renders the
    prvalue brace INLINE (`make_bag<int32_t>({10, 20, 30})`) -- the owning
    by-value param moves the rvalue straight in, no ref-slot temp."""

    _SRC = (
        "from tpy import Own, Int32, nocopy\n"
        "@nocopy\n"
        "class Holder[T]:\n"
        "    _items: list[T]\n"
        "    def __init__(self, items: Own[list[T]]) -> None:\n"
        "        self._items = items\n"
        "def make[T](items: Own[list[T]]) -> Own[Holder[T]]:\n"
        "    return Holder[T](items)\n"
        "def main() -> None:\n"
        "    h = make([1, 2, 3])\n"
        "    print(len(h._items))\n"
        "main()\n")

    def test_own_list_literal_arg_routes(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("call.generic_own_list_literal", 0) >= 1
        assert "make<int32_t>({1, 2, 3})" in (_hpp + cpp)

    def test_ref_slot_literal_still_hoists_temp(self):
        # The NON-Own sibling (`take_ref([7, 8])` at `list[T]`) keeps the
        # ref-slot temp row (`std::vector<int32_t> __tmp_N = {7, 8};`) --
        # the new inline row is Own-slot-keyed.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32\n"
            "def take_ref[T](items: list[T]) -> Int32:\n"
            "    return len(items)\n"
            "def main() -> None:\n"
            "    print(take_ref([7, 8]))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "__tmp_1 = {7, 8};" in cpp


class TestGenericShadowingBuiltinName:
    """A LOCAL generic generator shadowing a builtin name spells the bare
    explicit-targ call (`enumerate<std::string>(words)`) -- the generic
    kind row mirrors the tail's conditional-qualification refinement
    (reject only an fi genuinely living in the imported module)."""

    def test_local_generic_shadow_routes(self):
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32\n"
            "from typing import Iterable, Iterator\n"
            "def enumerate[T](iterable: Iterable[T])"
            " -> Iterator[tuple[Int32, T]]:\n"
            "    i: Int32 = 0\n"
            "    for item in iterable:\n"
            "        yield (i, item)\n"
            "        i += 1\n"
            "def main() -> None:\n"
            "    words = [\"x\", \"y\"]\n"
            "    for i, w in enumerate(words):\n"
            "        print(i, w)\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "enumerate<std::string>(words)" in cpp

    def test_own_dict_literal_arg_still_defers(self):
        # The inline row is LIST-keyed: a dict literal at a substituted
        # Own[dict[...]] slot keeps the AST path.
        from .testutil import _fn, _lower_ctx
        src = (
            "from tpy import Own, Int32, nocopy\n"
            "@nocopy\n"
            "class DHolder[T]:\n"
            "    _m: dict[str, T]\n"
            "    def __init__(self, m: Own[dict[str, T]]) -> None:\n"
            "        self._m = m\n"
            "def dmake[T](m: Own[dict[str, T]]) -> Own[DHolder[T]]:\n"
            "    return DHolder[T](m)\n"
            "def main() -> None:\n"
            "    h = dmake({\"a\": 1})\n"
            "    print(len(h._m))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is None


class TestOpenTSetReceiver:
    """`s.add(v)` on `set[T]` in a generic body: the open-T element joins
    the set-receiver family (the list family's open-T admission mirrored);
    the T-typed arg binds bare."""

    def test_open_t_set_add_routes(self):
        from .testutil import _lower_ctx, _fn as _fn_l
        src = (
            "def add_to_set[T](s: set[T], v: T) -> None:\n"
            "    s.add(v)\n")
        thir = _lower_ctx(src)
        assert _fn_l(thir, "add_to_set") is not None


class TestGenericForwardTypeParam:
    """Explicit-targ generic forwards inside generic bodies: the
    borrow-container call passthrough at the T& return
    (`return identity<std::vector<T>>(items);`) and the same-T field read
    at the open T slot (`identity<T>(this->val)`)."""

    _SRC = (
        "from tpy import Int32\n"
        "def identity[T](x: T) -> T:\n"
        "    return x\n"
        "def wrap_list[T](items: list[T]) -> list[T]:\n"
        "    return identity[list[T]](items)\n"
        "class Holder[T]:\n"
        "    val: T\n"
        "    def __init__(self, v: T) -> None:\n"
        "        self.val = v\n"
        "    def forward_val(self) -> T:\n"
        "        return identity[T](self.val)\n"
        "def main() -> None:\n"
        "    xs = [1, 2]\n"
        "    print(wrap_list(xs))\n"
        "    h = Holder(7)\n"
        "    print(h.forward_val())\n"
        "main()\n")

    def test_forward_shapes_route(self):
        from .testutil import (_assert_routes_byte_identical,
                               _lower_ctx_witnessed)
        _hpp, cpp = _assert_routes_byte_identical(self._SRC)
        _thir, wit = _lower_ctx_witnessed(self._SRC)
        assert wit.get("ret.record_call_borrow", 0) >= 1
        assert wit.get("call.generic_open_slot_field", 0) >= 1
        joined = _hpp + cpp
        assert "return identity<std::vector<T>>(items);" in joined
        assert "return identity<T>(this->val);" in joined


class TestCtorMilNullableProtocolUnionArg:
    """The Pool[T] shape: a generic-record ctor whose MIL init is an
    explicit-type-arg generic ctor (`ArrayList[T, 8](items)`) taking an
    `Own[list[T]]` PARAM name at a nullable protocol-union slot
    (`Spannable[T] | Iterable[Own[T]] | None`) -- the `&(items)` addr
    lift over the bare name (`_items(ArrayList<T, 8>(&(items)))`)."""

    def test_own_list_param_addr_lift_routes(self):
        from .testutil import _lower_ctor, _ctor_tail, _lower_ctx
        src = (
            "from tpy import Int32, Own\n"
            "from tplib.array_list import ArrayList\n"
            "class Pool[T]:\n"
            "    _items: ArrayList[T, 8]\n"
            "    def __init__(self, items: Own[list[T]]) -> None:\n"
            "        self._items = ArrayList[T, 8](items)\n"
            "def main() -> None:\n"
            "    nums: list[Int32] = [1, 2]\n"
            "    p = Pool[Int32](nums)\n"
            "    print(len(p._items))\n"
            "main()\n")
        ctor = _lower_ctor(src, "Pool")
        assert ctor is not None
        assert "&(items)" in _ctor_tail(ctor)
        _assert_routes_byte_identical(src)

    def test_free_decl_position_routes(self):
        # The addr lift serves the free decl position too (`al =
        # ArrayList[Int32, 8](items)` off an Own[list] param) -- the
        # verdict is nullability-keyed, never a move.
        from .testutil import _lower_ctx, _fn
        src = (
            "from tpy import Int32, Own\n"
            "from tplib.array_list import ArrayList\n"
            "def load(items: Own[list[Int32]]) -> Int32:\n"
            "    al = ArrayList[Int32, 8](items)\n"
            "    return len(al)\n"
            "def main() -> None:\n"
            "    nums: list[Int32] = [1, 2, 3]\n"
            "    print(load(nums))\n"
            "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "load") is not None
        _assert_routes_byte_identical(src)

    def test_own_list_param_general_read_after_the_lift_routes(self):
        # The lift takes the param's ADDRESS, not its value, so a later bare
        # `len(items)` is a plain read of a still-live binding -- both paths
        # render it bare off the by-value param.
        from .testutil import _assert_routes_byte_identical
        src = (
            "from tpy import Int32, Own\n"
            "from tplib.array_list import ArrayList\n"
            "def load(items: Own[list[Int32]]) -> Int32:\n"
            "    al = ArrayList[Int32, 8](items)\n"
            "    return len(al) + len(items)\n"
            "def main() -> None:\n"
            "    nums: list[Int32] = [1, 2, 3]\n"
            "    print(load(nums))\n"
            "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "::tpy::__len__(items)" in cpp
