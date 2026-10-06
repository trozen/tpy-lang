"""Pending numeric locals: the prescan's first bindings and pending set, the
join that settles a local from the values stored in it, the verdict on
sibling arms that bind a local together, and the refusals a list literal's
element cell words."""

import re
from textwrap import indent

import pytest

from .. import get_lib_dir
from ..compiler import Compiler
from ..codegen_cpp.context import ThirRejectError
from ..diagnostics import DiagnosticLevel, Scope, SemanticError
from ..parse import (Parser, SourceLocation, TpyArrayLiteral, TpyCoerce,
                     TpyFloatLiteral, TpyIntLiteral, TpyName, TpyStrLiteral,
                     TpyTupleLiteral, TpyVarDecl)
from ..prescan import (fold_int_constant, int_constant_too_wide,
                       literal_constant, scan_first_bindings,
                       scan_pending_num_locals)
from ..typesys import (BIGINT, ELEM, FLOAT, FLOAT32, INT8, INT16, INT32, INT64, STR,
                       UNKNOWN_ELEMENT, FloatLiteralType, IntLiteralType,
                       ListLiteralInfo, PendingListType, PendingNumType,
                       TupleType, TypeRegistry, UINT8, UINT32, UINT64,
                       make_list)
from .compatibility import TypeCompatibility
from .context import SemanticContext
from .pending_num import (NO_COMMON, PENDING_NUM_COERCION, ContainerCells,
                          PendingNumCell, PendingNums,
                          Entry, _splice_out, lub_int, pending_join,
                          tree_leaves)


def _pending(body: str, params: str = "") -> frozenset[str]:
    module = Parser().parse(f"def f({params}) -> None:\n"
                            + indent(body, "    ") + "\n")
    func = module.functions[0]
    params = [name for name, _ in func.params]
    return scan_pending_num_locals(
        func.body, params, scan_first_bindings(func.body, params)).pending


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("x = 0\nx = g()", {"x"}, id="literal-then-typed"),
        pytest.param("x = g()\nx = 0", set(), id="typed-then-literal"),
        pytest.param("x = -1\nx += g()", {"x"}, id="negated-and-aug"),
        pytest.param("x = 0\nx = 5\nx += 1", set(), id="literal-only"),
        pytest.param("x = 0\nx = 3000000000", {"x"}, id="literal-beyond-int32"),
        pytest.param("x = 1\nx += 10", set(), id="aug-by-constant-is-no-store"),
        pytest.param("x = 1\nx += 10**20", {"x"}, id="aug-by-wide-constant"),
        pytest.param("x = 1\nx += g()", {"x"}, id="aug-by-typed-value"),
        pytest.param("x = 10 + 5\nx = g()", {"x"}, id="constant-expression"),
        pytest.param("x = g()\nx = 10 + 5", set(), id="typed-then-constant"),
        pytest.param("x = 0\nx = 1 << 40", {"x"}, id="constant-beyond-int32"),
        pytest.param("x = 1\nx += 5", set(), id="aug-by-literal-is-no-store"),
        pytest.param("x = g()\nx += 5", set(), id="typed-then-aug-literal"),
        pytest.param("f = 0.1\nf = h()", {"f"}, id="float-literal"),
        pytest.param("f = h()\nf = 0.1", set(), id="typed-then-float-literal"),
        pytest.param("f = 0.1\nf = 1.5", set(), id="float-literal-only"),
        pytest.param("x = g()\nx = h()", set(), id="typed-only"),
        pytest.param("x: int = 0\nx = g()", set(), id="annotated"),
        pytest.param("x = 0\nfor x in xs:\n    pass\nx = g()", set(),
                     id="loop-target"),
        pytest.param("x = 0\nx, y = g()", set(), id="unpack"),
        pytest.param("x = 0\nprint(x := g())", set(), id="walrus"),
        pytest.param("x = 0\nwith g() as x:\n    pass", set(), id="with-target"),
        pytest.param("x = 0\ndef inner() -> None:\n    nonlocal x\n    x = g()\n"
                     "x = g()", set(), id="nonlocal-in-nested-def"),
        pytest.param("global x\nx = 0\nx = g()", set(), id="global"),
        pytest.param("x = 0\nif c:\n    x = g()", {"x"}, id="branch-store"),
        pytest.param("if c:\n    x = g()\nelse:\n    x = 0", {"x"},
                     id="literal-arm-beside-typed-arm"),
        pytest.param("if c:\n    x = g()\nelse:\n    x = h()\nx = 0", set(),
                     id="typed-arms-then-literal"),
    ],
)
def test_prescan_pending_set(body: str, expected: set[str]) -> None:
    assert _pending(body, "xs: list[int], c: bool") == expected


def test_prescan_excludes_parameters() -> None:
    assert _pending("n = 0\nn = g()", "n: int") == frozenset()


def _first(body: str, params: str = "") -> dict[str, list[int]]:
    module = Parser().parse(f"def f({params}) -> None:\n"
                            + indent(body, "    ") + "\n")
    func = module.functions[0]
    found = scan_first_bindings(func.body, [name for name, _ in func.params])
    return {n: [s.loc.line for s in ss] for n, ss in found.items()}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("x = 1\nx = g()", {"x": [2]}, id="sequential"),
        pytest.param("if c:\n    x = g()\nelse:\n    x = 1",
                     {"x": [3, 5]}, id="if-else"),
        pytest.param("if c:\n    x = 1\nelse:\n    x = g()",
                     {"x": [3, 5]}, id="if-else-other-order"),
        pytest.param("if c:\n    x = 1\nelif d:\n    x = g()\nelse:\n"
                     "    x = h()", {"x": [3, 5, 7]}, id="elif-chain"),
        pytest.param("if c:\n    x = 1", {"x": [3]}, id="one-arm"),
        pytest.param("x = 0\nif c:\n    x = g()\nelse:\n    x = 1",
                     {"x": [2]}, id="bound-before-the-arms"),
        pytest.param("if c:\n    x = 1\n    x = g()\nelse:\n    x = h()",
                     {"x": [3, 6]}, id="first-in-each-arm"),
        pytest.param("if c:\n    if d:\n        x = 1\n    else:\n"
                     "        x = g()\nelse:\n    x = h()",
                     {"x": [4, 6, 8]}, id="nested-if"),
        pytest.param("match c:\n    case 0:\n        x = 1\n    case _:\n"
                     "        x = g()", {"x": [4, 6]}, id="match"),
        pytest.param("try:\n    x = g()\nexcept E:\n    x = 1",
                     {"x": [3]}, id="try-body-first"),
        pytest.param("try:\n    pass\nexcept E:\n    x = 1\nexcept F:\n"
                     "    x = g()\nelse:\n    x = h()",
                     {"x": [5, 7, 9]}, id="handlers-and-else-together"),
        pytest.param("try:\n    pass\nfinally:\n    x = 1\nx = g()",
                     {"x": [5]}, id="finally-in-order"),
        pytest.param("for i in xs:\n    if c:\n        x = 1\n    else:\n"
                     "        x = g()\nx = h()",
                     {"i": [2], "x": [4, 6]}, id="loop-body-in-order"),
        pytest.param("ys = [y for y in xs]", {"ys": [2]},
                     id="comprehension-target-is-its-own"),
        pytest.param("c = 1", {}, id="parameter-is-bound-on-entry"),
        pytest.param("x: int = 0\nx = 1", {"x": [2]}, id="annotation"),
        pytest.param("def inner() -> None:\n    x = 1\nx = g()",
                     {"inner": [2], "x": [4]}, id="nested-def-body-own-scope"),
    ],
)
def test_prescan_first_bindings(body: str, expected: dict) -> None:
    assert _first(body, "xs: list[int], c: bool, d: bool") == expected


