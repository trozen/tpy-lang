"""Unit tests for `--dump-thir` (dump.py).

The load-bearing one is `test_every_node_has_a_dump_arm`: it fails the moment
a new THIR expression/statement class lands without a render arm, which is the
only check that catches an omission BEFORE any test happens to exercise it.
The dispatch itself raises on an unknown node (rather than printing a
placeholder), so a runtime miss is loud too.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from . import dump as dump_mod
from .dump import _UNDUMPED, _expr, _stmt_lines
from .nodes import Form, THIRExpr, THIRLiteral, THIRStmt


def _concrete(base: type) -> set[type]:
    """Every subclass of `base`, transitively."""
    out: set[type] = set()
    stack = [base]
    while stack:
        for sub in stack.pop().__subclasses__():
            stack.append(sub)
            out.add(sub)
    return out


def _dispatched_names() -> set[str]:
    """Class names appearing as an `isinstance(x, THIRFoo)` target in dump.py.

    Parsed rather than grepped so a merely-imported (but never dispatched)
    class does not count as covered.
    """
    tree = ast.parse(Path(dump_mod.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance" and len(node.args) == 2):
            target = node.args[1]
            elts = target.elts if isinstance(target, ast.Tuple) else [target]
            for elt in elts:
                if isinstance(elt, ast.Name):
                    names.add(elt.id)
    return names


def test_every_node_has_a_dump_arm():
    required = _concrete(THIRExpr) | _concrete(THIRStmt)
    dispatched = _dispatched_names()
    missing = sorted(c.__name__ for c in required
                     if c.__name__ not in dispatched and c not in _UNDUMPED)
    assert not missing, (
        "THIR node classes with no --dump-thir arm: " + ", ".join(missing)
        + " -- add an arm in dump.py (or list the class in _UNDUMPED with a "
          "reason)")


def test_undumped_allowlist_has_no_stale_entries():
    # An allowlisted class that DID get an arm must leave the list, else the
    # exception outlives its reason and silently weakens the check above.
    dispatched = _dispatched_names()
    stale = sorted(c.__name__ for c in _UNDUMPED if c.__name__ in dispatched)
    assert not stale, (
        "classes in _UNDUMPED that now have a dump arm: " + ", ".join(stale)
        + " -- remove them from the allowlist")


def test_unknown_expr_node_raises():
    # The dispatch refuses to degrade to a placeholder for an unregistered
    # node (mirrors faces.py rejecting an unregistered face).
    class _Rogue(THIRExpr):
        pass

    with pytest.raises(AssertionError, match="no arm for _Rogue"):
        _expr(_Rogue(result_type=None))


def test_unknown_stmt_node_raises():
    class _RogueStmt(THIRStmt):
        pass

    with pytest.raises(AssertionError, match="no arm for _RogueStmt"):
        _stmt_lines(_RogueStmt(), 0)


def test_known_node_still_renders():
    # Guard the raise from over-triggering on a node that does have an arm.
    assert _expr(THIRLiteral(result_type=None, value=1, form=Form.VALUE)) \
        == "lit(1)"


def _dump(src: str) -> str:
    """Dump via the real codegen path (the collector `--dump-thir` uses)."""
    from ..codegen_cpp.context import CodeGenOptions
    from .dump import dump_codegen_thir
    from .testutil import _compile, _entry

    compiler, modules = _compile(src)
    entry = _entry(modules)
    ctx = compiler.collect_thir(entry, CodeGenOptions(), tolerate_reject=True)
    return dump_codegen_thir(entry.ast, entry.analyzer, ctx,
                             compiler.thir_reject_by_node)


def test_dump_includes_async_bodies():
    # A resumable body lowers at frame emission, not at the sync per-body
    # entry, so only a collector reading codegen's caches shows an
    # `async def` at all.
    out = _dump(
        "import asyncio\nfrom tpy import int32\n\n"
        "async def f(n: int32) -> int32:\n"
        "    await asyncio.sleep(0)\n"
        "    return n\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "frame f [" in out
    # The return terminator's leaf follows it in its block.
    assert "    -> return\n      resumable_return %n" in out



def test_dump_prints_the_published_frame():
    # The frame is printed beside its leaves: captures, locals with their
    # placement, then the coarse CFG block by block with region stacks and
    # terminators.
    out = _dump(
        "from typing import Iterator\nfrom tpy import int32\n\n"
        "def g(n: int32) -> Iterator[int32]:\n"
        "    try:\n"
        "        for i in range(n):\n"
        "            yield i\n"
        "    finally:\n"
        "        print('done')\n\n"
        "def main() -> None:\n"
        "    for x in g(2):\n        print(x)\n"
        "main()\n")
    assert "frame g [" in out
    assert "  param n: int32 capture=VALUE" in out
    assert "placement=" in out
    assert "b0 [entry, case]:" in out
    assert "in try(__finally_0)" in out
    assert "suspend#0 yield -> b" in out
    # Each slot's leaf follows its position: the yielded value under the
    # suspension, the range bound under the loop setup, the helper's print
    # under the helper.
    assert "      yield_value: " in out
    assert "      range_bound: " in out
    assert "finally helper __finally_0:\n    print(" in out
    # The loop's machinery fields are published holders.
    # A range counter belongs to its loop, with the loop's strategy, no
    # source alias and the names the loop binds, and runs no user code when
    # dropped.
    assert ("  holder loop __for_i_0: int32_t of loop@6 range alias=- "
            "binds=i\n") in out
    assert "  holder try_finally " not in out  # its finally does not suspend


def test_dump_prints_loop_holder_owners():
    # Each loop holder prints its loop's strategy, the source alias it is
    # spelled off and the names the loop binds; a source the frame keeps
    # whole is its own alias, and an `async for` iterator has no loop.
    out = _dump(
        "import asyncio\nfrom typing import Iterator\n"
        "from tpy import int32, Own\n\n\n"
        "class Box:\n"
        "    def __init__(self, n: int32) -> None:\n        self.n = n\n\n\n"
        "class It:\n    i: int32\n\n"
        "    def __init__(self) -> None:\n        self.i = 0\n\n"
        "    def __aiter__(self) -> 'It':\n        return self\n\n"
        "    async def __anext__(self) -> int32:\n"
        "        self.i += 1\n"
        "        if self.i > 2:\n            raise StopAsyncIteration\n"
        "        return self.i\n\n\n"
        "def make() -> Own[list[Box]]:\n    return [Box(1), Box(2)]\n\n\n"
        "def g(xs: list[int32]) -> Iterator[int32]:\n"
        "    for b in make():\n        print(b.n)\n"
        "    for x in xs:\n        yield x\n\n\n"
        "async def f() -> int32:\n"
        "    total = 0\n"
        "    async for v in It():\n        total += v\n"
        "    return total\n\n\n"
        "def main() -> None:\n"
        "    for x in g([1, 2]):\n        print(x)\n"
        "    print(asyncio.run(f()))\n\n\n"
        "main()\n")
    for line in (
            "  holder loop __for_src_0: __for_src_0_t of loop@32 whole "
            "alias=__for_src_0_t binds=b\n",
            "  holder loop __for_it_1: ::tpy::begin_iter_t<__for_src_1_t> "
            "of loop@34 begin_end alias=__for_src_1_t binds=x\n",
            "  holder loop __for_itr_0: ::tpy::aiter_type_t<It> drops\n",
    ):
        assert line in out, line


_ASYNC_FRAME = (
    "import asyncio\nfrom tpy import int32\n\n\n"
    "class Tracer:\n"
    "    def __enter__(self) -> None:\n        print('enter')\n\n"
    "    def __exit__(self, exc_type, exc_val, exc_tb) -> None:\n"
    "        print('exit')\n\n\n"
    "class CM:\n"
    "    async def __aenter__(self) -> int32:\n        return 7\n\n"
    "    async def __aexit__(self, exc_type: None, exc_val: None,"
    " exc_tb: None) -> None:\n        print('aexit')\n\n\n"
    "async def step(n: int32) -> int32:\n    return n\n\n\n"
    "async def f(xs: list[int32], sel: int32) -> int32:\n"
    "    total = 0\n"
    "    try:\n"
    "        for x in xs:\n            total += await step(x)\n"
    "    except ValueError:\n        total = await step(-1)\n"
    "    finally:\n        await step(0)\n"
    "    with Tracer():\n        total += await step(1)\n"
    "    async with CM() as v:\n        total += v\n"
    "    match sel:\n"
    "        case 1:\n            y = await step(2)\n            total += y\n"
    "        case _:\n            pass\n"
    "    return total\n\n\n"
    "def main() -> None:\n    print(asyncio.run(f([1, 2], 1)))\n\n\n"
    "main()\n")


def test_dump_prints_async_frame_operations():
    # Each pseudo-statement and terminator of a coroutine frame by its
    # operation, the regions with the blocks their edges reach, the handler
    # entries, and the try-finally holders.
    out = _dump(_ASYNC_FRAME)
    for line in (
            "-> suspend#0 await inline vardecl -> b9",
            "for_iter_setup uid 0",
            "-> for_advance uid 0 b7 b8",
            "-> match_dispatch b22 b24 join b21",
            "with_enter ctx 0",
            "async_with_setup ctx 1",
            "finally_exit __finally_exc_0",
            "b4 [case] in try finally b2:",
            "b2 [case] in finally exit b1:",
            "b10 in except finally b2:",
            "b14 [case] in with(ctx 0) post b13:",
            "  handler builtins.ValueError of try finally b2 enters b10",
            "  holder try_finally __finally_exc_0: std::exception_ptr",
    ):
        assert line in out, line


def test_dump_prints_the_carried_print_verdicts():
    # A resume case's re-extraction facts and the receiver are printed
    # with the frame.
    out = _dump(
        "import asyncio\nfrom tpy import int32\n\n\n"
        "class Box:\n"
        "    def __init__(self, n: int32) -> None:\n        self.n = n\n\n"
        "    async def get(self, v: int32 | str) -> int32:\n"
        "        if isinstance(v, int32):\n"
        "            await asyncio.sleep(0)\n"
        "            return v + self.n\n"
        "        return 0\n\n\n"
        "def main() -> None:\n    print(asyncio.run(Box(1).get(2)))\n\n\n"
        "main()\n")
    assert "  receiver Box const" in out
    assert "  resume extract v -> " in out
    assert " param-const" in out


def test_dump_prints_the_source_verdict_flags():
    # A borrowed manager that already renders as a pointer, a loop source
    # the printer unwraps, and an owned erased await handle are flagged at
    # their slots.
    out = _dump(
        "import asyncio\nfrom typing import Iterator\n"
        "from tpy import Own, int32\nfrom tpy.coro import Cancellable\n\n\n"
        "class M:\n"
        "    def __init__(self) -> None:\n        pass\n\n"
        "    async def __aenter__(self) -> int32:\n        return 1\n\n"
        "    async def __aexit__(self, a: None, b: None, c: None) -> None:\n"
        "        pass\n\n\n"
        "GM = M()\n\n\n"
        "async def held() -> int32:\n"
        "    async with GM as v:\n        return v\n\n\n"
        "def chars(s: str | None) -> Iterator[str]:\n"
        "    if s is not None:\n"
        "        for ch in s:\n            yield ch\n\n\n"
        "async def one() -> int:\n    return 1\n\n\n"
        "async def consume(c: Own[Cancellable[int]]) -> int:\n"
        "    return await c\n\n\n"
        "async def main_coro() -> None:\n"
        "    print(await held(), list(chars('ab')), await consume(one()))\n\n\n"
        "def main() -> None:\n    asyncio.run(main_coro())\n\n\n"
        "main()\n")
    flagged = {line.split(":")[0].strip(): line for line in out.splitlines()
               if line.rstrip().endswith("]") and ": " in line}
    assert flagged["with_manager"].endswith(" [pointer]")
    assert flagged["for_iterable"].endswith(" [unwrapped]")
    assert flagged["await_operand"].endswith(" [owned-erased]")


def test_dump_includes_constructors():
    out = _dump(
        "from tpy import int32\n\n"
        "class R:\n"
        "    n: int32\n"
        "    def __init__(self, n: int32) -> None:\n        self.n = n\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "ctor R.__init__:" in out
    assert "mil n = %n" in out


def test_resumable_keys_are_stable_across_runs():
    # Rendered keys must not be raw id()s -- otherwise the dump differs
    # run to run and cannot be diffed.
    src = ("import asyncio\nfrom tpy import int32\n\n"
           "async def f(n: int32) -> int32:\n"
           "    await asyncio.sleep(0)\n"
           "    return n\n\n"
           "def main() -> None:\n    pass\nmain()\n")
    assert _dump(src) == _dump(src)


# One tiny source per construct, each asserting a substring the arm must
# render. Parametrized rather than one blob so a failure names the construct,
# and substring-asserted rather than no-crash because a missing field would
# otherwise render as empty text (how the except-binding bug hid).
_CONSTRUCTS = [
    ("except_binding",
     "def f(n: int32) -> int32:\n"
     "    try:\n        return 1 // n\n"
     "    except ZeroDivisionError as e:\n        print(e)\n        return 0\n",
     "except "),
    ("for_else",
     "def f(xs: list[int32]) -> int32:\n"
     "    for x in xs:\n        if x > 2:\n            return x\n"
     "    else:\n        return -1\n    return 0\n",
     "else:"),
    ("raise_stmt",
     "def f(n: int32) -> int32:\n"
     "    if n < 0:\n        raise ValueError('neg')\n    return n\n",
     "raise "),
    ("membership",
     "def f(xs: list[int32], n: int32) -> bool:\n    return n in xs\n",
     "in["),
    ("str_membership",
     "def f(s: str) -> bool:\n    return 'a' in s\n",
     "str_in("),
    ("slice_assign",
     "def f(xs: list[int32]) -> None:\n    xs[0:2] = [7, 8]\n",
     "] = "),
    ("setitem",
     "def f(d: dict[int32, int32]) -> None:\n    d[1] = 2\n",
     " = "),
    ("inplace_dunder",
     "def f(xs: list[int32], ys: list[int32]) -> None:\n    xs += ys\n",
     "native tpy::list_extend"),
    ("isinstance_eval_then",
     "from tpy import Own\n"
     "class A:\n    def __init__(self) -> None:\n        pass\n"
     "def make() -> Own[A]:\n    return A()\n"
     "def f() -> bool:\n    return isinstance(make(), A)\n",
     "eval_then("),
    ("isinstance_dyn_cast",
     "from typing import Protocol\nfrom tpy import dynamic\n"
     "@dynamic\nclass P(Protocol):\n    def n(self) -> int32: ...\n"
     "class C(P):\n    def __init__(self) -> None:\n        pass\n"
     "    def n(self) -> int32:\n        return 1\n"
     "def f(p: P) -> int32:\n    if isinstance(p, C):\n"
     "        return p.n()\n    return 0\n",
     "dyn_cast["),
    ("isinstance_poly_value",
     "from typing import Protocol\nfrom tpy import dynamic\n"
     "@dynamic\nclass P(Protocol):\n    def n(self) -> int32: ...\n"
     "class C(P):\n    def __init__(self) -> None:\n        pass\n"
     "    def n(self) -> int32:\n        return 1\n"
     "def f(p: P) -> bool:\n    return isinstance(p, C)\n",
     "dyn_isinstance_multi("),
]


@pytest.mark.parametrize("name,body,expect",
                         _CONSTRUCTS, ids=[c[0] for c in _CONSTRUCTS])
def test_construct_renders(name, body, expect):
    out = _dump("from tpy import int32\n\n" + body
                + "\ndef main() -> None:\n    pass\nmain()\n")
    assert expect in out, f"{name} did not render: {out}"


def test_except_binding_is_rendered():
    # Regression: the arm read `.name` where the field is `.binding`, so the
    # `as e` half rendered as nothing at all.
    out = _dump(
        "from tpy import int32\n\n"
        "def f(n: int32) -> int32:\n"
        "    try:\n        return 1 // n\n"
        "    except ZeroDivisionError as e:\n        print(e)\n        return 0\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "as %e" in out


def test_bodyless_binding_says_it_has_no_body():
    # A @native binding is never ATTEMPTED, so it must not read as a reject:
    # the dump would otherwise name a lowering gap that does not exist.
    out = _dump(
        "__tpy__ = {\"includes\": [\"<math.h>\"],\n"
        "           \"links\": [\"m\"]}\n"
        "from tpy.extern import native\n"
        "from tpy import float64\n\n"
        "@native(binding=\"C\")\n"
        "def sqrt(x: float64) -> float64: ...\n\n"
        "def main() -> None:\n    print(sqrt(4.0))\nmain()\n")
    assert "fn sqrt: <no body to lower>" in out, out
    assert "rejected" not in out, out


# One body lowers, the next rejects, a later one lowers and the module init
# never gets its turn -- the no-THIR states the dump must keep apart. The rejecting shape is a
# rebound record a tuple unpack takes an owned element into
# (BUGS.md#sync-unpack-into-rebound-local-rejects); if lowering ever admits it
# this test goes vacuous, which the first assertion catches.
_REJECTING_MODULE = (
    "from tpy import int32, Own\n\n\n"
    "class Point:\n"
    "    x: int32\n\n"
    "    def __init__(self, x: int32) -> None:\n"
    "        self.x = x\n\n\n"
    "def mk(i: int32) -> tuple[Own[Point], int32]:\n"
    "    return (Point(i), i)\n\n\n"
    "def lowers(n: int32) -> int32:\n"
    "    return n + 1\n\n\n"
    "def rejects() -> None:\n"
    "    p = Point(1)\n"
    "    p, n = mk(2)\n"
    "    print(p.x, n)\n\n\n"
    "def after() -> int32:\n"
    "    return 2\n\n\n"
    "def main() -> None:\n    print(lowers(1))\nmain()\n")


def test_rejected_body_names_its_reason():
    out = _dump(_REJECTING_MODULE)
    assert "fn rejects: <rejected: " in out, out
    # The bodies BEFORE the reject are the point of dumping a rejecting
    # module at all: a reject used to abort the module and print nothing.
    assert "fn lowers(n: int32) -> int32:" in out, out


def test_a_reject_does_not_end_the_survey_of_function_bodies():
    # Function, method and constructor bodies lower ahead of emission, so the
    # dump attempts every one: a body after the rejecting one shows its THIR.
    out = _dump(_REJECTING_MODULE)
    assert "fn after() -> int32:" in out, out
    assert out.count("<rejected: ") == 1, out


def test_units_lowered_during_emission_are_not_called_rejected():
    # The module-init body lowers while the C++ around it is written, which
    # the function reject above never reaches -- never lowered AND never
    # attempted, so calling it rejected would invent a gap.
    out = _dump(_REJECTING_MODULE)
    assert "top-level __tpy_init: <not attempted:" in out, out




def test_anchored_rebind_slot_is_rendered():
    # A rebind slot is declared ahead of the body-level statement that
    # anchors it, not by the write that fills it, so the dump prints it at
    # that statement or the slot vanishes from the IR view.
    out = _dump(
        "from tpy import int32\n\n"
        "class Box:\n"
        "    val: int32\n"
        "    def __init__(self, v: int32) -> None:\n        self.val = v\n\n"
        "def f(a: Box, c: bool) -> int32:\n"
        "    x = a\n"
        "    if c:\n        x = Box(2)\n"
        "    return x.val\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "slot %x: std::optional<Box>" in out, out
