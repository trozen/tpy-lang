"""The for-head ref-target long-tail rows: container-VALUE dict views
(`for k, v in d.items()` over `dict[str, list[Int32]]`), the opt_ptr
unpack targets at the for head (`for x, y in items:` over ptr-Optional
tuples -- the standalone bind mirrored), the FIELD iterable unpack head,
the container-returning method-call iterable, and the view-call const
recursion (`d.items()` tracks its receiver's const verdict, incl. the
inferred-@readonly method's self)."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestRefTargetLongTail:
    def test_container_value_items_routes_const(self):
        # A readonly dict param's items(): the ref target aliases the
        # value; the head lift spells const element pointers.
        src = ("from tpy import Int32, readonly\n"
               "def total(d: readonly[dict[str, list[Int32]]]) -> Int32:\n"
               "    n = 0\n"
               "    for k, v in d.items():\n"
               "        n = n + len(v)\n"
               "    return n\n"
               "def main() -> None:\n"
               "    d: dict[str, list[Int32]] = {}\n"
               "    d[\"a\"] = [1, 2]\n"
               "    print(total(d))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "total") is not None
        cpp = _assert_byte_identical(src)
        assert ("tuple_to_pointer<std::tuple<std::string_view, "
                "const std::vector<int32_t>*>>" in cpp[1])

    def test_container_method_unpack_routes(self):
        # A container-returning METHOD call as the unpack iterable: the
        # call renders inside the `__obj_N` rvalue capture, value-tuple
        # elements bind plain.
        src = ("from tpy import Int32, Own\n"
               "class C:\n"
               "    counts: dict[str, Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.counts = {\"a\": 2, \"b\": 1}\n"
               "    def top(self) -> Own[list[tuple[str, Int32]]]:\n"
               "        out: list[tuple[str, Int32]] = []\n"
               "        for k, n in self.counts.items():\n"
               "            out.append((k, n))\n"
               "        return out\n"
               "def f(c: C) -> Int32:\n"
               "    total = 0\n"
               "    for k, n in c.top():\n"
               "        total = total + n\n"
               "    return total\n"
               "def main() -> None:\n"
               "    print(f(C()))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["method.container_iterable"] >= 1
        cpp = _assert_byte_identical(src)
        assert "auto __obj_0 = c.top();" in cpp[1]

    def test_fresh_container_method_const_receiver_yields_mutable(self):
        # The const-recursion boundary: a method minting a FRESH container
        # off a const-inferred receiver does not alias it, so the elements
        # bind mutable (`P*`, not `const P*`) -- only a borrowing-view
        # return tracks the receiver's const verdict.
        src = ("from tpy import Int32, Own\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32):\n"
               "        self.x = x\n"
               "class M:\n"
               "    items: list[P]\n"
               "    def __init__(self) -> None:\n"
               "        self.items = [P(1)]\n"
               "    def snapshot(self) -> Own[list[P]]:\n"
               "        out: list[P] = []\n"
               "        for p in self.items:\n"
               "            out.append(P(p.x))\n"
               "        return out\n"
               "def f(m: M) -> None:\n"
               "    for p in m.snapshot():\n"
               "        p.x = p.x + 1\n"
               "        print(p.x)\n"
               "def main() -> None:\n"
               "    f(M())\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _assert_byte_identical(src)
        assert "const P*" not in cpp[1]

    def test_mutable_view_iteration_stays_mutable(self):
        # The inverse: a mutable dict param's items() spells non-const
        # pointers and the mutation reaches the dict.
        src = ("from tpy import Int32\n"
               "class P:\n"
               "    x: Int32\n"
               "    def __init__(self, x: Int32):\n"
               "        self.x = x\n"
               "def bump(d: dict[str, P]) -> None:\n"
               "    for k, v in d.items():\n"
               "        v.x = v.x + 1\n"
               "def main() -> None:\n"
               "    d: dict[str, P] = {\"a\": P(1)}\n"
               "    bump(d)\n"
               "    print(d[\"a\"].x)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "bump") is not None
        _assert_byte_identical(src)


class TestFieldReceiverItemsUnpack:
    """`for k, v in self.<field>.items():` -- the FIELD-receiver flavor of
    the tuple-unpack for head. The same loop over a NAME binding already
    routed; the view render is receiver-blind (`::tpy::dict_items(
    this->meta)`) and the unpack targets bind off the lifted borrow tuple,
    not off a receiver-keyed registration. Corpus witness:
    `tplib/json_model_nested`."""

    _SRC = (
        "from tpy import Int32\n"
        "class Bag:\n"
        "    meta: dict[str, Int32]\n"
        "    lists: dict[str, list[Int32]]\n"
        "    def __init__(self) -> None:\n"
        "        self.meta = {}\n"
        "        self.lists = {}\n"
        "    def sum_meta(self) -> Int32:\n"
        "        total = 0\n"
        "        for k, v in self.meta.items():\n"
        "            total += v + Int32(len(k))\n"
        "        return total\n"
        "    def sum_lists(self) -> Int32:\n"
        "        total = 0\n"
        "        for k, xs in self.lists.items():\n"
        "            total += Int32(len(k)) + Int32(len(xs))\n"
        "        return total\n"
        "def main() -> None:\n"
        "    b = Bag()\n"
        "    print(b.sum_meta())\n"
        "    print(b.sum_lists())\n"
        "main()\n"
    )

    def test_scalar_and_ref_target_field_items_route(self):
        out = "".join(_assert_routes_byte_identical(self._SRC))
        assert "::tpy::dict_items(this->meta)" in out
        # The container-VALUE target still takes the ref-target unpack off
        # the lifted borrow tuple -- the field receiver changes only where
        # the view comes from.
        assert ("::tpy::tuple_to_pointer<std::tuple<std::string_view, "
                "const std::vector<int32_t>*>>") in out
        assert "::tpy::unwrap_ref(::tpy::tuple_elem_ref(" in out

    _SINGLE_SRC = (
        "from tpy import Int32\n"
        "class Bag2:\n"
        "    meta: dict[str, Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.meta = {}\n"
        "    def total(self) -> Int32:\n"
        "        n = 0\n"
        "        for v in self.meta.values():\n"
        "            n += v\n"
        "        return n\n"
        "def main() -> None:\n"
        "    print(Bag2().total())\n"
        "main()\n"
    )

    def test_single_var_field_view_head_routes(self):
        # The SINGLE-VAR for head takes the same field receiver: the
        # loop-var storage registration keys on the ITERABLE TYPE, not on
        # the receiver's node kind, so a field view registers exactly as a
        # name view does.
        out = "".join(_assert_routes_byte_identical(self._SINGLE_SRC))
        assert "::tpy::dict_values(this->meta)" in out

    _CHAIN_SRC = (
        "from tpy import Int32\n"
        "class Inner:\n"
        "    meta: dict[str, Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.meta = {}\n"
        "class Bag3:\n"
        "    inner: Inner\n"
        "    def __init__(self) -> None:\n"
        "        self.inner = Inner()\n"
        "    def total(self) -> Int32:\n"
        "        n = 0\n"
        "        for v in self.inner.meta.values():\n"
        "            n += v\n"
        "        return n\n"
        "def main() -> None:\n"
        "    print(Bag3().total())\n"
        "main()\n"
    )

    def test_two_level_field_chain_receiver_stays_ast(self):
        # BOUNDARY: the view row admits a ONE-level field read; a chained
        # receiver is a different render family and keeps rejecting.
        from .testutil import _assert_rejects_at, _thir_ctx
        _ctx, fell = _thir_ctx(self._CHAIN_SRC)
        _assert_rejects_at(fell, "body:stmt.for_each",
                           shape="iter.method_call_shape")
        _assert_byte_identical(self._CHAIN_SRC)