def _pend() -> PendingNums:
    ctx = SemanticContext(registry=TypeRegistry(), global_scope=Scope())
    return PendingNums(ctx, compat=None)  # type: ignore[arg-type]


def _arm_cell(pend: PendingNums, stores: list, derived: bool,
              is_float: bool = False):
    """A cell whose first binding is one store per arm, `stores` holding
    each arm's value type (a literal type for a bare literal)."""
    sites = [TpyVarDecl("x", None, None, loc=SourceLocation(3 + 2 * i, 0))
             for i in range(len(stores))]
    pend.ctx.func.first_bindings = {"x": tuple(sites)}
    cell = pend.new_cell("x", sites[0], derived=derived, is_float=is_float)
    for t, site in zip(stores, sites):
        literal = TpyFloatLiteral(1.0) if is_float else TpyIntLiteral(1)
        pend.record_arm_store(cell, t, literal, site)
        if isinstance(t, (IntLiteralType, FloatLiteralType)):
            pend.add_literal_store(cell, t, literal, site)
        else:
            pend.add_store(cell, t, site)
    return cell


@pytest.mark.parametrize("order", [1, -1], ids=["in-order", "reversed"])
@pytest.mark.parametrize(
    ("stores", "derived", "expected"),
    [
        pytest.param([INT8, INT16], True, INT16, id="typed-arms-join"),
        pytest.param([INT64, IntLiteralType(1)], False, INT64,
                     id="literal-beside-wider-arm"),
        pytest.param([BIGINT, IntLiteralType(1)], False, BIGINT,
                     id="literal-beside-int-arm"),
        pytest.param([INT64, IntLiteralType(3000000000)], False, BIGINT,
                     id="oversized-literal-is-int"),
        pytest.param([IntLiteralType(1), IntLiteralType(2)], False, INT32,
                     id="literal-arms"),
    ],
)
def test_arm_group_settles_whichever_arm_comes_first(
        stores: list, derived: bool, expected: object, order: int) -> None:
    pend = _pend()
    cell = _arm_cell(pend, stores[::order], derived)
    pend.settle({cell.cid})
    pend.check_arm_group(cell)
    assert cell.settled == expected


def test_arm_group_float_literal_beside_float_arm() -> None:
    pend = _pend()
    cell = _arm_cell(pend, [FLOAT, FloatLiteralType(0.5)], False, True)
    pend.settle({cell.cid})
    assert cell.settled == FLOAT


@pytest.mark.parametrize("order", [1, -1], ids=["in-order", "reversed"])
@pytest.mark.parametrize(
    ("stores", "derived", "message"),
    [
        pytest.param([INT8, IntLiteralType(1)], False,
                     r"'x' is int8 in one arm \(line \d\) and a bare literal "
                     r"in the other \(line \d\), so its type depends on which "
                     r"arm is read first; write int8\(1\), or annotate x: "
                     r"int8 at line 3", id="literal-beside-narrower-arm"),
        pytest.param([UINT32, IntLiteralType(1)], False,
                     r"'x' is uint32 in one arm .* and a bare literal",
                     id="literal-beside-unsigned-arm"),
        pytest.param([INT8, IntLiteralType(1000)], False,
                     r"is read first; annotate x: int16 at line 3$",
                     id="literal-past-the-narrower-arm"),
        pytest.param([UINT8, IntLiteralType(-1)], False,
                     r"is read first; annotate x: int16 at line 3$",
                     id="negative-literal-beside-unsigned-arm"),
        pytest.param([UINT64, IntLiteralType(-1)], False,
                     r"is read first; annotate x: int at line 3$",
                     id="literal-no-fixed-width-holds"),
        pytest.param([INT8, UINT32], True,
                     r"'x' is (int8|uint32) in one arm \(line \d\) and "
                     r"(uint32|int8) in the other \(line \d\), which have no "
                     r"common type; annotate x: int64 at line 3",
                     id="typed-arms-without-common-type"),
    ],
)
def test_arm_group_refusals(stores: list, derived: bool, message: str,
                            order: int) -> None:
    pend = _pend()
    cell = _arm_cell(pend, stores[::order], derived)
    with pytest.raises(SemanticError, match=message):
        pend.settle({cell.cid})
        pend.check_arm_group(cell)


@pytest.mark.parametrize("order", [1, -1], ids=["in-order", "reversed"])
@pytest.mark.parametrize(
    ("literal", "message"),
    [
        pytest.param(FloatLiteralType(0.5),
                     r"'x' is float32 in one arm \(line \d\) and a bare "
                     r"literal in the other \(line \d\), so its type depends "
                     r"on which arm is read first; write float32\(1\.0\), or "
                     r"annotate x: float32 at line 3", id="in-range"),
        pytest.param(FloatLiteralType(1e39),
                     r"is read first; annotate x: float at line 3$",
                     id="past-float32-range"),
    ],
)
def test_arm_group_float_literal_beside_float32_arm(
        literal: FloatLiteralType, message: str, order: int) -> None:
    pend = _pend()
    cell = _arm_cell(pend, [FLOAT32, literal][::order], False, True)
    with pytest.raises(SemanticError, match=message):
        pend.settle({cell.cid})
        pend.check_arm_group(cell)


def test_arm_group_judged_with_every_arm_after_an_early_settle() -> None:
    # The literal arm settled the local before the int8 arm was seen; the
    # int8 value fits, but the arms still disagree.
    pend = _pend()
    sites = [TpyVarDecl("x", None, None, loc=SourceLocation(3, 0)),
             TpyVarDecl("x", None, None, loc=SourceLocation(5, 0))]
    pend.ctx.func.first_bindings = {"x": tuple(sites)}
    cell = pend.new_cell("x", sites[0], derived=False)
    pend.record_arm_store(cell, IntLiteralType(1), TpyIntLiteral(1), sites[0])
    pend.add_literal_store(cell, IntLiteralType(1), TpyIntLiteral(1), sites[0])
    pend.settle({cell.cid}, use=TpyName("x"), what="a list element")
    pend.record_arm_store(cell, INT8, None, sites[1])
    pend.add_store(cell, INT8, sites[1])
    with pytest.raises(SemanticError, match="a bare literal in the other"):
        pend.check_arm_group(cell)


def test_typed_arm_group_refuses_a_wider_later_store() -> None:
    pend = _pend()
    cell = _arm_cell(pend, [INT8, INT16], True)
    pend.add_store(cell, INT64, TpyVarDecl("x", None, None))
    with pytest.raises(SemanticError, match="'x' is int16"):
        pend.settle({cell.cid})


def _elem_pend() -> PendingNums:
    """`_pend` with the compatibility checker an element refusal asks
    whether a literal fits."""
    ctx = SemanticContext(registry=TypeRegistry(), global_scope=Scope())
    compat = TypeCompatibility(ctx)
    compat.pend = PendingNums(ctx, compat)
    return compat.pend


def _a_list() -> ListLiteralInfo:
    """The record of a list literal whose cells a test makes by hand."""
    return ListLiteralInfo(literal_id=0, expr=TpyArrayLiteral([]),
                           element_type=UNKNOWN_ELEMENT, size=0)


