"""The recorded "this analysis read its slot" fact (`SemanticContext.slot_reads`,
set through `ExpressionAnalyzer.read_slot`): every reader of the slot hint
marks the node it analyzes, the mark climbs to the call argument holding it,
and a trial's marks go with the trial."""

import ast
import inspect
import pathlib

from ..parse import TpyCall, TpyExpr, TpyName
from ..parse.nodes import TpyStarUnpack, walk_body_stmts
from ..thir.testutil import _compile, _entry
from . import expressions as expressions_module
from .expressions import SLOT_READER_KINDS

_TPYC = pathlib.Path(__file__).resolve().parent.parent

# The context's hint accessors that do not mark: `slot_hint_at`,
# `expr_slot_hint`, `expr_type_hint` and the field behind them.
_UNMARKED_READS = frozenset(("slot_hint_at", "expr_slot_hint",
                             "expr_type_hint", "current_slot_hint"))

# The only functions outside the context that may read the hint without
# marking, each with how many reads it holds and why they decide nothing
# about the node: a further read in one of them is a new reader.
_PLUMBING = {
    "ExpressionAnalyzer.reuse":
        (1, "records the hint a handed-back node was analyzed under"),
    "ExpressionAnalyzer._analyze_expr_raw":
        (1, "the hint for the reuse check and the fill peek, which marks "
            "when the fill applies"),
    "ExpressionAnalyzer._forward_fill":
        (1, "hands a select's fill hint to the operand, whose own analysis "
            "reads it"),
    "ExpressionAnalyzer.slot_hint_for": (1, "the marking accessor"),
    "ExpressionAnalyzer.type_hint_for": (1, "the marking accessor"),
    "ExpressionAnalyzer.type_hint_here": (1, "the marking accessor"),
}

# The shapes typed from a hint (`_analyze_hinted_shape`) whose analysis
# WITHOUT a hint consults nothing: a hint reaches them only as what their
# parts are analyzed under, and the parts mark themselves.
_HINTED_ONLY = {
    "TpyName": "a function reference only at a callable slot; else a plain read",
    "TpyFieldAccess": "a dotted class only at a callable slot; else a field read",
    "TpyBinOp": "an and/or joins under a hint only; unhinted, its operands decide",
    "TpyIfExpr": "a ternary joins under a hint only; unhinted, its arms decide",
    "TpyTupleLiteral": "per-element hints only; unhinted, its elements decide",
}


def _functions(path: pathlib.Path):
    """(qualified name, node) of every function in `path`."""
    tree = ast.parse(path.read_text())
    out = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((f"{prefix}{child.name}", child))
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def test_every_hint_read_outside_the_context_marks():
    """A reader of the hint in force goes through a marking accessor
    (`slot_hint_for`, `type_hint_for`, `type_hint_here`); a new direct read
    of the context's hint is a reader that records nothing."""
    unmarked: list[str] = []
    for path in sorted(_TPYC.rglob("*.py")):
        if path.name.startswith("test_") or path.name == "context.py":
            continue
        for qname, fn in _functions(path):
            reads = [node for node in ast.walk(fn)
                     if isinstance(node, ast.Attribute)
                     and node.attr in _UNMARKED_READS
                     and isinstance(node.ctx, ast.Load)]
            allowed = _PLUMBING.get(qname, (0, ""))[0]
            if len(reads) > allowed:
                unmarked.extend(f"{path.relative_to(_TPYC)}:{node.lineno} "
                                f"{qname} reads .{node.attr}"
                                for node in reads)
    assert not unmarked, (
        "slot hint read without recording it (use ExpressionAnalyzer."
        "slot_hint_for / type_hint_for):\n" + "\n".join(sorted(set(unmarked))))


def test_every_hinted_shape_is_a_reader_or_hinted_only():
    """Each shape `_analyze_hinted_shape` types from a hint is either one
    whose unhinted analysis takes a default (`SLOT_READER_KINDS`, marked in
    `_analyze_expr_raw`) or one listed above as consulting nothing
    unhinted -- so a new hinted shape is a decision about its unhinted
    twin, not a reader that goes unrecorded without a hint."""
    source = inspect.getsource(
        expressions_module.ExpressionAnalyzer._analyze_hinted_shape)
    tree = ast.parse(source.replace("\n    ", "\n").lstrip())
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance" and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "expr"):
            spec = node.args[1]
            names = spec.elts if isinstance(spec, ast.Tuple) else [spec]
            kinds.update(n.id for n in names if isinstance(n, ast.Name))
    assert kinds, "no isinstance(expr, ...) arms found -- did the shape change?"
    readers = {k.__name__ for k in SLOT_READER_KINDS}
    unlisted = kinds - readers - set(_HINTED_ONLY)
    assert not unlisted, (
        f"hinted shapes neither in SLOT_READER_KINDS nor known to read "
        f"nothing unhinted: {sorted(unlisted)}")


