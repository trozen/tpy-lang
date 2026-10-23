"""Assembly of an emitted body: region handles, borrow-operation handles and
the canonical numbering are fixed only once the whole body is built."""

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import BOOL, INT32, STR, VoidType, return_representation
from .emit import Fragment, MIRRegionStorageError, MIRValue, assemble, owned_leaf_shape
from .nodes import MIRBodyId, MIRBodyKind, MIRConstant, MIRGoto, MIRPlace, MIRRead, MIRReturn, MIRSlotKind
from ..thir.scalar_leaves import MIRClass
from .testutil import records_of, bound
from .value import MIRRole, MIRShape, MIRValidationError

BODY = MIRBodyId("main", "f")
LEAF = MIRShape(INT32, MIRClass.LEAF)


def _emitter() -> Fragment:
    return Fragment(body=BODY)


def _build(body: Fragment, void: bool = True, parameter_bindings: tuple = ()):
    return_type = VoidType() if void else INT32
    fn, _first = assemble(body, void=void, return_type=return_type, kind=MIRBodyKind.FREE_FUNCTION,
                          callees=(), result=None, layouts=records_of(), parameter_bindings=parameter_bindings,
                          return_representation=return_representation(return_type))
    return fn


def test_a_dissolved_region_takes_its_parent_region() -> None:
    emitter = _emitter()
    inner = emitter.block()
    emitter.end(MIRGoto(inner.id))
    emitter.current = inner
    with emitter.scope():
        scoped = emitter.region
        with emitter.dissolved():
            # A full expression owning no temporaries: its slots live in the scope around it.
            temporary = emitter.leaf(INT32, MIRConstant(1), None)
        assert emitter.records[temporary].residence.resolved() is scoped
        emitter.end(MIRReturn())
    fn = _build(emitter)
    slot, = fn.slots
    nested, = (r for r in fn.regions if r.parent is not None)
    assert slot.residence == nested.id and len(fn.regions) == 2


def test_a_pruned_arm_keeps_a_distinct_point_absent_from_the_body() -> None:
    emitter = _emitter()
    condition = emitter.leaf(BOOL, MIRConstant(True), None)
    yes, no = emitter.block(), emitter.block()
    emitter.branch(condition, yes.id, no.id, None)
    taken, pruned = object(), object()
    for block, operation in ((yes, taken), (no, pruned)):
        emitter.current = block
        emitter.borrow_operation(operation)
        emitter.end(MIRReturn())
    fn = _build(emitter)
    held = {block.id for block in fn.blocks}
    (kept,), (gone,) = emitter.borrow_operations[taken], emitter.borrow_operations[pruned]
    # Both arms are numbered depth-first; only the taken one stays in the body.
    assert kept.block in held and gone.block not in held and kept != gone
    assert [b.id.index for b in fn.blocks] == [0, 1] and gone.block.index == 2


def test_an_unresolvable_borrow_handle_fails_assembly() -> None:
    emitter = _emitter()
    emitter.borrow_operation(object())
    # The block the operation waits in is left without a statement or a terminator.
    emitter.current = emitter.block()
    with pytest.raises(MIRValidationError, match="borrow operation names no statement"):
        _build(emitter)


def test_parameters_keep_signature_order() -> None:
    # The body reads the last parameter first; the parameters still come first, in signature order.
    emitter = _emitter()
    a, b, c = (emitter.adopt(emitter.record(LEAF, MIRSlotKind.PARAMETER, name))
               for name in "abc")
    value = emitter.fragment()
    value.end(MIRReturn(value.leaf(INT32, MIRRead(MIRPlace(c)), None)))
    emitter.splice(value)
    fn = _build(emitter, void=False, parameter_bindings=tuple(bound(INT32, ParamPassing.VALUE) for _ in "abc"))
    parameters = [s for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert [(s.name, s.id.index) for s in parameters] == [("a", 0), ("b", 1), ("c", 2)]
    assert [s.id.index for s in fn.slots] == [0, 1, 2, 3]


def _two_reads(flip: bool):
    """One body built with its slots and blocks allocated in either order."""
    emitter = _emitter()
    first, second = emitter.slot(LEAF), emitter.slot(LEAF)
    if flip:
        first, second = second, first
    exit_, middle = emitter.block(), emitter.block()
    if flip:
        exit_, middle = middle, exit_
    emitter.write(first, MIRConstant(1), None)
    emitter.end(MIRGoto(middle.id))
    emitter.current = middle
    emitter.write(second, MIRRead(MIRPlace(first)), None)
    emitter.end(MIRGoto(exit_.id))
    emitter.current = exit_
    emitter.end(MIRReturn())
    return _build(emitter)


def test_canonical_numbering_is_walk_order_independent() -> None:
    plain, flipped = _two_reads(False), _two_reads(True)
    assert plain == flipped
    assert [s.id.index for s in plain.slots] == [0, 1] and [b.id.index for b in plain.blocks] == [0, 1, 2]


def test_a_refusal_reaches_the_body_only_with_its_fragment() -> None:
    # A declaration refused where the body reaches it: a dropped statement's
    # refusal is not the body's, a spliced one's is (the first one spliced).
    body = _emitter()
    dropped, placed, later = body.fragment(), body.fragment(), body.fragment()
    dropped.refuse("dropped", "missing direct storage placement")
    placed.refuse("placed", "missing direct storage placement")
    later.refuse("later", "missing direct storage placement")
    assert body.refusal is None
    body.splice(placed)
    body.splice(later)
    assert body.refusal == ("placed", "missing direct storage placement")


def test_a_spliced_fragment_takes_no_more_statements() -> None:
    # Splicing may hand the child's lists to its target, so a write to the dead child would land in the target.
    body = _emitter()
    child = body.fragment()
    child.write(child.slot(LEAF), MIRConstant(1), None)
    body.splice(child)
    with pytest.raises(AssertionError, match="spliced fragment"):
        child.write(child.slot(LEAF), MIRConstant(2), None)


def test_region_storage_outside_a_full_expression_fails_assembly() -> None:
    body = _emitter()
    body.active_temporaries = []
    # Storage of a full expression's region, materialized where no full expression opened a region.
    body.materialize(MIRValue(MIRConstant("x"), owned_leaf_shape(STR, MIRRole.STORAGE)), "node")
    with pytest.raises(MIRRegionStorageError, match="region storage outside its full expression"):
        _build(body)