def _one(pend: PendingNums, cell: PendingNumCell) -> ContainerCells:
    """The cells of a list of scalars whose one cell is `cell`."""
    return ContainerCells(cell.record,
                          PendingListType(pend.elem_leaf(cell), 0, 0),
                          (cell,), ((ELEM,),))


def _list_decl(pend: PendingNums, *values: int) -> TpyVarDecl:
    """`ys = [values...]` at line 3, its literals analyzed."""
    elements = [TpyIntLiteral(v) for v in values]
    for elem in elements:
        pend.ctx.set_expr_type(elem, IntLiteralType(elem.value))
    return TpyVarDecl("ys", None, TpyArrayLiteral(elements),
                      loc=SourceLocation(3, 0))


@pytest.mark.parametrize(
    ("resolved", "message"),
    [
        pytest.param(False,
                     r"^'ys' holds int32 elements, and it is passed here as "
                     r"list\[int8\], which would not hold 300$",
                     id="declared-container"),
        pytest.param(True,
                     r"which would not hold 300; the call's other arguments "
                     r"make the element int8: convert them to int32$",
                     id="generic-parameter"),
    ],
)
def test_list_at_a_container_that_would_not_hold_a_value(
        resolved: bool, message: str) -> None:
    pend = _elem_pend()
    cell = pend.new_elem_cell("ys", _a_list(), _list_decl(pend, 1, 300), False,
                              no_base=False, path=(ELEM,))
    refusal = pend.context_refusal(_one(pend, cell), make_list(INT8),
                                   "passed", None, resolved)
    assert re.search(message, refusal)


def test_list_at_two_containers_names_both() -> None:
    pend = _elem_pend()
    decl = _list_decl(pend, 1)
    cell = pend.new_elem_cell("ys", _a_list(), decl, False, no_base=False, path=(ELEM,))
    pend.add_store(cell, INT64, decl)
    pend.settle({cell.cid}, use=decl, what="passed as list[int64]")
    cell.context = (make_list(INT64), decl, "passed")
    assert pend.context_refusal(_one(pend, cell), make_list(INT32),
                                "passed") == (
        "'ys' is passed as list[int64] at line 3 and as list[int32] here; "
        "a list has one element type")
    # A literal the decided element holds still counts as its own type.
    wide = TpyIntLiteral(6000000000)
    with pytest.raises(SemanticError, match=(
            r"'ys' holds int64 elements since line 3 \(passed as "
            r"list\[int64\]\), and the literal 6000000000 counts as int; "
            r"annotate its first binding: ys: list\[int64\] = \[\.\.\.\]")):
        pend.elem_store(cell, IntLiteralType(6000000000), wide, decl)


def test_typed_seeded_list_is_decided_at_its_first_binding() -> None:
    pend = _elem_pend()
    decl = _list_decl(pend, 1)
    cell = pend.new_elem_cell("ys", _a_list(), decl, False, no_base=True, path=(ELEM,))
    pend.add_store(cell, INT8, decl)
    pend.decide_at_birth(cell, decl)
    assert cell.settled == INT8
    # A fitting literal adapts; one that does not is refused on the spot.
    pend.elem_store(cell, IntLiteralType(7), TpyIntLiteral(7), decl)
    with pytest.raises(SemanticError, match=(
            r"^'ys' holds int8 elements \(line 3\), and the literal 300 does "
            r"not fit int8; annotate its first binding: ys: list\[int32\] = ")):
        pend.elem_store(cell, IntLiteralType(300), TpyIntLiteral(300), decl)
    with pytest.raises(SemanticError, match=(
            r"^'ys' holds int8 elements \(line 3\), and this value is int64; "
            r"annotate its first binding: ys: list\[int64\] = ")):
        pend.elem_store(cell, INT64, None, decl)


def _empty_list(pend: PendingNums, name: str = "ys", literal_id: int = 0,
                source: int | None = None) -> PendingListType:
    """`name = []` at line 3 in the function under analysis, or, with
    `source`, a second name bound to that list's record."""
    expr = TpyArrayLiteral([])
    if source is not None:
        expr = pend.ctx.container_literals[source].expr
    else:
        pend.ctx.func.var_decl_by_name[name] = TpyVarDecl(
            name, None, expr, loc=SourceLocation(3, 0))
    pend.ctx.container_literals[literal_id] = ListLiteralInfo(
        literal_id=literal_id, expr=expr, element_type=UNKNOWN_ELEMENT,
        size=0, variable_name=name, source_literal_id=source)
    pend.ctx.func.pending_resolutions.append(literal_id)
    return PendingListType(UNKNOWN_ELEMENT, 0, literal_id)


def _at(line: int) -> TpyVarDecl:
    return TpyVarDecl("site", None, None, loc=SourceLocation(line, 0))


@pytest.mark.parametrize(
    ("seed", "store", "message"),
    [
        pytest.param(
            ("store", INT8), (INT64, None),
            r"^'ys' holds int8 elements \(line 4\), and this value is int64; "
            r"annotate its first binding: ys: list\[int64\] = \[\]$",
            id="typed-store-then-wider"),
        pytest.param(
            ("context", make_list(INT32)), (INT64, None),
            r"^'ys' holds int32 elements since line 4 \(passed as "
            r"list\[int32\]\), and this value is int64; the list is passed "
            r"as list\[int32\], so the value must be int32$",
            id="context-then-wider"),
        pytest.param(
            ("context", make_list(INT8)), (IntLiteralType(300), 300),
            r"^'ys' holds int8 elements since line 4 \(passed as "
            r"list\[int8\]\), and the literal 300 counts as int32; the list "
            r"is passed as list\[int8\], so the value must be int8$",
            id="context-then-literal"),
    ],
)
def test_empty_list_seeded_then_refused(seed: tuple, store: tuple,
                                        message: str) -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    kind, what = seed
    if kind == "store":
        assert pend.seed_by_store(ys, Entry(((what, None),)), _at(4)) is not None
    else:
        pend.seed_by_context(ys, what, _at(4), "passed")
    cell = pend.list_cell(ys)
    assert cell is not None and cell.settled is not None
    t, literal = store
    value = TpyIntLiteral(literal) if literal is not None else None
    with pytest.raises(SemanticError, match=message):
        pend.elem_store(cell, t, value, _at(5))


def test_empty_list_literal_store_leaves_the_element_open() -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    one = TpyIntLiteral(1)
    pend.ctx.set_expr_type(one, IntLiteralType(1))
    cell = pend.seed_by_store(ys, Entry(((IntLiteralType(1), one),)),
                              _at(4)).scalar
    assert cell is not None and cell.settled is None
    pend.elem_store(cell, INT64, None, _at(5))
    pend.settle({cell.cid})
    assert cell.settled == INT64


def test_empty_list_non_numeric_store_seeds_no_cell() -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    assert pend.seed_by_store(ys, Entry(((STR, None),)), _at(4)) is None
    assert pend.ctx.container_literals[0].elem_cells is None


def test_empty_list_cell_reaches_every_name_bound_to_it() -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    zs = _empty_list(pend, "zs", 1, source=0)
    # Seeded through the second name: both records share the cell, and the
    # diagnostics name the first binding.
    cell = pend.seed_by_store(zs, Entry(((INT8, None),)), _at(5)).scalar
    assert cell is not None and cell.name == "ys"
    assert pend.list_cell(ys) is cell and pend.list_cell(zs) is cell
    assert pend.seedable(ys) is None


