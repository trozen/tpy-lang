"""The alias-rebind pass's per-element verdict for a rebound tuple binding."""

import pytest

from ..parse import RebindStorage, TpyAssign, TpyIf, TpyStmt, TpyVarDecl
from ..thir.testutil import _compile, _entry

IN, OWN = RebindStorage.IN_PLACE, RebindStorage.OWN

PRELUDE = """\
from tpy import int32, Own
class Box:
    def __init__(self, n: int32) -> None:
        self.n = n
def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)
class Node:
    child: Box
    def __init__(self, n: int32) -> None:
        self.child = Box(n)
def make_node(n: int32, b: Box) -> tuple[Own[Node], Box]:
    return (Node(n), b)
def pair(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))
"""


def _sites(body: str, name: str) -> list[tuple[RebindStorage | None, ...]]:
    """The per-element verdicts of every decided rebind of `name` in the
    body of `subject`, in program order."""
    source = PRELUDE + ("def subject(c: bool) -> None:\n"
                        "    v = Box(5)\n    w = Box(6)\n    b = Box(7)\n"
                        + "".join("    " + line + "\n"
                                  for line in body.splitlines()))
    compiler, modules = _compile(source)
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    storage = entry.analyzer.ctx.tuple_elem_storage
    out: list[tuple[RebindStorage | None, ...]] = []

    def walk(stmts: list[TpyStmt]) -> None:
        for s in stmts:
            bound = (s.name if isinstance(s, TpyVarDecl)
                     else s.target.name
                     if isinstance(s, TpyAssign)
                     and hasattr(s.target, "name") else None)
            if bound == name and s in storage:
                verdict = storage[s]
                # The site verdict is IN_PLACE exactly when every owned
                # element is.
                assert (s.rebind_storage is IN) == all(
                    v is not OWN for v in verdict)
                out.append(verdict)
            for sub in s.sub_bodies():
                walk(sub)
    walk(subject.body)
    return out


@pytest.mark.parametrize(("body", "name", "expected"), [
    # Nothing reaches the old element at the second binding; `a` does at
    # the third.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\na = p[0]\n"
     "p = make_mixed(3, v)\na.n += 100\nprint(a.n, p[0].n)",
     "p", [(IN, None), (OWN, None)]),
    # The origin owns no element: an in-place write would land in `v`.
    ("p = (v, w)\np = make_mixed(1, v)\nprint(p[0].n)",
     "p", [(OWN, None)]),
    # The new value keeps the object the rebind would overwrite.
    ("p = make_mixed(1, b)\np = make_mixed(9, p[0])\np[1].n += 100\n"
     "print(p[0].n, p[1].n)",
     "p", [(OWN, None)]),
    # The literal twin: an element borrowing the old element.
    ("p = make_mixed(1, b)\np = (Box(9), p[0])\nprint(p[0].n, p[1].n)",
     "p", [(OWN, None)]),
    # ... through a field of the old element.
    ("q = make_node(1, b)\nq = make_node(9, q[0].child)\nprint(q[1].n)",
     "q", [(OWN, None)]),
    # A value read off the old element is a copy taken before the rebind.
    ("p = (Box(1), 0)\np = (Box(9), p[0].n)\nprint(p[0].n, p[1])",
     "p", [(IN, None)]),
    # A deleted tuple's elements may still be read through an alias.
    ("p = make_mixed(1, b)\na = p[0]\ndel p\np = make_mixed(9, b)\n"
     "a.n += 1\nprint(a.n, p[0].n)",
     "p", [(OWN, None)]),
    # A whole alias of a branch-hoisted tuple keeps both of its elements.
    ("if c:\n    t = pair(1)\nelse:\n    t = pair(2)\nu = t\nt = pair(9)\n"
     "u[0].n += 100\nprint(u[0].n, t[0].n)",
     "t", [(OWN, OWN)]),
    # A path on which the tuple is unbound leaves null element pointers.
    ("if c:\n    t = pair(1)\nt = pair(9)\nprint(t[0].n)",
     "t", [(OWN, OWN)]),
    # An unpack target of a tuple still read after it aliases the owned
    # element it was given.
    ("p = make_mixed(1, b)\na0, a1 = p\nprint(p[0].n)\np = make_mixed(9, b)\n"
     "a0.n += 100\nprint(a0.n, a1.n, p[0].n)",
     "p", [(OWN, None)]),
    # One of a tuple read no more moves the element out: nothing is left to
    # alias.
    ("p = make_mixed(1, b)\na0, a1 = p\np = make_mixed(9, b)\n"
     "a0.n += 100\nprint(a0.n, a1.n, p[0].n)",
     "p", [(IN, None)]),
    # A target holding the borrowed element points outside the tuple.
    ("p = make_mixed(1, b)\n_, a = p\np = make_mixed(9, b)\n"
     "a.n += 100\nprint(a.n, p[0].n)",
     "p", [(IN, None)]),
    # An operand shape the retention projection does not model (a ternary)
    # keeps every element of a tuple it mentions.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\n"
     "p = make_mixed(9, p[0] if c else v)\nprint(p[0].n, p[1].n)",
     "p", [(IN, None), (OWN, None)]),
    # ... and one that does not mention the tuple keeps nothing of it.
    ("p = make_mixed(1, v)\np = make_mixed(9, v if c else w)\n"
     "print(p[0].n)",
     "p", [(IN, None)]),
])
def test_tuple_rebind_per_element_verdict(
        body: str, name: str, expected: list) -> None:
    assert _sites(body, name) == expected


