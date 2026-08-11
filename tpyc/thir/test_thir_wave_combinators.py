"""Pins for the combinator admission rungs: range / iterator-call /
genexpr args at native Iterable slots and container instantiations --
plus the no-double-move and non-template-iterator boundaries."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _fn,
    _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = "from tpy import Int32\n"


class TestNativeLadderRungs:
    def test_range_arg_routes(self):
        src = _PRELUDE + (
            "def pair_up() -> None:\n"
            "    names = ['a', 'b', 'c']\n"
            "    for i, n in zip(range(3), names):\n"
            "        print(i, n)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pair_up") is not None
        assert faces["call.native_range_arg"] >= 1
        _assert_byte_identical(src)

    def test_nested_combinator_arg_routes(self):
        src = _PRELUDE + (
            "def double(v: Int32) -> Int32:\n"
            "    return v * 2\n"
            "def tag_pairs() -> None:\n"
            "    xs = [1, 2, 3]\n"
            "    ys = [4, 5, 6]\n"
            "    for x, y in zip(map(double, xs), ys):\n"
            "        print(x, y)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "tag_pairs") is not None
        assert faces["call.native_iter_call_arg"] >= 1
        _assert_byte_identical(src)

    def test_gen_factory_arg_routes(self):
        src = _PRELUDE + (
            "from typing import Iterator\n"
            "def gen() -> Iterator[Int32]:\n"
            "    yield 1\n"
            "    yield 2\n"
            "def doubled() -> None:\n"
            "    for v in map(lambda x: x * 2, gen()):\n"
            "        print(v)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["call.native_iter_call_arg"] >= 1
        _assert_byte_identical(src)

    def test_user_iterator_call_unpack_routes(self):
        # A plain user function returning an Iterator-conforming record now
        # rides the tuple-unpack head's iter-proto call leg (zip is a
        # generator call; the unpack targets are scalars).
        src = (
            "from tpy import Int32, Own\n"
            "class CountUp:\n"
            "    n: Int32\n"
            "    limit: Int32\n"
            "    def __init__(self, limit: Int32) -> None:\n"
            "        self.n = 0\n"
            "        self.limit = limit\n"
            "    def __iter__(self) -> 'CountUp':\n"
            "        return self\n"
            "    def __next__(self) -> Int32 | None:\n"
            "        if self.n >= self.limit:\n"
            "            return None\n"
            "        self.n = self.n + 1\n"
            "        return self.n\n"
            "def make_counter(limit: Int32) -> Own[CountUp]:\n"
            "    return CountUp(limit)\n"
            "def use() -> None:\n"
            "    for a, b in zip(make_counter(3), [10, 20, 30]):\n"
            "        print(a, b)\n"
        )
        assert _fn(_lower_ctx(src), "use") is not None
        _assert_byte_identical(src)


class TestInstantiationRungs:
    def test_combinator_instantiation_routes(self):
        src = _PRELUDE + (
            "def doubled() -> None:\n"
            "    nums = [1, 2, 3]\n"
            "    result = list(map(lambda x: x * 2, nums))\n"
            "    print(result)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "doubled") is not None
        assert faces["call.inst_iter_arg"] >= 1
        _assert_byte_identical(src)

    def test_last_use_nested_container_binds_bare(self):
        # nums' LAST USE sits nested in the map arg: the oracle binds it
        # bare (no own_iter / std::move double-move) -- byte-diff pins it.
        src = _PRELUDE + (
            "def f() -> None:\n"
            "    nums = [1, 2, 3]\n"
            "    print(list(map(lambda x: x + 1, nums)))\n"
        )
        _assert_byte_identical(src)

    def test_genexpr_instantiation_routes(self):
        src = _PRELUDE + (
            "def squares() -> None:\n"
            "    nums = [1, 2, 3]\n"
            "    result = list(x * x for x in nums)\n"
            "    print(result)\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert faces["call.inst_genexpr_arg"] >= 1
        _assert_byte_identical(src)