_EMPTY_PRELUDE = """from tpy import int8, int32, int64, Own
def a8() -> int8: return 100
def a64() -> int64: return 1099511627776
def take8(v: list[int8]) -> None: pass
"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            "def main() -> None:\n    rs = []\n    rs.append(1)\n"
            "    take8(rs)\n",
            r"^'rs' holds int32 elements, and it is passed here as "
            r"list\[int8\]; annotate its first binding: "
            r"rs: list\[int8\] = \[\]$",
            id="literal-store-then-narrower-container"),
        pytest.param(
            "def main() -> None:\n    ys = []\n    ys.append(a8())\n"
            "    ys.append(300)\n",
            r"^'ys' holds int8 elements \(line 7\), and the literal 300 does "
            r"not fit int8; annotate its first binding: "
            r"ys: list\[int32\] = \[\]$",
            id="typed-store-then-wide-literal"),
        pytest.param(
            "def main() -> None:\n    ys = []\n    ys.append(1)\n"
            "    for v in ys:\n        print(v)\n    ys.append(a64())\n",
            r"^'ys' holds int32 elements since line 8 \(a loop iterable\), "
            r"and this value is int64; annotate its first binding: "
            r"ys: list\[int64\] = \[\]$",
            id="loop-then-wider"),
        pytest.param(
            "def main(x32: int32) -> Own[list[int64]]:\n    out = []\n"
            "    for i in range(3):\n        out.append(x32)\n"
            "    return out\n",
            r"^'out' holds int32 elements \(line 8\), and it is returned here "
            r"as list\[int64\]; annotate its first binding: "
            r"out: list\[int64\] = \[\]$",
            id="typed-store-then-wider-return"),
        # The capture decides the list: a nested body stores no wider value.
        pytest.param(
            "def main() -> None:\n    ys = []\n    ys.append(1)\n"
            "    def inner() -> None:\n        ys.append(a64())\n"
            "    inner()\n",
            r"^'ys' holds int32 elements since line 8 \(captured by the nested "
            r"function 'inner'\), and this value is int64; annotate its first "
            r"binding: ys: list\[int64\] = \[\]$",
            id="capture-then-wider"),
    ],
)
def test_empty_list_refusal_wording(body: str, message: str) -> None:
    """An empty list is refused in the words its literal twin is, with the
    hint spelling the empty first binding."""
    with pytest.raises(SemanticError, match=message):
        Compiler.from_source(_EMPTY_PRELUDE + body,
                             lib_dirs=[get_lib_dir() / "tpy"]).compile()


_LEAVES_PRELUDE = """from tpy import copy, int8, int32, int64
def a8() -> int8: return 100
def a64() -> int64: return 1099511627776
def pairs64(v: list[tuple[int64, int32]]) -> None: pass
def rows32(v: list[list[int32]]) -> None: pass
"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            "def main() -> None:\n    ps = [(a8(), 2)]\n"
            "    ps.append((a64(), 3))\n",
            r"^'ps' holds tuple\[int8, int32\] elements \(line 7\), and this "
            r"value is int64 \(tuple element 0\); annotate its first binding: "
            r"ps: list\[tuple\[int64, int32\]\] = \[\.\.\.\]$",
            id="typed-seeded-member"),
        pytest.param(
            "def main() -> None:\n    xs = [(1, 2)]\n    pairs64(xs)\n"
            "    xs.append((a64(), a64()))\n",
            r"^'xs' holds tuple\[int64, int32\] elements since line 8 \(passed "
            r"as list\[tuple\[int64, int32\]\]\), and this value is int64 "
            r"\(tuple element 1\); the list is passed as "
            r"list\[tuple\[int64, int32\]\], so the value must be int32$",
            id="member-context-then-wider"),
        pytest.param(
            "def main() -> None:\n    g = [[1, 2], [3]]\n    rows32(g)\n"
            "    g[1].append(a64())\n",
            r"^'g' holds list\[int32\] elements since line 8 \(passed as "
            r"list\[list\[int32\]\]\), and this value is int64 \(row "
            r"element\); the list is passed as list\[list\[int32\]\], so the "
            r"value must be int32$",
            id="row-context-then-wider"),
        # A list stored as a row is linked to it: widening the stored list
        # afterwards widens the rows, which the later context then meets.
        pytest.param(
            "def main() -> None:\n    g = [[1]]\n    other = [5]\n"
            "    g.append(other)\n    other.append(a64())\n    rows32(g)\n",
            r"^'g' holds list\[int64\] elements, and it is passed here as "
            r"list\[list\[int32\]\] \(row element\)",
            id="stored-row-linked-live"),
        # So is a list stored through a subscript.
        pytest.param(
            "def main() -> None:\n    g = [[1]]\n    other = [5]\n"
            "    g[0] = other\n    other.append(a64())\n    rows32(g)\n",
            r"^'g' holds list\[int64\] elements, and it is passed here as "
            r"list\[list\[int32\]\] \(row element\)",
            id="subscript-row-linked-live"),
        pytest.param(
            "def main() -> None:\n    g = [[a8()], [2]]\n"
            "    g[0].append(a64())\n",
            r"^'g' holds list\[int8\] elements \(line 7\), and this value is "
            r"int64 \(row element\); annotate its first binding: "
            r"g: list\[list\[int64\]\] = \[\.\.\.\]$",
            id="typed-seeded-row"),
        pytest.param(
            "def main() -> None:\n    g = [[a8()], [2]]\n"
            "    g[0] = [a64()]\n",
            r"^'g' holds list\[int8\] elements \(line 7\), and this value is "
            r"int64 \(row element\); annotate its first binding: "
            r"g: list\[list\[int64\]\] = \[\.\.\.\]$",
            id="typed-seeded-row-subscript-store"),
        # A typed list stored as a row is a container the rows meet, both
        # ways round; the hint names the whole list's element.
        pytest.param(
            "def main() -> None:\n    r: list[int32] = [1]\n    g = [[1]]\n"
            "    g.append(r)\n    g[0].append(a64())\n",
            r"^'g' holds list\[int32\] elements since line 9 \(stored as "
            r"list\[int32\]\), and this value is int64 \(row element\); the "
            r"list is stored as list\[int32\], so the value must be int32$",
            id="typed-row-then-wider"),
        pytest.param(
            "def main() -> None:\n    r: list[int32] = [1]\n    g = [[1]]\n"
            "    g[0] = r\n    g[0].append(a64())\n",
            r"^'g' holds list\[int32\] elements since line 9 \(stored as "
            r"list\[int32\]\), and this value is int64 \(row element\); the "
            r"list is stored as list\[int32\], so the value must be int32$",
            id="typed-row-subscript-store-then-wider"),
        # A row of another element type stored through a subscript is not a
        # store into the row element: the ordinary check refuses it.
        pytest.param(
            "def main() -> None:\n    g = [[1]]\n    g[0] = [\"a\"]\n",
            r"^Type mismatch in assignment: expected list\[int32\], got "
            r"list\[str\]$",
            id="mismatched-row-subscript-store"),
        pytest.param(
            "def main() -> None:\n    r: list[int32] = [1]\n"
            "    g = [[a64()]]\n    g.append(r)\n",
            r"^'g' holds list\[int64\] elements \(line 8\), and it is stored "
            r"here as list\[int32\] \(row element\), which would not hold "
            r"\.\.\.$",
            id="wide-rows-then-typed-row"),
        pytest.param(
            "def main() -> None:\n    r: list[int32] = [1]\n    g = [[1]]\n"
            "    g[0].append(a64())\n    g.append(r)\n",
            r"^'g' holds list\[int64\] elements, and it is stored here as "
            r"list\[int32\] \(row element\); annotate its first binding: "
            r"g: list\[list\[int32\]\] = \[\.\.\.\]$",
            id="widened-rows-then-typed-row"),
        # copy() decides the list it copies, as any generic call does.
        pytest.param(
            "def main() -> None:\n    g = [[1]]\n    other = [5]\n"
            "    g.append(copy(other))\n    other.append(a64())\n",
            r"^'other' holds int32 elements since line 9 \(an argument to "
            r"'copy\(\)'\), and this value is int64; annotate its first "
            r"binding: other: list\[int64\] = \[\.\.\.\]$",
            id="copy-then-wider"),
        pytest.param(
            "def main() -> None:\n    xs = [(1, 2)]\n    pairs64(xs)\n"
            "    xs.extend([(a64(), a64())])\n",
            r"^'xs' holds tuple\[int64, int32\] elements since line 8 \(passed "
            r"as list\[tuple\[int64, int32\]\]\), and this value is int64 "
            r"\(tuple element 1\); the list is passed as "
            r"list\[tuple\[int64, int32\]\], so the value must be int32$",
            id="member-context-then-wider-extend"),
        # A list's `remove` / `index` / `count` and `in` are lookups: a
        # value the element holds leaves it open
        # (`test_list_lookup_leaves_cells_open`); any other decides the list
        # first and is then checked as against a decided list, which refuses
        # a wider argument to the methods.
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    xs.remove(a64())\n",
            r"^Type mismatch in argument 'value': expected int32, got int64$",
            id="remove-wider-lookup"),
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    i = xs.index(a64())\n",
            r"^Type mismatch in argument 'value': expected int32, got int64$",
            id="index-wider-lookup"),
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    n = xs.count(a64())\n",
            r"^Type mismatch in argument 'value': expected int32, got int64$",
            id="count-wider-lookup"),
        # A looked-up value the element does not hold -- a wider width, an
        # `int`, the other family, a part that is no single number --
        # decides the list first and is compared, never converted down.
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    b = a64() in xs\n"
            "    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 8 \(an operand of "
            r"'in'\), and this value is int64",
            id="in-wider-decides"),
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    n: int = 5\n"
            "    b = n in xs\n    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 9 \(an operand of "
            r"'in'\), and this value is int64",
            id="in-bigint-decides"),
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    n: int = 5\n"
            "    k = xs.count(n)\n    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 9 \(a call to "
            r"'count\(\)'\), and this value is int64",
            id="count-bigint-decides"),
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    b = 1.5 in xs\n"
            "    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 8 \(an operand of "
            r"'in'\), and this value is int64",
            id="in-other-family-decides"),
        pytest.param(
            "def main() -> None:\n    xs = [(1, 2)]\n    b = (1, 2) in xs\n"
            "    xs.append((a64(), 3))\n",
            r"^'xs' holds tuple\[int32, int32\] elements since line 8 \(an "
            r"operand of 'in'\)",
            id="in-tuple-element-decides"),
        # An `or` whose value is bound (not a truth test) is an alias the
        # list is decided at.
        pytest.param(
            "def main() -> None:\n    ys = [1]\n    other = [2]\n"
            "    zs = ys or other\n    ys.append(a64())\n",
            r"^'ys' holds int32 elements since line 9 \(an operand of "
            r"'or'\), and this value is int64",
            id="or-value-decides"),
        # `sort` bounds the element (`def sort[T: Comparable]`) and declares
        # no effect: it decides the list first.
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    xs.sort()\n"
            "    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 8 \(a call to "
            r"'sort\(\)'\), and this value is int64",
            id="sort-decides"),
    ],
)
def test_leaf_refusal_wording(body: str, message: str) -> None:
    """A tuple member and a row element are refused in the words a scalar
    element is, naming the member and spelling the whole element."""
    with pytest.raises(SemanticError, match=message):
        Compiler.from_source(_LEAVES_PRELUDE + body,
                             lib_dirs=[get_lib_dir() / "tpy"]).compile()