def test_unknown_callee_keeps_every_owned_element() -> None:
    # A callable value has no return-borrow facts: its result may keep any
    # element of the tuple it is handed, so nothing is written in place.
    source = PRELUDE.replace("from tpy import int32, Own",
                             "from typing import Callable\n"
                             "from tpy import int32, Own") + (
        "def subject(f: Callable[[int32, Box], tuple[Own[Box], Box]],\n"
        "            b: Box) -> None:\n"
        "    p = make_mixed(1, b)\n"
        "    p = f(9, p[0])\n"
        "    print(p[0].n, p[1].n)\n")
    _, modules = _compile(source)
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    site = subject.body[1]
    assert entry.analyzer.ctx.tuple_elem_storage.get(site) == (OWN, None), (
        entry.analyzer.ctx.tuple_elem_storage.get(site),
        site.init.resolved_function_info)


CALLEES = """\
def later(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)
def later_whole(n: int32, t: tuple[Box, Box]) -> tuple[Own[Box], Box]:
    return (Box(n), t[1])
def later_own(n: int32, b: Own[Box], c: Box) -> tuple[Own[Box], Box]:
    return (b, c)
class Cell:
    def __init__(self, n: int32) -> None:
        self.n = n
    def f(self, n: int32) -> tuple[Own[Cell], Cell]:
        return (Cell(n), self)
"""


@pytest.mark.parametrize("before", [True, False])
@pytest.mark.parametrize(("body", "expected"), [
    # An operand that is not the old tuple's storage keeps nothing of it.
    ("p = make_mixed(1, v)\np = later(9, v)", (IN, None)),
    # Handing the old element over may keep it.
    ("p = make_mixed(1, v)\np = later(9, p[0])", (OWN, None)),
    # ... and handing the whole tuple keeps every element it owns.
    ("p = make_mixed(1, v)\np = later_whole(9, p)", (OWN, None)),
    # A method receiver is an operand like any other.
    ("c = Cell(4)\np = (Cell(1), c)\np = p[0].f(9)", (OWN, None)),
    # An operand moved into an `Own` slot is not retained.
    ("p = make_mixed(1, v)\np = later_own(9, p[0], v)", (IN, None)),
])
def test_call_retention_follows_the_contract(body: str, expected: tuple,
                                             before: bool) -> None:
    # The verdict is the call's contract, whichever body is analyzed first.
    subject = ("def subject(c0: bool) -> None:\n    v = Box(5)\n"
               + "".join("    " + line + "\n" for line in body.splitlines())
               + "    print(p[0].n)\n")
    source = (PRELUDE + CALLEES + subject if before
              else PRELUDE + subject + CALLEES)
    _, modules = _compile(source)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    site = [s for s in fn.body if isinstance(s, (TpyVarDecl, TpyAssign))
            and s in entry.analyzer.ctx.tuple_elem_storage][-1]
    assert entry.analyzer.ctx.tuple_elem_storage[site] == expected


@pytest.mark.parametrize(("arg", "expected"), [
    ("p[0]", (OWN, None)),
    ("v", (IN, None)),
])
def test_callable_value_retains_its_lent_operands(arg: str,
                                                  expected: tuple) -> None:
    source = PRELUDE.replace("from tpy import int32, Own",
                             "from typing import Callable\n"
                             "from tpy import int32, Own") + (
        "def subject(f: Callable[[int32, Box], tuple[Own[Box], Box]],\n"
        "            v: Box) -> None:\n"
        "    p = make_mixed(1, v)\n"
        f"    p = f(9, {arg})\n"
        "    print(p[0].n, p[1].n)\n")
    _, modules = _compile(source)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    assert entry.analyzer.ctx.tuple_elem_storage.get(fn.body[1]) == expected


