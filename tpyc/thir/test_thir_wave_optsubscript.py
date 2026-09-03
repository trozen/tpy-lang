"""Storage-form Optional container-subscript consumers: the None-test
(`d["a"] is not None` -> has_value over the bare `__getitem__` read), the
runtime-checked field read (`d["a"].x` -> deref_optional_check), the
OPTIONAL_TO_PTR decl (`a = d["a"]` -> optional_to_ptr lift), and the
storage-opt LOOP VAR (`for v in d.values():` -- the for-statement producer
of the storage-opt set; `T*` slots lift via optional_to_ptr). Generator
sources (borrow-form yields) and narrowed loop-var reads stay AST."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)

_HDR = (
    "from tpy import Int32\n"
    "class P:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
)


class TestOptionalContainerSubscript:
    def test_subscript_none_test_and_checked_read_route(self):
        src = (_HDR +
               "def main() -> None:\n"
               "    d: dict[str, P | None] = {\"a\": P(Int32(10))}\n"
               "    if d[\"a\"] is not None:\n"
               "        print(d[\"a\"].x)\n"
               "    a = d[\"a\"]\n"
               "    if a is not None:\n"
               "        print(a.x)\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("isnone.subscript_storage", 0) >= 1
        assert faces.get("field.opt_check_subscript_recv", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert '(::tpy::__getitem__(d, "a").has_value())' in cpp[1]
        assert ('::tpy::deref_optional_check(::tpy::__getitem__(d, "a")).x'
                in cpp[1])
        assert ('P* a = ::tpy::optional_to_ptr(::tpy::__getitem__(d, "a"));'
                in cpp[1])

    def test_values_loop_var_registers_storage_opt(self):
        # The for-statement producer: the loop var reads bare and lifts
        # via optional_to_ptr at the `T*` param slot.
        src = (_HDR +
               "def borrow(p: P | None) -> Int32:\n"
               "    if p is None:\n"
               "        return -1\n"
               "    return p.x\n"
               "def main() -> None:\n"
               "    d: dict[str, P | None] = {\"a\": P(Int32(10)), \"b\": None}\n"
               "    for v in d.values():\n"
               "        print(borrow(v))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "main") is not None
        assert faces.get("foreach.storage_opt_elem", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "borrow(::tpy::optional_to_ptr(v))" in cpp[1]

    def test_narrowed_loop_var_read_routes(self):
        # CONVERTED (storage-opt narrow wave): a NARROWED member access off
        # the storage-opt loop var derefs at the ACCESS site -- `(*v).x` --
        # the same rows the comp loop var rides.
        src = (_HDR +
               "def main() -> None:\n"
               "    d: dict[str, P | None] = {\"a\": P(Int32(10))}\n"
               "    for v in d.values():\n"
               "        if v is not None:\n"
               "            print(v.x)\n"
               "main()\n")
        cpp = _assert_routes_byte_identical(src)
        assert "(*v).x" in cpp[1]

    def test_checked_subscript_field_write_stays_ast(self):
        # The WRITE side (`d["a"].x = 5`) is outside the read-only
        # container-subscript row and must keep falling back.
        src = (_HDR +
               "def main() -> None:\n"
               "    d: dict[str, P | None] = {\"a\": P(Int32(10))}\n"
               "    if d[\"a\"] is not None:\n"
               "        d[\"a\"].x = 5\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_readonly_view_iteration_registers_the_const_twin(self):
        # A CONST dict receiver's `.values()` view (`readonly[dict[..]]`
        # param) tracks the receiver's const-ness: the storage-opt
        # registration probes the view's RECEIVER and records the loop var in
        # the CONST twin, so consumers off it spell `const P*`.
        src = ("from tpy import Int32, readonly\n"
               + _HDR.replace("from tpy import Int32\n", "") +
               "def count(d: readonly[dict[str, P | None]]) -> Int32:\n"
               "    n = 0\n"
               "    for v in d.values():\n"
               "        n += 1\n"
               "    return n\n"
               "def main() -> None:\n"
               "    d: dict[str, P | None] = {\"a\": P(Int32(1))}\n"
               "    print(count(d))\n"
               "main()\n")
        from .testutil import _lower_ctx_witnessed
        thir, wit = _lower_ctx_witnessed(src)
        assert _fn(thir, "count") is not None
        assert wit.get("foreach.storage_opt_const_elem", 0) == 1
        _assert_byte_identical(src)

    def test_generator_optional_elem_stays_ast(self):
        # A generator yields the BORROW form (`P*`); the iter-proto route
        # keeps rejecting the optional element family.
        src = ("from typing import Iterator\n"
               + _HDR +
               "def gen() -> Iterator[P | None]:\n"
               "    yield P(1)\n"
               "    yield None\n"
               "def use(p: P | None) -> Int32:\n"
               "    if p is None:\n"
               "        return -1\n"
               "    return p.x\n"
               "def main() -> None:\n"
               "    for v in gen():\n"
               "        print(use(v))\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.for_each:foreach.elem_family.optional")