_PROGRAM = """\
from typing import Callable
from tpy import dispatch, int32, int64, Own

class Box[T]:
    def ident(self) -> 'Box[T]':
        return self

    def size(self, xs: list[int32]) -> int32:
        return len(xs)

def make[T]() -> Own[Box[T]]:
    return Box[T]()

def bx(b: Box[int32]) -> int32:
    return 1

def lst(xs: list[int32]) -> int32:
    return len(xs)

@dispatch
def over(p: tuple[int64, int64]) -> int32:
    return 2

@dispatch
def over(s: str) -> str:
    return s

def fn(c: Callable[[int32], int32]) -> int32:
    return c(1)

def num(n: int32) -> int32:
    return n

def text(s: str) -> int32:
    return len(s)

def pair(p: tuple[int32, list[int32]]) -> int32:
    return p[0]

def var(*xs: int32) -> int32:
    return len(xs)

def main() -> None:
    n = 3
    c = n > 2
    print(lst([1, 2]))
    print(lst([x for x in range(3)]))
    print(fn(lambda x: x + 1))
    print(text(f"{n}"))
    print(num(n))
    print(num(n + 1))
    print(num(n if c else 2))
    print(pair((1, [2])))
    print(num(lst([4])))
    print(var(*[1, 2]))
    print(bx(make().ident()))
    print(lst(list()))
    print(bx(Box()))
    w: int64 = 1
    r = 0
    r = over((w, w))
    print(r)
    b = Box[int32]()
    print(num(b.size([5])))

main()
"""


def _args_of(modules, callee: str) -> list[TpyExpr]:
    """The first argument of every call to `callee` in `main`, in order."""
    main = next(f for f in _entry(modules).ast.functions if f.name == "main")
    found: list[TpyExpr] = []

    def on_expr(e: TpyExpr) -> None:
        stack = [e]
        while stack:
            node = stack.pop()
            if (isinstance(node, TpyCall) and isinstance(node.func, TpyName)
                    and node.func.name == callee and node.args):
                found.append(node.args[0])
            stack.extend(node.children())

    walk_body_stmts(main.body, on_expr, lambda s: None)
    return sorted(found, key=lambda a: a.loc.line)


def test_readers_mark_their_argument():
    compiler, modules = _compile(_PROGRAM)
    analyzer = _entry(modules).analyzer
    read = analyzer.expr.slot_was_read
    lst_literal, lst_comp, lst_inner, empty = _args_of(modules, "lst")
    assert read(lst_literal), "a list literal reads its slot"
    assert read(lst_comp), "a comprehension reads its slot"
    (lam,) = _args_of(modules, "fn")
    assert read(lam), "a lambda reads its slot"
    (fstr,) = _args_of(modules, "text")
    assert read(fstr), "an f-string reads its slot"
    name, binop, select, call, method = _args_of(modules, "num")
    assert not read(name), "a name reads no slot"
    assert not read(binop), "arithmetic reads no slot"
    assert read(select), "a ternary under a hint reads it"
    (tup,) = _args_of(modules, "pair")
    assert read(tup), "a tuple literal under a tuple hint reads it"
    # The list literal inside reads `lst`'s parameter, not `num`'s: the
    # mark stops at the call it is an argument of.
    assert read(lst_inner)
    assert not read(call), "a call does not read its slot through its argument"
    (pack,) = _args_of(modules, "var")
    star = pack.args[0]
    assert isinstance(star, TpyStarUnpack)
    assert read(star) and read(star.expr), "an unpacked list literal reads its slot"
    # `make` takes its type argument from `bx`'s parameter through the
    # method its value is the receiver of.
    chain, ctor = _args_of(modules, "bx")
    assert read(chain.obj), "a generic call reads its return hint"
    assert read(chain), "a method reads its slot through its receiver"
    assert read(method.args[0])
    assert not read(method), "a method does not read its slot through its argument"
    assert read(empty), "an empty generic constructor reads its slot"
    assert read(ctor), "a generic record construction reads its slot"
    # `over`'s int variant is the one the inferred `r` matches: its
    # parameter fills the tuple.
    (pair_arg,) = _args_of(modules, "over")
    assert read(pair_arg), "a tuple typed by an overload's fill reads it"


def test_mark_climbs_to_the_argument():
    """A node inside an argument marks every expression holding it up to the
    argument: the argument's analysis read the slot through it."""
    compiler, modules = _compile(_PROGRAM)
    analyzer = _entry(modules).analyzer
    (tup,) = _args_of(modules, "pair")
    inner = tup.elements[1]
    assert analyzer.expr.slot_was_read(inner)
    ctx = analyzer.ctx
    ctx.slot_reads.discard(tup)
    analyzer.expr._expr_stack.extend([tup])
    try:
        analyzer.expr.read_slot(inner)
    finally:
        analyzer.expr._expr_stack.pop()
    assert analyzer.expr.slot_was_read(tup)


def test_trial_marks_are_dropped_with_the_trial():
    compiler, modules = _compile(_PROGRAM)
    analyzer = _entry(modules).analyzer
    ctx = analyzer.ctx
    name = _args_of(modules, "num")[0]
    assert not analyzer.expr.slot_was_read(name)
    with ctx.trial_scope():
        analyzer.expr.read_slot(name)
        assert analyzer.expr.slot_was_read(name)
    assert not analyzer.expr.slot_was_read(name)
