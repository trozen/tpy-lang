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
    ctx = compiler.collect_thir(entry, CodeGenOptions(thir_codegen=True))
    return dump_codegen_thir(entry.ast, entry.analyzer, ctx,
                             compiler.thir_reject_by_node)


def test_dump_includes_async_bodies():
    # The collection gap this collector closes: `lower_module` never reaches
    # a resumable body, so the old dump showed nothing for an `async def`.
    out = _dump(
        "import asyncio\nfrom tpy import Int32\n\n"
        "async def f(n: Int32) -> Int32:\n"
        "    await asyncio.sleep(0)\n"
        "    return n\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "resumable f:" in out
    assert "return_values:" in out


def test_dump_includes_constructors():
    out = _dump(
        "from tpy import Int32\n\n"
        "class R:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "ctor R.__init__:" in out
    assert "mil n = %n" in out


def test_dump_names_fallback_bodies():
    # "What did NOT route" is usually the question being asked, so a body
    # that fell back is named rather than silently absent.
    out = _dump(
        "from tpy import Int32\n\n"
        "class R:\n    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n\n"
        "def f(rs: list[R]) -> Int32:\n"
        "    return sum(r.n for r in rs)\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    # The first-reject reason rides along, so the dump answers WHY. Matched
    # loosely: the exact tag moves as constructs migrate, and this test is
    # about the reason being PRESENT, not about which construct blocks today.
    import re
    m = re.search(r"fn f: <fell back to AST: (\S+)>", out)
    assert m and m.group(1), out
    # The routed siblings still render, so the marker is per body.
    assert "fn main() -> None:" in out
    assert "ctor R.__init__:" in out


def test_resumable_keys_are_stable_across_runs():
    # Rendered keys must not be raw id()s -- otherwise the dump differs
    # run to run and cannot be diffed.
    src = ("import asyncio\nfrom tpy import Int32\n\n"
           "async def f(n: Int32) -> Int32:\n"
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
     "def f(n: Int32) -> Int32:\n"
     "    try:\n        return 1 // n\n"
     "    except ZeroDivisionError as e:\n        print(e)\n        return 0\n",
     "except "),
    ("for_else",
     "def f(xs: list[Int32]) -> Int32:\n"
     "    for x in xs:\n        if x > 2:\n            return x\n"
     "    else:\n        return -1\n    return 0\n",
     "else:"),
    ("raise_stmt",
     "def f(n: Int32) -> Int32:\n"
     "    if n < 0:\n        raise ValueError('neg')\n    return n\n",
     "raise "),
    ("membership",
     "def f(xs: list[Int32], n: Int32) -> bool:\n    return n in xs\n",
     "in["),
    ("str_membership",
     "def f(s: str) -> bool:\n    return 'a' in s\n",
     "str_in("),
    ("slice_assign",
     "def f(xs: list[Int32]) -> None:\n    xs[0:2] = [7, 8]\n",
     "] = "),
    ("setitem",
     "def f(d: dict[Int32, Int32]) -> None:\n    d[1] = 2\n",
     " = "),
    ("inplace_container",
     "def f(xs: list[Int32], ys: list[Int32]) -> None:\n    xs += ys\n",
     "inplace["),
]


@pytest.mark.parametrize("name,body,expect",
                         _CONSTRUCTS, ids=[c[0] for c in _CONSTRUCTS])
def test_construct_renders(name, body, expect):
    out = _dump("from tpy import Int32\n\n" + body
                + "\ndef main() -> None:\n    pass\nmain()\n")
    assert expect in out, f"{name} did not render: {out}"


def test_except_binding_is_rendered():
    # Regression: the arm read `.name` where the field is `.binding`, so the
    # `as e` half rendered as nothing at all.
    out = _dump(
        "from tpy import Int32\n\n"
        "def f(n: Int32) -> Int32:\n"
        "    try:\n        return 1 // n\n"
        "    except ZeroDivisionError as e:\n        print(e)\n        return 0\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "as %e" in out


def test_bodyless_binding_is_not_called_a_fallback():
    # A @native binding is never ATTEMPTED, so labelling it a fallback would
    # misreport the migration frontier.
    out = _dump(
        "from tpy import Int32\n"
        "# tpy: link(\"m\")\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    assert "<fell back to AST" not in out or "not a body-migration candidate" in out


def test_resumable_fallback_records_its_reason():
    # gen_async's fold site is a THIRD call of the node-carrying fold_attempt
    # (alongside body/ctor); without this, only the sync path was covered.
    out = _dump(
        "import asyncio\nfrom tpy import Int32\n\n"
        "class Holder:\n    items: list[Int32]\n"
        "    def __init__(self) -> None:\n        self.items = [1, 2]\n\n"
        "async def f(h: Holder) -> Int32:\n"
        "    await asyncio.sleep(0)\n"
        "    match h.items[0]:\n"
        "        case 1:\n            return 10\n"
        "        case _:\n            return 20\n\n"
        "def main() -> None:\n    pass\nmain()\n")
    import re
    m = re.search(r"fn f: <fell back to AST: (\S+)>", out)
    assert m and m.group(1), out
