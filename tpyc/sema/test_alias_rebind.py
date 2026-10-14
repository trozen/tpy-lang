"""The alias-rebind pass's facts for a rebound tuple binding: every site's
storage, what a rebind keeps of the tuple it replaces, the pointer-tuple
sets."""

import pytest

from ..parse import RebindStorage, TpyAssign, TpyStmt, TpyVarDecl
from ..sema.alias_rebind import kept_elems
from ..thir.testutil import _compile, _entry

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


def _sites(body: str, name: str) -> list[RebindStorage | None]:
    """The storage of every stamped rebind of `name` in the body of
    `subject`, in program order, after checking sema recorded no
    per-element verdict for it."""
    source = PRELUDE + ("def subject(c: bool) -> None:\n"
                        "    v = Box(5)\n    w = Box(6)\n    b = Box(7)\n"
                        + "".join("    " + line + "\n"
                                  for line in body.splitlines()))
    compiler, modules = _compile(source)
    entry = _entry(modules)
    subject = next(f for f in entry.ast.functions if f.name == "subject")
    storage = entry.analyzer.ctx.tuple_elem_storage
    out: list[RebindStorage | None] = []

    def walk(stmts: list[TpyStmt]) -> None:
        for s in stmts:
            bound = (s.name if isinstance(s, TpyVarDecl)
                     else s.target.name
                     if isinstance(s, TpyAssign)
                     and hasattr(s.target, "name") else None)
            if bound == name and s.rebind_storage is not None:
                assert s not in storage
                out.append(s.rebind_storage)
            for sub in s.sub_bodies():
                walk(sub)
    walk(subject.body)
    return out


@pytest.mark.parametrize("body", [
    # Nothing reaches the old element at the second binding; `a` does at
    # the third.
    "p = make_mixed(1, v)\np = make_mixed(2, v)\na = p[0]\n"
    "p = make_mixed(3, v)\na.n += 100\nprint(a.n, p[0].n)",
    # The new value keeps the object the rebind replaces.
    "p = make_mixed(1, b)\np = make_mixed(9, p[0])\np[1].n += 100\n"
    "print(p[0].n, p[1].n)",
    # A value read off the old element is a copy taken before the rebind.
    "p = (Box(1), 0)\np = (Box(9), p[0].n)\nprint(p[0].n, p[1])",
    # A deleted tuple's elements may still be read through an alias.
    "p = make_mixed(1, b)\na = p[0]\ndel p\np = make_mixed(9, b)\n"
    "a.n += 1\nprint(a.n, p[0].n)",
    # A path on which the tuple is unbound leaves null element pointers.
    "if c:\n    t = pair(1)\nt = pair(9)\nprint(t[0].n)",
    # An unpack target of a tuple read no more.
    "p = make_mixed(1, b)\na0, a1 = p\np = make_mixed(9, b)\n"
    "a0.n += 100\nprint(a0.n, a1.n, p[0].n)",
])
def test_every_rebind_takes_storage_of_its_own(body: str) -> None:
    # Reusing the old object's storage is MIR's to prove: sema leaves every
    # site OWN, whether or not anything still reaches the old object.
    name = "t" if body.startswith("if c:") else "p"
    sites = _sites(body, name)
    assert sites and all(s is RebindStorage.OWN for s in sites), sites


def _kept(source: str) -> frozenset[int | None]:
    """What the last stamped rebind of `p` in `subject` keeps of the tuple
    it replaces (`kept_elems`)."""
    _, modules = _compile(source)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    site = [s for s in fn.body if isinstance(s, (TpyVarDecl, TpyAssign))
            and s.rebind_storage is not None][-1]
    value = site.init if isinstance(site, TpyVarDecl) else site.value
    return kept_elems(entry.analyzer.ctx, "p", value)


def test_unknown_callee_keeps_every_owned_element() -> None:
    # A callable value has no return-borrow facts: its result may keep any
    # element of the tuple it is handed.
    source = PRELUDE.replace("from tpy import int32, Own",
                             "from typing import Callable\n"
                             "from tpy import int32, Own") + (
        "def subject(f: Callable[[int32, Box], tuple[Own[Box], Box]],\n"
        "            b: Box) -> None:\n"
        "    p = make_mixed(1, b)\n"
        "    p = f(9, p[0])\n"
        "    print(p[0].n, p[1].n)\n")
    assert _kept(source) == {0}


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
    ("p = make_mixed(1, v)\np = later(9, v)", set()),
    # Handing the old element over may keep it.
    ("p = make_mixed(1, v)\np = later(9, p[0])", {0}),
    # ... and handing the whole tuple keeps all of it.
    ("p = make_mixed(1, v)\np = later_whole(9, p)", {None}),
    # A method receiver is an operand like any other.
    ("c = Cell(4)\np = (Cell(1), c)\np = p[0].f(9)", {0}),
    # An operand moved into an `Own` slot is not retained.
    ("p = make_mixed(1, v)\np = later_own(9, p[0], v)", set()),
])
def test_call_retention_follows_the_contract(body: str, expected: set,
                                             before: bool) -> None:
    # What a rebind keeps is the call's contract, whichever body is
    # analyzed first.
    subject = ("def subject(c0: bool) -> None:\n    v = Box(5)\n"
               + "".join("    " + line + "\n" for line in body.splitlines())
               + "    print(p[0].n)\n")
    source = (PRELUDE + CALLEES + subject if before
              else PRELUDE + subject + CALLEES)
    assert _kept(source) == expected


@pytest.mark.parametrize(("arg", "expected"), [
    ("p[0]", {0}),
    ("v", set()),
])
def test_callable_value_retains_its_lent_operands(arg: str,
                                                  expected: set) -> None:
    source = PRELUDE.replace("from tpy import int32, Own",
                             "from typing import Callable\n"
                             "from tpy import int32, Own") + (
        "def subject(f: Callable[[int32, Box], tuple[Own[Box], Box]],\n"
        "            v: Box) -> None:\n"
        "    p = make_mixed(1, v)\n"
        f"    p = f(9, {arg})\n"
        "    print(p[0].n, p[1].n)\n")
    assert _kept(source) == expected
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
