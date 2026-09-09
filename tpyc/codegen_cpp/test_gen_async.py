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
from pathlib import Path

import pytest

from ..compiler import Compiler
from .. import get_lib_dir
from .context import CodeGenOptions


_SUB_FIELD = re.compile(r"await_arg_capture_t<decltype\(\((.*)\)\)>>> __sub_0;")
_EMPLACE = re.compile(r"__sub_0\.emplace\((.*)\);")


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "nested"])
@pytest.mark.parametrize("generic", ["none", "owner", "method"])
@pytest.mark.parametrize("bound", [False, True], ids=["direct_await", "bound_await"])
def test_resumable_method_frame_declarations_match_references(
        nested: bool, generic: str, bound: bool) -> None:
    owner_decl = "Inner[T]" if generic == "owner" else "Inner"
    method_decl = "compute[T]" if generic == "method" else "compute"
    value_type = "T" if generic != "none" else "int"
    record = (
        f"class {owner_decl}:\n"
        f"    async def {method_decl}(self, n: {value_type}) -> {value_type}:\n"
        "        return n\n"
    )
    if nested:
        record = "class Outer:\n" + "".join("    " + line for line in record.splitlines(True))
    owner_type = "Outer.Inner" if nested else "Inner"
    if generic == "owner":
        owner_type += "[int]"
    body = (
        "    result = x.compute(n)\n    return await result\n" if bound
        else "    return await x.compute(n)\n"
    )
    compiler = Compiler.from_source(
        record + f"async def use(x: {owner_type}, n: int) -> int:\n" + body,
        lib_dirs=[get_lib_dir() / "tpy"],
    )
    modules = compiler.compile()
    entry = next(m for m in modules if m.is_entry_point)
    hpp, cpp = compiler.generate_code_to_strings(entry)
    frame = "__coro_2_5_Outer_5_Inner_7_compute" if nested else "__coro_Inner_compute"
    assert f"struct {frame};" in hpp
    assert f"struct {frame} {{" in hpp
    factory_type = frame + ("<T>" if generic != "none" else "")
    assert f"{factory_type} compute(" in hpp
    assert f"{factory_type}::__poll__" in hpp + cpp
    concrete_type = frame + ("<::tpy::BigInt>" if generic != "none" else "")
    slot = "result" if bound else "__sub_0"
    assert f"std::optional<{concrete_type}> {slot};" in hpp
    assert not re.search(r"(?:Outer|Inner)::(?:__coro_|__gen_)", hpp + cpp)
    assert not re.search(r"__(?:coro|gen)_[\w]*\.", hpp + cpp)
    assert not compiler.diagnostics


@pytest.mark.parametrize("namespace", [None, "custom::workers"])
@pytest.mark.parametrize(("nested", "import_style"), [
    (False, "record_alias"), (False, "module_alias"), (True, "record_alias"),
], ids=["flat_record_alias", "flat_module_alias", "nested_record_alias"])
def test_coroutine_references_use_defining_module_namespace(
        tmp_path: Path, import_style: str, namespace: str | None, nested: bool) -> None:
    record = (
        "class Outer:\n    class Inner:\n"
        "        async def compute(self, n: int) -> int:\n            return n\n"
    ) if nested else (
        "class Outer:\n    async def compute(self, n: int) -> int:\n        return n\n"
    )
    directive = f'# tpy: cpp_namespace("{namespace}")\n' if namespace else ""
    (tmp_path / "worker.py").write_text(directive + record)
    (tmp_path / "peer.py").write_text(record)
    if import_style == "record_alias":
        imports = "from worker import Outer as First\nfrom peer import Outer as Second\n"
        first, second = "First", "Second"
    else:
        imports = "import worker as first_module\nimport peer as second_module\n"
        first, second = "first_module.Outer", "second_module.Outer"
    if nested:
        first += ".Inner"
        second += ".Inner"
    # Local aliases must preserve the two owners' distinct defining modules.
    source = (
        imports + "async def use(n: int) -> int:\n"
        f"    first = {first}()\n    second = {second}()\n"
        "    pending = first.compute(n)\n"
        "    result = await pending\n"
        "    return result + await second.compute(n)\n"
    )
    entry_path = tmp_path / "main.py"
    entry_path.write_text(source)
    compiler = Compiler(entry_path, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = next(m for m in modules if m.is_entry_point)
    hpp, cpp = compiler.generate_code_to_strings(entry)
    frame = "__coro_2_5_Outer_5_Inner_7_compute" if nested else "__coro_Outer_compute"
    worker_namespace = namespace or "tpyapp::worker"
    assert f"std::optional<::{worker_namespace}::{frame}> pending;" in hpp
    assert f"std::optional<::tpyapp::peer::{frame}> __sub_1;" in hpp
    assert not re.search(r"(?:Outer|Inner)::(?:__coro_|__gen_)", hpp + cpp)
    assert not any(alias + "::__coro_" in hpp + cpp
                   for alias in ("First", "Second", "first_module", "second_module"))
    assert not compiler.diagnostics


def test_stdlib_method_coroutine_keeps_mapped_namespace() -> None:
    source = (
        "from asyncio import Event as Ready\n"
        "async def use(event: Ready) -> bool:\n"
        "    pending = event.wait()\n"
        "    first = await pending\n"
        "    return first and await event.wait()\n"
    )
    compiler = Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = next(m for m in modules if m.is_entry_point)
    hpp, _ = compiler.generate_code_to_strings(entry)
    frame = "::tpystd::asyncio::__coro_Event_wait"
    assert f"std::optional<{frame}> pending;" in hpp
    assert f"std::optional<{frame}> __sub_1;" in hpp
    assert "Ready::__coro_" not in hpp
    assert "::tpyapp::asyncio::__coro_" not in hpp
    assert not compiler.diagnostics


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