def _tuple_literal(pend: PendingNums, *members) -> TpyTupleLiteral:
    for m in members:
        t = (IntLiteralType(m.value) if isinstance(m, TpyIntLiteral)
             else STR)
        pend.ctx.set_expr_type(m, t)
    return TpyTupleLiteral(list(members))


def test_non_numeric_member_takes_no_cell() -> None:
    pend = _elem_pend()
    one, a = TpyIntLiteral(1), TpyStrLiteral("a")
    value = _tuple_literal(pend, one, a)
    tree = pend.new_tree("ms", _a_list(), None,
                         [Entry(((TupleType((IntLiteralType(1), STR)), value),))],
                         None, _at(3))
    member = tree.element_type
    assert isinstance(member, TupleType) and member.element_types[1] == STR
    [(path, leaf)] = tree_leaves(tree)
    assert path == (ELEM, 0)
    assert pend.ctx.pending_num_cells[min(leaf.cells)].path == (ELEM, 0)


def test_empty_list_seeded_by_a_tuple() -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    one = TpyIntLiteral(1)
    pend.ctx.set_expr_type(one, IntLiteralType(1))
    lc = pend.seed_by_store(ys, Entry(((TupleType((IntLiteralType(1), INT8)),
                                        TpyTupleLiteral([one, TpyName("k")])),)),
                            _at(4))
    assert lc is not None and lc.scalar is None
    literal, typed = lc.cells
    # Each member is born as a list of scalars is: the literal one open at
    # the default, the typed one decided at the store.
    assert literal.settled is None and literal.path == (ELEM, 0)
    assert typed.settled == INT8 and typed.path == (ELEM, 1)
    pend.elem_store(literal, INT64, None, _at(5))
    pend.settle({literal.cid})
    assert literal.settled == INT64


def test_empty_list_seeded_by_a_row() -> None:
    pend = _elem_pend()
    ys = _empty_list(pend)
    one = TpyIntLiteral(1)
    pend.ctx.set_expr_type(one, IntLiteralType(1))
    row = TpyArrayLiteral([one])
    pend.ctx.container_literals[1] = ListLiteralInfo(
        literal_id=1, expr=row, element_type=IntLiteralType(1), size=1)
    pend.ctx.func.pending_resolutions.append(1)
    lc = pend.seed_by_store(
        ys, Entry(((PendingListType(IntLiteralType(1), 1, 1), row),)), _at(4))
    assert lc is not None
    # The row literal takes the row's cell; the two are one row group.
    row_info = pend.ctx.container_literals[1]
    assert pend.list_cell(PendingListType(row_info.element_type, 1, 1)) \
        is lc.cells[0]
    assert lc.cells[0].path == (ELEM, ELEM)
    assert row_info.row_group == [1]


