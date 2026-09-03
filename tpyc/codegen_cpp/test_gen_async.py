"""Resumable-frame skeleton units.

The sub-future field of an inline `await` names the deduced capture type of
the argument the emplace passes to the sub-coro constructor. Field and emplace
are emitted by different passes over different scopes, so the only thing
keeping them in agreement is that both render the argument the same way -- a
divergence there is ill-formed C++, and no snapshot in the corpus carries a
shape where the two renders could differ.
"""
from __future__ import annotations

import re

from ..compiler import Compiler
from .. import get_lib_dir
from .context import CodeGenOptions


_SUB_FIELD = re.compile(r"await_arg_capture_t<decltype\(\((.*)\)\)>>> __sub_0;")
_EMPLACE = re.compile(r"__sub_0\.emplace\((.*)\);")


def _capture_and_emplace(source: str) -> tuple[str, str]:
    """The awaiting frame's sub-future capture spelling and the argument its
    emplace passes.

    Scoped to the `driver` frame: every fixture also awaits `asyncio.sleep`
    inside the callee, whose own frame carries a `__sub_0` of its own.
    """
    compiler = Compiler.from_source(
        source, lib_dirs=[get_lib_dir() / "tpy"], default_int="Int32")
    modules = compiler.compile()
    entry = [m for m in modules if m.is_entry_point][0]
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False))
    field = _SUB_FIELD.search(hpp, hpp.index("struct __coro_driver"))
    emplace = _EMPLACE.search(cpp, cpp.index("__coro_driver::__poll__"))
    assert field is not None, f"no protocol-templated sub-future field:\n{hpp}"
    assert emplace is not None, f"no sub-future emplace:\n{cpp}"
    return field.group(1), emplace.group(1)


_NARROWED_OPTIONAL = '''
import asyncio
from typing import Iterable, Optional
from tpy import Int32, Own, nocopy


class CounterIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration


@nocopy
class Counter:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)


async def consume(it: Iterable[Int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def driver(maybe: Optional[Counter]) -> None:
    if maybe is not None:
        await consume(maybe)
'''


_PLAIN_PROTOCOL_ARG = '''
import asyncio
from typing import Protocol
from tpy import Int32, nocopy


class Sink(Protocol):
    def emit(self, v: Int32) -> None: ...


@nocopy
class Printer:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def emit(self, v: Int32) -> None:
        print(self.n, v)


async def consume(s: Sink) -> None:
    await asyncio.sleep(0)
    s.emit(1)


async def driver() -> None:
    p = Printer(7)
    await consume(p)
'''


_ROUTED_PROTOCOL_ARG = '''
import asyncio
from typing import Iterable
from tpy import Int32


async def consume(it: Iterable[Int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def driver(xs: list[Int32]) -> None:
    await consume(xs)
'''


def test_routed_frame_captures_from_its_own_lowered_argument() -> None:
    """A routed body renders its emplace arguments off its lowered nodes, so
    the capture type has to come from the same author -- otherwise the two
    spellings agree only as long as nothing about the argument distinguishes
    the paths.

    Asserting the RENDER is what makes this able to fail: the two paths emit
    identical C++ here, so byte-identity alone is satisfied whichever author
    supplied the field.
    """
    from ..thir.emit import ResumableLeafEmitter
    from ..thir.testutil import _assert_routes_byte_identical

    _assert_routes_byte_identical(_ROUTED_PROTOCOL_ARG, comments=False)

    seen: list[list[str]] = []
    original = ResumableLeafEmitter.render_await_args

    def spy(self, call):
        rendered = original(self, call)
        seen.append(rendered)
        return rendered

    compiler = Compiler.from_source(
        _ROUTED_PROTOCOL_ARG, lib_dirs=[get_lib_dir() / "tpy"],
        default_int="Int32")
    modules = compiler.compile()
    entry = [m for m in modules if m.is_entry_point][0]
    ResumableLeafEmitter.render_await_args = spy
    try:
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
    finally:
        ResumableLeafEmitter.render_await_args = original
    # Exactly twice: the capture type at struct emit, then the emplace.
    assert seen == [["xs"], ["xs"]], seen


def test_narrowed_optional_arg_capture_matches_the_emplace() -> None:
    """A sema-narrowed pointer-repr Optional argument: the field and the
    emplace are emitted by different passes over different scopes, so the
    capture type has to be deduced from the same spelling the emplace passes
    -- a divergence there is ill-formed C++."""
    capture, emplace = _capture_and_emplace(_NARROWED_OPTIONAL)
    assert capture == emplace


def test_borrowed_protocol_arg_captures_the_lvalue() -> None:
    """The boundary the two rows above must not swallow: a plain (borrowing)
    protocol parameter takes the argument as an lvalue, so the capture stays
    a reference and the coroutine borrows rather than owning a copy."""
    capture, emplace = _capture_and_emplace(_PLAIN_PROTOCOL_ARG)
    assert capture == emplace == "(*p)"
