"""Branch-nested binds inside a resumable leaf: a name backed by NO frame
member is a true C++ block local and re-enters the sync ladder; every
frame-resident name keeps its member-assign arms."""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _codegen_facts(source: str):
    """(face witnesses, fallback tally) from a full THIR codegen run -- the
    resumable leaf pass only runs there, so `lower_module` alone never
    witnesses these faces."""
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True,
                               comment_line_numbers=False, thir_codegen=True))
    return compiler._thir_face_witnesses, compiler._thir_fallback


# A while loop nested inside a for loop keeps `end` off the frame: the AST
# renders `int32_t end = k;` at the BB top level and a bare `end = ...;`
# reassign inside the branch.
BLOCK_LOCAL = """
from tpy import Int32
from typing import Iterator


def src(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        yield i
        i = i + 1


def gen(n: Int32) -> Iterator[Int32]:
    for c in src(n):
        k = c
        while k >= 0:
            end = k
            if end > 2:
                end = end - 1
            yield end
            k = k - 2


def main() -> None:
    for v in gen(4):
        print(v)


main()
"""


def test_branch_reassign_of_block_local_routes():
    faces, fallback = _codegen_facts(BLOCK_LOCAL)
    assert fallback == {}
    assert faces.get("res.branch_block_local", 0) >= 1


def test_branch_reassign_of_block_local_byte_identical():
    _hpp, cpp = _assert_routes_byte_identical(BLOCK_LOCAL)
    assert "int32_t end = k;" in cpp
    assert "end = (::tpy::sub_check<int32_t>(end, 1));" in cpp


# The same reassign on a name the frame DOES back: `k` is a generator local,
# so its branch write is the member assign, not a decl.
FRAME_BACKED = """
from tpy import Int32
from typing import Iterator


def src(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        yield i
        i = i + 1


def gen(n: Int32) -> Iterator[Int32]:
    for c in src(n):
        k = c
        while k >= 0:
            if k > 2:
                k = k - 1
            yield k
            k = k - 2


def main() -> None:
    for v in gen(4):
        print(v)


main()
"""


def test_branch_reassign_of_frame_field_keeps_member_assign():
    faces, fallback = _codegen_facts(FRAME_BACKED)
    assert fallback == {}
    assert faces.get("res.branch_frame_write", 0) >= 1
    assert faces.get("res.branch_block_local", 0) == 0
    _assert_routes_byte_identical(FRAME_BACKED)


# A str block local: the delegation is family-blind, so the sync decl arm owns
# the `std::string` slot exactly as it does in a sync body.
STR_BLOCK_LOCAL = """
from typing import Iterator
from tpy import Int32


def src(n: Int32) -> Iterator[Int32]:
    i: Int32 = 0
    while i < n:
        yield i
        i = i + 1


def gen(n: Int32) -> Iterator[str]:
    for c in src(n):
        k = c
        while k >= 0:
            tag = "even"
            if k % 2 == 1:
                tag = "odd"
            yield tag
            k = k - 2


def main() -> None:
    for v in gen(3):
        print(v)


main()
"""


def test_str_block_local_branch_reassign_routes():
    faces, fallback = _codegen_facts(STR_BLOCK_LOCAL)
    assert fallback == {}
    assert faces.get("res.branch_block_local", 0) >= 1
    _assert_routes_byte_identical(STR_BLOCK_LOCAL)


# The async twin: the frame is shape-neutral, so the same delegation holds for
# a coroutine's leaf branch.
ASYNC_BLOCK_LOCAL = """
import asyncio
from tpy import Int32


async def step(x: Int32) -> Int32:
    return x + 1


async def run(n: Int32) -> Int32:
    total: Int32 = 0
    for i in range(n):
        k = i
        while k >= 0:
            end = k
            if end > 1:
                end = end - 1
            total = total + await step(end)
            k = k - 2
    return total


def main() -> None:
    print(asyncio.run(run(3)))


main()
"""


def test_async_leaf_branch_block_local_routes():
    faces, _fallback = _codegen_facts(ASYNC_BLOCK_LOCAL)
    assert faces.get("res.branch_block_local", 0) >= 1
    _assert_routes_byte_identical(ASYNC_BLOCK_LOCAL)


# Boundary: a concrete-coro handle IS a frame member (an `optional<__coro_*>`
# slot the skeleton emplaces), so its branch-nested bind must never take the
# block-local delegation -- a sync decl there would shadow the slot.
CORO_HANDLE_BRANCH = """
import asyncio
from tpy import Int32


async def step(x: Int32) -> Int32:
    return x + 1


async def run(flag: bool) -> Int32:
    c = step(0)
    if flag:
        c = step(1)
    return await c


def main() -> None:
    print(asyncio.run(run(True)))


main()
"""


def test_branch_coro_handle_bind_stays_ast():
    _faces, fallback = _codegen_facts(CORO_HANDLE_BRANCH)
    _assert_rejects_at(fallback, "resumable:res.leaf_field_write")