def test_float_list_at_an_int_container_names_no_fix() -> None:
    pend = _elem_pend()
    cell = pend.new_elem_cell("fs", _a_list(), None, True, no_base=False, path=(ELEM,))
    assert pend.context_refusal(_one(pend, cell), make_list(INT64),
                                "passed") == (
        "'fs' holds float values, and it is passed here as list[int64]")


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        pytest.param([INT32, UINT8], INT32, id="unsigned-into-wider-signed"),
        pytest.param([INT32, UINT32], NO_COMMON, id="no-common-type"),
        pytest.param([INT32, UINT32, INT64], INT64, id="three-way"),
        pytest.param([INT64, UINT32, INT32], INT64, id="three-way-reordered"),
        pytest.param([INT32, BIGINT], BIGINT, id="int-absorbs"),
        pytest.param([None, INT64], INT64, id="no-evidence"),
        pytest.param([], None, id="empty"),
    ],
)
def test_lub(types: list, expected: object) -> None:
    assert lub_int(types) is expected or lub_int(types) == expected


def test_pending_join_keeps_cells_and_floor() -> None:
    p = PendingNumType(frozenset({1}))
    assert pending_join(p, INT64) == PendingNumType(frozenset({1}), INT64)
    assert pending_join(p, BIGINT) == BIGINT
    assert pending_join(PendingNumType(frozenset({1}), UINT32), INT32) is None


def test_settle_unsigned_evidence_joins_default() -> None:
    pend = _pend()
    cell = pend.new_cell("n", None, derived=False)
    pend.add_literal_store(cell, IntLiteralType(0), None, None)
    pend.add_store(cell, UINT8, None)
    pend.settle({cell.cid})
    assert cell.settled == INT32


def test_settle_unsigned_evidence_without_common_type() -> None:
    pend = _pend()
    cell = pend.new_cell("n", None, derived=False)
    pend.add_literal_store(cell, IntLiteralType(0), None, None)
    pend.add_store(cell, UINT32, None)
    with pytest.raises(SemanticError, match="no common type"):
        pend.settle({cell.cid})


def test_settle_literal_beyond_default() -> None:
    pend = _pend()
    cell = pend.new_cell("n", None, derived=False)
    pend.add_literal_store(cell, IntLiteralType(0), None, None)
    pend.add_literal_store(cell, IntLiteralType(3000000000), None, None)
    pend.settle({cell.cid})
    assert cell.settled == BIGINT


def test_settle_float_literal_joins_float() -> None:
    pend = _pend()
    cell = pend.new_cell("f", None, derived=False, is_float=True)
    pend.add_store(cell, FLOAT32, None)
    pend.settle({cell.cid})
    assert cell.settled == FLOAT


def test_derived_cell_follows_its_first_store() -> None:
    pend = _pend()
    steps = pend.new_cell("steps", None, derived=False)
    pend.add_literal_store(steps, IntLiteralType(0), None, None)
    j = pend.new_cell("j", None, derived=True)
    pend.add_store(j, PendingNumType(frozenset({steps.cid})), None)
    pend.add_store(steps, INT64, None)
    pend.settle({j.cid})
    assert (steps.settled, j.settled) == (INT64, INT64)


def test_derived_cell_refuses_a_wider_later_store() -> None:
    pend = _pend()
    steps = pend.new_cell("steps", None, derived=False)
    pend.add_literal_store(steps, IntLiteralType(0), None, None)
    j = pend.new_cell("j", None, derived=True)
    pend.add_store(j, PendingNumType(frozenset({steps.cid})), None)
    pend.add_store(j, BIGINT, None)
    with pytest.raises(SemanticError, match="'j' is int32"):
        pend.settle({j.cid})


def test_settled_cell_refuses_a_wider_store_naming_the_use() -> None:
    pend = _pend()
    n = pend.new_cell("n", None, derived=False)
    pend.add_literal_store(n, IntLiteralType(0), None, None)
    pend.settle({n.cid}, use=TpyName("n"), what="a list element")
    with pytest.raises(SemanticError, match=r"'n' was used as int32 \(a list element\)"):
        pend.add_store(n, INT64, None)


def _const(src: str):
    module = Parser().parse(f"def f() -> None:\n    x = {src}\n")
    return literal_constant(module.functions[0].body[0].init)


def test_one_folder_folds_a_large_right_shift() -> None:
    # The prescan and sema fold with one function, so they agree.
    assert _const("1 >> 20000").value == 0
    assert fold_int_constant(">>", 1, 20000) == 0


def test_folder_leaves_a_power_chain_unfolded() -> None:
    const = _const("((2 ** 10000) ** 10000) ** 10000")
    assert const is not None and const.value is None
    assert fold_int_constant("**", 2, 5000) is None
    assert int_constant_too_wide("**", 2, 5000)
    assert fold_int_constant("**", 2, 100) == 2 ** 100


def test_resolve_ready_waits_out_an_overload_trial() -> None:
    # A deferred operation resolved inside a trial would be rolled back with
    # it and never resolved again; it waits for the first call outside.
    pend = _pend()
    p = pend.new_cell("p", None, derived=False)
    pend.add_literal_store(p, IntLiteralType(1), None, None)
    resolved: list[tuple] = []
    pend.defer(None, (PendingNumType(frozenset({p.cid})),), resolved.append)
    pend.settle({p.cid})
    pend.ctx.trial_depth = 1
    pend.resolve_ready()
    assert resolved == [] and len(pend.ctx.func.pending_num_deferred) == 1
    pend.ctx.trial_depth = 0
    pend.resolve_ready()
    assert resolved == [(INT32,)] and pend.ctx.func.pending_num_deferred == []


def _placeholder(line: int) -> TpyCoerce:
    return TpyCoerce(expr=TpyName("v"), actual_type=INT32, expected_type=INT64,
                     coercion=PENDING_NUM_COERCION, context_kind=None,
                     context_msg="", loc=SourceLocation(line=line))


def _body_holding(init: object) -> list:
    module = Parser().parse("def f() -> None:\n    x = v\n")
    body = module.functions[0].body
    body[0].init = init
    return body


def test_splice_out_unwraps_a_placeholder_the_body_holds() -> None:
    held = _placeholder(2)
    body = _body_holding(held)
    _splice_out(body, [held])
    assert body[0].init is held.expr


def test_splice_out_rebuilds_a_tuple_holding_a_placeholder() -> None:
    # A tuple field cannot be written in place, so the walk stores a new one.
    module = Parser().parse("def f() -> None:\n"
                            "    x = sum(v for v in range(3))\n")
    genexpr = module.functions[0].body[0].init.args[0]
    held = _placeholder(2)
    genexpr.frame_range_args = (TpyName("a"), held)
    _splice_out(module.functions[0].body, [held])
    assert isinstance(genexpr.frame_range_args, tuple)
    assert genexpr.frame_range_args[1] is held.expr


def test_splice_out_refuses_a_placeholder_held_outside_the_body() -> None:
    body = _body_holding(TpyName("v"))
    with pytest.raises(AssertionError,
                       match="placeholder at line 7 is held where"):
        _splice_out(body, [_placeholder(7)])


def test_assert_settled_refuses_a_surviving_placeholder() -> None:
    pend = _pend()
    pend.new_cell("p", None, derived=False)
    with pytest.raises(AssertionError,
                       match="conversion at line 4 was left unresolved"):
        pend.assert_settled(_body_holding(_placeholder(4)))


