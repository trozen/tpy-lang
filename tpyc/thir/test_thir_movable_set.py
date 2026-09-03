"""The movable WORKING set: built at the decl arms that promote, never seeded
wholesale from sema's raw owned-locals fact.

`analyzer.function_movable_locals` means "sema proved this local owned"; the
set the move sites read means "owned AND declared by an arm that promotes".
Conflating them moves a value-typed local (a view-promoted `str`, a BigInt)
and a ptr-variant alias where the value must be copied.
"""

from __future__ import annotations

from .lower.context import _LowerCtx
from .testutil import _assert_byte_identical, _compile, _entry


def _cpp(src: str) -> str:
    _hpp, cpp = _assert_byte_identical(src, comments=False)
    return cpp


# A view-promoted `str` local: sema marks it owned, the tier-1 decl arm skips
# it (value-typed), so its last-use read into an owned sink COPIES.
_VALUE_STR = (
    "from tpy import Own\n"
    "def in_list() -> Own[list[str]]:\n"
    "    label: str = 'no'\n"
    "    return [label]\n"
    "def main() -> None:\n"
    "    print(in_list())\n"
    "main()\n"
)


class TestWorkingSetSeed:
    def test_locals_are_not_seeded_at_construction(self):
        """The raw sema fact must not reach the working set up front -- the
        whole defect class is one being mistaken for the other."""
        _compiler, modules = _compile(_VALUE_STR)
        entry = _entry(modules)
        fn = next(f for f in entry.ast.functions if f.name == "in_list")
        lc = _LowerCtx(fn, entry.analyzer, None)
        assert "label" in lc.sema_movable_locals, (
            "fixture no longer exercises a sema-owned local")
        assert "label" not in lc.movable_locals, (
            "sema's raw owned-locals fact leaked into the working set")

    def test_promote_movable_is_the_only_door(self):
        _compiler, modules = _compile(_VALUE_STR)
        entry = _entry(modules)
        fn = next(f for f in entry.ast.functions if f.name == "in_list")
        lc = _LowerCtx(fn, entry.analyzer, None)
        lc.promote_movable("label")
        assert "label" in lc.movable_locals
        # A name sema never proved owned stays out, whatever the arm asks.
        lc.promote_movable("not_a_local")
        assert "not_a_local" not in lc.movable_locals


# The Own-slot move row (`_own_move_source_slice`) must not re-derive
# movability from the payload TYPE: a VALUE-typed payload (Int32) reaching the
# working set via the await-bind promotion, passed as a bare last-use NAME into
# an `Own[T]` element slot, is movable. A `not is_value_type()` filter at the
# consumer renders `push_back(x)` and drops the move.
_OWN_SLOT_VALUE = (
    "import asyncio\n"
    "from tpy import Int32\n"
    "async def get_one() -> Int32:\n"
    "    return 7\n"
    "async def collect(out: list[Int32]) -> None:\n"
    "    x = await get_one()\n"
    "    out.append(x)\n"
    "def main() -> None:\n"
    "    xs: list[Int32] = []\n"
    "    asyncio.run(collect(xs))\n"
    "    print(xs[0])\n"
    "main()\n"
)

# The over-trigger guard at the same site: a name read AGAIN after the slot is
# not a last use, so it must copy however movable it is.
_OWN_SLOT_VALUE_REUSED = (
    "import asyncio\n"
    "from tpy import Int32\n"
    "async def get_one() -> Int32:\n"
    "    return 7\n"
    "async def collect(out: list[Int32]) -> Int32:\n"
    "    x = await get_one()\n"
    "    out.append(x)\n"
    "    return x\n"
    "def main() -> None:\n"
    "    xs: list[Int32] = []\n"
    "    print(asyncio.run(collect(xs)))\n"
    "main()\n"
)


class TestOwnSlotValuePayload:
    """A value-typed payload at an `Own[T]` slot moves when it is movable --
    movability is decided by the working set, never re-derived from the
    payload type at the consumer."""

    def test_value_payload_moves_at_an_own_slot(self):
        assert "out.push_back(std::move(x));" in _cpp(_OWN_SLOT_VALUE)

    def test_a_non_last_use_still_copies(self):
        cpp = _cpp(_OWN_SLOT_VALUE_REUSED)
        assert "out.push_back(x);" in cpp
        assert "out.push_back(std::move(x));" not in cpp


# The element-sink split. A value-typed payload promoted into the working set
# (here a BigInt frame local) renders DIFFERENTLY at the two sinks:
#   container literal -> `make_vector<BigInt>(std::move(n))`
#   tuple literal     -> `{n, ...}`, bare
# One shared helper serves both, so the rule keys on the SINK the caller
# represents (`tuple_elem`), never on the payload type. Collapsing them either
# way diverges one side.
_SINK_CONTAINER = (
    "from tpy import Own\n"
    "import asyncio\n"
    "async def one() -> int:\n"
    "    return 1\n"
    "async def collect(k: int) -> Own[list[int]]:\n"
    "    n = 0\n"
    "    while n < k:\n"
    "        n = n + await one()\n"
    "    return [n]\n"
    "def main() -> None:\n"
    "    print(asyncio.run(collect(3)))\n"
    "main()\n"
)

_SINK_TUPLE = (
    "import asyncio\n"
    "async def one() -> int:\n"
    "    return 1\n"
    "async def pair() -> tuple[int, int]:\n"
    "    n = 0\n"
    "    while n < 3:\n"
    "        n = n + await one()\n"
    "    return (n, 2)\n"
    "def main() -> None:\n"
    "    print(asyncio.run(pair()))\n"
    "main()\n"
)

# The dict half of the container rule: a distinct call path (keys lower
# through `_lower_container_elem` directly, values through the checked
# wrapper) with its own `make_ordered_map` decision.
_SINK_DICT = (
    "from tpy import Own\n"
    "import asyncio\n"
    "async def one() -> int:\n"
    "    return 1\n"
    "async def build() -> Own[dict[int, int]]:\n"
    "    n = 0\n"
    "    while n < 3:\n"
    "        n = n + await one()\n"
    "    return {n: 2}\n"
    "def main() -> None:\n"
    "    print(asyncio.run(build()))\n"
    "main()\n"
)


class TestElementSinkSplit:
    """The two element sinks disagree on a value-typed movable payload, and
    the shared helper must ask which sink it is serving."""

    def test_container_literal_moves_the_value_payload(self):
        assert "make_vector<::tpy::BigInt>(std::move(n))" in _cpp(
            _SINK_CONTAINER)

    def test_dict_literal_moves_the_value_payload_key(self):
        assert "make_ordered_map<::tpy::BigInt, ::tpy::BigInt>(std::move(n)" \
            in _cpp(_SINK_DICT)

    def test_tuple_literal_renders_the_value_payload_bare(self):
        cpp = _cpp(_SINK_TUPLE)
        assert "{n, ::tpy::BigInt(2)}" in cpp
        assert "std::move(n)" not in cpp
