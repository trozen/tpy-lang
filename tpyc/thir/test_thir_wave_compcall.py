"""Container-returning CALL iterables in comprehensions: the free-call and
method-call (module-qualified) twins of the for-each fallback arms -- the
call renders inside the `__obj_N` capture, elements bind through the shared
loop-var classifier. Iterator-protocol combinator results (no container
return) keep rejecting."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestCompCallIterable:
    def test_free_call_iterable_routes(self):
        src = ("from tpy import Int32, Own\n"
               "def make_list() -> Own[list[Int32]]:\n"
               "    return [1, 2, 3]\n"
               "def main() -> None:\n"
               "    xs = [x * 2 for x in make_list()]\n"
               "    print(xs)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "= make_list();" in cpp[1]

    def test_method_call_iterable_routes(self):
        # A borrow-returning container METHOD call: `auto&` capture.
        src = ("from tpy import Int32\n"
               "class Holder:\n"
               "    xs: list[Int32]\n"
               "    def __init__(self) -> None:\n"
               "        self.xs = [1, 2]\n"
               "    def get(self) -> list[Int32]:\n"
               "        return self.xs\n"
               "def main() -> None:\n"
               "    h = Holder()\n"
               "    ys = [x + 1 for x in h.get()]\n"
               "    print(ys)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_routes_byte_identical(src)
        assert "= h.get();" in cpp[1]

    def test_iterator_combinator_source_stays_ast(self):
        # An Iterator-protocol combinator result (`islice(count(), 4)`) is
        # no container return -- the comp route keeps rejecting.
        src = ("import itertools\n"
               "def main() -> None:\n"
               "    print([x for x in itertools.islice(itertools.count(), 4)])\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)

    def test_single_var_items_view_stays_out_of_call_arm(self):
        # `for v in d.items()` single-var: the dict-view gate (items is
        # tuple-unpack-only) rejects, and the container-return fallback
        # must not swallow it -- the items view is no container return.
        src = ("from tpy import Int32\n"
               "def main() -> None:\n"
               "    d: dict[str, Int32] = {\"a\": 1}\n"
               "    xs = [kv for kv in d.items()]\n"
               "    print(len(xs))\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "main") is None
        _assert_byte_identical(src)