_DICT_SET_PRELUDE = """from tpy import int8, int32, int64
def a8() -> int8: return 100
def a64() -> int64: return 1099511627776
def take32(d: dict[str, int32]) -> None: pass
"""


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n    take32(d)\n"
            "    d['c'] = a64()\n",
            r"^'d' holds int32 values since line 7 \(passed as "
            r"dict\[str, int32\]\), and this value is int64; the dict is "
            r"passed as dict\[str, int32\], so the value must be int32$",
            id="dict-context-then-wider"),
        pytest.param(
            "def main() -> None:\n    d = {}\n    d['a'] = a8()\n"
            "    d['b'] = a64()\n",
            r"^'d' holds int8 values \(line 7\), and this value is int64; "
            r"annotate its first binding: d: dict\[str, int64\] = \{\}$",
            id="empty-dict-typed-first-store"),
        pytest.param(
            "def main() -> None:\n    s = {a8()}\n    s.add(a64())\n",
            r"^'s' holds int8 elements \(line 6\), and this value is int64; "
            r"annotate its first binding: s: set\[int64\] = \{\.\.\.\}$",
            id="set-typed-seed"),
        pytest.param(
            "def main() -> None:\n    s = {1}\n    for v in s:\n"
            "        print(v)\n    s.add(a64())\n",
            r"^'s' holds int32 elements since line 7 \(a loop iterable\), "
            r"and this value is int64; annotate its first binding: "
            r"s: set\[int64\] = \{\.\.\.\}$",
            id="set-loop-then-wider"),
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n"
            "    print(d.get('b', a64()))\n",
            r"^Type mismatch in argument 'default': expected int32, got "
            r"int64$",
            id="wider-get-default"),
        pytest.param(
            "def main() -> None:\n    k = {1: 'x'}\n    print(a64() in k)\n",
            r"^Type mismatch in membership test \(expected int32\): "
            r"expected int32, got int64$",
            id="wider-lookup-key"),
        pytest.param(
            "def main() -> None:\n    s = {1}\n    print(a64() in s)\n",
            r"^Type mismatch in membership test \(expected int32\): "
            r"expected int32, got int64$",
            id="set-wider-in"),
        # An `int` looked up decides the dict or set first. `in` then
        # compares it as it is; `discard` checks it against the decided
        # element as a value to store, narrowing it with a check that
        # panics on a value the element cannot hold
        # (BUGS.md#list-lookup-method-arg-converts).
        pytest.param(
            "def main() -> None:\n    k = {1: 'x'}\n    n: int = 5\n"
            "    b = n in k\n    k[a64()] = 'y'\n",
            r"^Type mismatch in dict key: expected int32, got int64$",
            id="dict-bigint-in-decides"),
        pytest.param(
            "def main() -> None:\n    s = {1}\n    n: int = 5\n"
            "    b = n in s\n    s.add(a64())\n",
            r"^'s' holds int32 elements since line 8 \(an operand of 'in'\)",
            id="set-bigint-in-decides"),
        pytest.param(
            "def main() -> None:\n    s = {1}\n    n: int = 5\n"
            "    s.discard(n)\n    s.add(a64())\n",
            r"^'s' holds int32 elements since line 8 \(a call to "
            r"'discard\(\)'\)",
            id="set-bigint-discard-decides"),
        pytest.param(
            "def main() -> None:\n    d1 = {'a': 1}\n    d2 = {'b': a64()}\n"
            "    z = d1 if len(d1) > 0 else d2\n",
            r"^Incompatible types in ternary expression: 'dict\[str, int32\]'"
            r" and 'dict\[str, int64\]'$",
            id="select-of-two-cell-dicts"),
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n    d = {'b': 1.5}\n",
            r"^'d' is bound to int elements at line 6 and to float elements "
            r"here",
            id="int-dict-rebound-to-floats"),
        # An int stored into, or defaulted out of, a float container mixes
        # the families.
        pytest.param(
            "def main() -> None:\n    d = {'a': 0.5}\n    d['b'] = 1\n",
            r"^dict 'd' mixes int and float values, and CPython keeps each "
            r"value's own type; write 1\.0 instead of 1, or annotate the "
            r"container, e\.g\. d: dict\[str, float\] = \{\.\.\.\}$",
            id="int-store-into-float-dict"),
        pytest.param(
            "def main() -> None:\n    s = {0.5}\n    s.add(1)\n",
            r"^set 's' mixes int and float values, and CPython keeps each "
            r"value's own type; write 1\.0 instead of 1, or annotate the "
            r"container, e\.g\. s: set\[float\] = \{\.\.\.\}$",
            id="int-add-into-float-set"),
        pytest.param(
            "def main() -> None:\n    fd = {'a': 0.5}\n"
            "    print(fd.get('b', 0))\n",
            r"^dict 'fd' mixes int and float values, and CPython keeps each "
            r"value's own type; write 0\.0 instead of 0",
            id="int-get-default-of-float-dict"),
        pytest.param(
            "def main() -> None:\n    s = set()\n    s.add(a8())\n"
            "    s.add(a64())\n",
            r"^'s' holds int8 elements \(line 7\), and this value is int64; "
            r"annotate its first binding: s: set\[int64\] = set\(\)$",
            id="empty-set-typed-first-add"),
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n"
            "    for v in d.values():\n        print(v)\n"
            "    d['b'] = a64()\n",
            r"^'d' holds int32 values since line 7 \(a loop iterable\), and "
            r"this value is int64; annotate its first binding: "
            r"d: dict\[str, int64\] = \{\.\.\.\}$",
            id="values-loop-then-wider"),
        pytest.param(
            "def main() -> None:\n    d1 = {'a': 1}\n    d2 = {'b': 2}\n"
            "    z = d1 if len(d1) > 0 else d2\n    print(z)\n"
            "    d1['c'] = a64()\n",
            r"^'d1' holds int32 values since line 8 \(a conditional "
            r"expression arm\), and this value is int64",
            id="select-of-two-cell-dicts-then-wider"),
        pytest.param(
            "def main() -> None:\n    k = {1: 'x'}\n    print(k[a64()])\n",
            r"^Type mismatch in dict key: expected int32, got int64$",
            id="wider-subscript-key"),
        pytest.param(
            "def main() -> None:\n    d = {1: 'x'}\n    print(d.get(a64()))\n",
            r"^No matching overload for 'get' with argument types \(int64\)$",
            id="wider-get-key"),
        # An update's source rows must be the receiver's: no conversion makes
        # one C++ list type of another.
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n"
            "    print(d.pop('x', a64()))\n",
            r"^Type mismatch in argument 'default': expected int32, got "
            r"int64$",
            id="wider-pop-default"),
        # Each stub method's declared effect: a looked-up argument must fit
        # the cells (a fitting one leaves them open:
        # `test_dict_set_effect_leaves_cells_open`), an inserted one widens
        # them, which a typed container met afterwards shows.
        pytest.param(
            "def main() -> None:\n    s = {1}\n    s.discard(a64())\n",
            r"^Type mismatch in argument 'value': expected int32, got int64$",
            id="set-discard-lookup"),
        pytest.param(
            "def main() -> None:\n    s = {1}\n    s.remove(a64())\n",
            r"^Type mismatch in argument 'value': expected int32, got int64$",
            id="set-remove-lookup"),
        pytest.param(
            "def main() -> None:\n    d = {1: 'x'}\n    del d[a64()]\n",
            r"^Type mismatch in dict key: expected int32, got int64$",
            id="dict-delitem-lookup"),
        # `update` from a dict literal as an empty dict's first evidence
        # stops with today's ordinary mismatch against the still-unknown
        # parts; pinned so a change to it is seen.
        pytest.param(
            "def main() -> None:\n    d = {}\n    d.update({'a': 'b'})\n",
            r"^Type mismatch in argument 'other': expected \?\?\?, got str$",
            id="empty-dict-update-from-literal"),
        # A list's `__delitem__` takes an index, no element to look up: the
        # delete decides the list first.
        pytest.param(
            "def main() -> None:\n    xs = [1, 2]\n    del xs[0]\n"
            "    xs.append(a64())\n",
            r"^'xs' holds int32 elements since line 7 \(a 'del' key\), and "
            r"this value is int64",
            id="list-del-decides"),
        pytest.param(
            "def takeset32(s: set[int32]) -> None: pass\n"
            "def main() -> None:\n    s = {1}\n"
            "    s.symmetric_difference_update({a64()})\n    takeset32(s)\n",
            r"^'s' holds int64 elements, and it is passed here as set\[int32\]",
            id="set-symmetric-difference-update-inserts"),
        pytest.param(
            "def takeset32(s: set[int32]) -> None: pass\n"
            "def main() -> None:\n    s = {1}\n    s ^= {a64()}\n"
            "    takeset32(s)\n",
            r"^'s' is stored as set\[int64\] at line 8 and passed as "
            r"set\[int32\] here",
            id="set-ixor-inserts"),
        pytest.param(
            "def main() -> None:\n    d = {'a': 1}\n    d |= {'b': a64()}\n"
            "    take32(d)\n",
            r"^'d' holds int64 values, and it is passed here as "
            r"dict\[str, int32\]",
            id="dict-ior-inserts"),
        pytest.param(
            "def takelist32(xs: list[int32]) -> None: pass\n"
            "def main() -> None:\n    xs = [1]\n    xs.insert(0, a64())\n"
            "    takelist32(xs)\n",
            r"^'xs' holds int64 elements, and it is passed here as "
            r"list\[int32\]",
            id="list-insert-inserts"),
        # A stub method that names the element and declares no effect
        # decides the element first.
        pytest.param(
            "def main() -> None:\n    s = {1}\n"
            "    s.intersection_update({1})\n    s.add(a64())\n",
            r"^'s' holds int32 elements since line 7 \(a call to "
            r"'intersection_update\(\)'\), and this value is int64",
            id="set-undeclared-method-decides"),
        pytest.param(
            "def main() -> None:\n    g = {'a': [1]}\n    g['c'] = [a64()]\n"
            "    h: dict[str, list[int32]] = {'z': [3]}\n    g.update(h)\n",
            r"^'g' holds list\[int64\] values, and it is stored here as "
            r"list\[int32\] \(row element\)",
            id="update-from-narrower-typed-rows"),
    ],
)
def test_dict_set_refusal_wording(body: str, message: str) -> None:
    """A dict or set literal's leaves are refused in the words a list's
    element is, naming the key, value or element."""
    with pytest.raises(SemanticError, match=message):
        Compiler.from_source(_DICT_SET_PRELUDE + body,
                             lib_dirs=[get_lib_dir() / "tpy"]).compile()