def _pointer_tuples(body: str) -> tuple[dict[str, str], list[str]]:
    """`subject`'s pointer tuples, each with the owner whose storage it
    holds, and the compile's warnings."""
    source = PRELUDE + ("def subject(c: bool) -> None:\n"
                        "    v = Box(5)\n    w = Box(6)\n"
                        + "".join("    " + line + "\n"
                                  for line in body.splitlines()))
    compiler, modules = _compile(source)
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    names = entry.analyzer.ctx.pointer_tuples_by_func[subject]
    warnings = [d.message for d in entry.analyzer.ctx.diagnostics
                if d.level.value == "warning"]
    return {n: o.name for n, o in names.items()}, warnings


@pytest.mark.parametrize(("body", "expected"), [
    # Every binding a name of a pointer tuple, through two hops and a
    # branch.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\nq = p\nr = q\n"
     "if c:\n    r = q\nr[0].n += 100\nprint(p[0].n, r[0].n)",
     {"p": "p", "q": "p", "r": "p"}),
    # Aliases bound from one another round a loop.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\nq = p\n"
     "for i in range(2):\n    r = q\n    q = r\nprint(q[0].n)",
     {"p": "p", "q": "p", "r": "p"}),
    # A ternary of two pointer tuples.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\ns = make_mixed(3, w)\n"
     "s = make_mixed(4, w)\nu = p if c else s\nprint(u[0].n)",
     {"p": "p", "s": "s", "u": "p"}),
    # A binding to a local owning nothing beside one from a pointer tuple,
    # in either order: the name would point into the local's storage, so
    # it stays out (and is refused at its binding to 's').
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\ns = (w, v)\nq = p\n"
     "q = s\nprint(q[0].n, w.n)",
     {"p": "p"}),
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\ns = (w, v)\nq = s\n"
     "q = p\nprint(q[0].n, w.n)",
     {"p": "p"}),
    # A binding owning an element makes the name a pointer tuple of its own.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\nq = p\n"
     "q = make_mixed(3, v)\nprint(q[0].n)",
     {"p": "p", "q": "q"}),
    # An `except` target of the same spelling is the handler's own name.
    ("p = make_mixed(1, v)\np = make_mixed(2, v)\nprint(p[0].n)\n"
     "try:\n    pass\nexcept ValueError as p:\n    pass",
     {"p": "p"}),
])
def test_pointer_tuple_names(body: str, expected: dict) -> None:
    assert _pointer_tuples(body)[0] == expected


@pytest.mark.parametrize("late", [False, True])
def test_alias_copy_warning_is_order_free(late: bool) -> None:
    # `r = q` is analyzed before or after `s` is known for a pointer tuple
    # (and so `q`, bound from it): no copy is warned either way, since the
    # rebind re-points.
    s_lines = "s = make_mixed(3, v)\ns = make_mixed(4, v)\n"
    body = ((s_lines if not late else "")
            + "p = make_mixed(1, v)\np = make_mixed(2, v)\nq = p\nr = q\n"
            "r = q\n" + (s_lines if late else "")
            + "q = s\nr = q\nprint(r[0].n)")
    names, warnings = _pointer_tuples(body)
    assert set(names) == {"p", "q", "r", "s"}
    assert not [w for w in warnings if "owned storage" in w]


@pytest.mark.parametrize(("line", "expected"), [
    # A capture of the body's own pointer tuple is that pointer tuple.
    ("print(sum(p[0].n + i for i in range(2)))", {"p"}),
    # A comprehension variable or a lambda parameter of the same name is a
    # binding of its own, which the capture reads instead.
    ("print([sum(p[0].n + i for i in range(2)) for p in [make_mixed(5, v)]])",
     set()),
    ("f: Callable[[int32], int32] = lambda p: sum(p + i for i in range(2))\n"
     "print(f(7))", set()),
])
def test_genexpr_capture_reads_creator_binding(line: str,
                                               expected: set) -> None:
    source = (PRELUDE.replace("from tpy import int32, Own",
                              "from typing import Callable\n"
                              "from tpy import int32, Own")
              + "def subject(v: Box) -> None:\n"
                "    p = make_mixed(1, v)\n    p = make_mixed(2, v)\n"
              + "".join("    " + ln + "\n" for ln in line.splitlines())
              + "    print(p[0].n)\n")
    _, modules = _compile(source)
    entry = _entry(modules)
    frames = [f for f in entry.ast.functions
              if f.is_genexpr and f.genexpr_owner == "subject"]
    assert len(frames) == 1
    sets = entry.analyzer.ctx.pointer_tuples_by_func
    assert set(sets[frames[0]]) == expected