def test_tuple_store_into_a_cell_dict_copies_per_member() -> None:
    """A tuple literal stored into a dict whose value cells are still open
    copies its reference members as a declared dict's store does."""
    source = _DICT_SET_PRELUDE + """class P:
    def __init__(self, x: int) -> None:
        self.x = x
def main() -> None:
    t = {"a": (P(1), 1)}
    w = P(5)
    t["b"] = (w, 2)
    w.x = 50
"""
    comp = Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"])
    # The store itself is not lowered yet
    # (BUGS.md#setitem-dict-tuple-ref-member); the warning is sema's.
    try:
        comp.compile()
    except ThirRejectError:
        pass
    entry = next(m for m in comp.modules.values() if m.is_entry_point)
    warnings = [d.message for d in entry.analyzer.diagnostics
                if d.level is DiagnosticLevel.WARNING]
    assert any(w.startswith("copies P into container (tuple element 0)")
               for w in warnings), warnings


@pytest.mark.parametrize("body", [
    pytest.param("    s = {1}\n    s.discard(1)\n    s.add(a64())\n",
                 id="set-discard"),
    pytest.param("    s = {1}\n    s.remove(1)\n    s.add(a64())\n",
                 id="set-remove"),
    # `del d[k]` is the stub's `__delitem__`, a lookup of the key (a list's
    # `del xs[i]` takes an index and still decides the list first).
    pytest.param("    d = {1: 2}\n    del d[1]\n    d[a64()] = a64()\n",
                 id="dict-del-lookup"),
    pytest.param("    d = {1: 2}\n    b = a8() in d\n    n = d[1]\n"
                 "    m = d.get(a8())\n    d[a64()] = a64()\n",
                 id="dict-in-getitem-get"),
    pytest.param("    s = {1}\n    b = 2 in s\n    s.add(a64())\n",
                 id="set-in"),
    # A typed value of the part's own width is held losslessly.
    pytest.param("    n: int32 = 1\n    d = {1: 2}\n    b = n in d\n"
                 "    m = d.get(n)\n    d[a64()] = a64()\n",
                 id="dict-equal-typed-key"),
    pytest.param("    n: int32 = 1\n    s = {1}\n    b = n in s\n"
                 "    s.discard(n)\n    s.add(a64())\n",
                 id="set-equal-typed-element"),
])
def test_dict_set_effect_leaves_cells_open(body: str) -> None:
    """A looked-up argument that fits leaves the cells open: a wider store
    after it widens them."""
    Compiler.from_source(_DICT_SET_PRELUDE + "def main() -> None:\n" + body,
                         lib_dirs=[get_lib_dir() / "tpy"]).compile()


@pytest.mark.parametrize("body", [
    pytest.param("    xs.remove(1)\n    xs.remove(a8())\n", id="remove"),
    pytest.param("    i = xs.index(2)\n    j = xs.index(a8())\n", id="index"),
    pytest.param("    n = xs.count(2)\n    m = xs.count(a8())\n", id="count"),
    pytest.param("    b = 2 in xs\n    c = a8() not in xs\n", id="in"),
    pytest.param("    n: int32 = 2\n    b = n in xs\n    k = xs.count(n)\n"
                 "    xs.remove(n)\n", id="equal-typed"),
    # A literal-seeded local reaches the lookup as an int32 already, so it
    # is an equal-typed operand, not a pending one, and the list stays
    # open; pinned so a change to either rule is seen.
    pytest.param("    x = 1\n    b = x in xs\n    k = xs.count(x)\n",
                 id="literal-seeded-local"),
    # A truth test reads the length only, and so does a list operand of
    # an `and` / `or` that is one.
    pytest.param("    if xs:\n        print(1)\n", id="truth-test"),
    pytest.param("    c = len(xs) > 1\n    if xs and c:\n        print(1)\n"
                 "    while c or xs:\n        c = False\n        break\n",
                 id="and-or-truth-operand"),
])
def test_list_lookup_leaves_cells_open(body: str) -> None:
    """A list's lookup of a fitting value (a literal, a narrower typed
    value) and a truth test leave its element open: a wider store after
    them widens it."""
    source = (_DICT_SET_PRELUDE + "def main() -> None:\n    xs = [1, 2, 3]\n"
              + body + "    xs.append(a64())\n")
    Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"]).compile()
