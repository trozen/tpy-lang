"""Field-write lowering: `recv.field = <value>` decomposed into shape
families, each a (classify, lower) pair over a frozen plan.

The classifier decides every fact the render needs (family, source row,
move/copy verdict, convert target) and stores it on the plan; the lowering
function consumes plan fields and never re-derives statement shape from the
AST. Families are tried in declaration order -- the first non-None plan
wins -- so a family whose field-type slice overlaps a later one (None at
any Optional field, class constants before plain scalars) claims its
statements by position; reordering the table changes routing.
"""
from __future__ import annotations
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import Callable

from ...parse.nodes import (
    TpyArrayLiteral,
    TpyAssign,
    TpyDictLiteral,
    TpyFieldAccess,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
)
from ...type_def_registry import (
    is_array,
    is_bytes_type,
    is_dict,
    is_list,
    is_set,
)
from ...typesys import (
    OptionalType,
    TpyType,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..faces import witness as _witness
from ..fallback import (
    ThirUnsupported,
    note_detail,
    stmt_reject_reason,
)
from ..nodes import (
    Form,
    THIRAssign,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRLiteral,
    THIRName,
)
from .context import _ExprResultUse, _ExprUse, _LowerCtx
from .checks import (
    _borrow_tuple_local_type,
    _bytes_field_write_ok,
    _class_const_write_target_ok,
    _container_copy_field_write_ok,
    _container_field_write_ok,
    _optional_record_field_inner,
    _optional_record_field_upcast_write_ok,
    _optional_record_field_write_ok,
    _optional_value_record_field_inner,
    _ptr_union_field_write_ok,
    _record_field_write_ok,
    _scalar_field_write_ok,
    _str_field_write_ok,
    _union_member_ctor_rvalue,
    _user_deref_field_write_ok,
    copy_ctor_rvalue_source,
)
from .predicates import (
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _f1_record,
    _f1_tuple,
    _f1_tuple_field_write_ok,
    _f2b_optional_field_write_ok,
    _field_decl_type,
    _field_receiver_ok,
    _peel_coerce,
    _plain_container_read,
    _resolved_bytes_value,
    _resolved_str_value,
    _value_opt_owned_str,
    _value_opt_scalar,
    _value_tuple,
    _value_tuple_field_literal_write_ok,
    copy_ptr_optional_peel,
)
from .expressions import (
    _flush_witness,
    _is_move_source,
    _lower_class_const_write_target,
    _lower_copy_container,
    _lower_copy_record,
    _lower_expr,
    _lower_field_source,
    _lower_tuple_literal,
    _slot_literal_retype,
    _subscript_yields_borrow_ptr,
)
from . import statements as _statements


def _lower_field_write_target(stmt: TpyAssign, lc: _LowerCtx,
                              declared: dict[str, TpyType]) -> THIRExpr:
    """A plain-assign FIELD target renders via gen_expr (not gen_expr_deref):
    a narrowed Optional field assigns into the bare optional storage
    (`this->f = v;`), so the value-position narrowed deref is stripped from
    the top-level node only -- a narrowed RECEIVER inside the chain keeps
    its unwrap (`(*this->opt).x = v;`, the AST's receiver-deref arm)."""
    target = _lower_expr(stmt.target, lc, declared, field_prechecked=True)
    if isinstance(target, THIRFieldAccess) and target.narrowed_deref:
        target = replace(target, narrowed_deref=False)
    return target


def _btuple_elem_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                lc: _LowerCtx, analyzer) -> bool:
    """A value-scalar field write through a borrow-tuple element
    (`t[1].val = 99` -> `std::get<1>(t)->val = 99;`): the target rides the
    tuple-subscript field READ arm (the `_subscript_yields_borrow_ptr`
    arrow) off an in-scope borrow-tuple NAME receiver; only value-scalar /
    Char field slots admit (record/container slots carry write machinery
    not mirrored here)."""
    target = stmt.target
    if not (isinstance(target, TpyFieldAccess)
            and isinstance(target.obj, TpySubscript)
            and isinstance(target.obj.obj, TpyName)
            and _borrow_tuple_local_type(
                target.obj.obj.name, declared,
                lc.storage_tuple_locals) is not None
            # Plain borrow elements only: an Optional element write needs
            # the deref_check machinery (stays AST, pinned).
            and _subscript_yields_borrow_ptr(target.obj, lc)):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(target))))
    return bool((_eligible_scalar(ft) or _eligible_char(ft))
                and _witness("assign.btuple_elem_field"))


def _container_name_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """`recv.field = name` where field and name are the same builtin container
    family: the AST's default field assign renders the bare name and
    `_maybe_move` moves an owned local at its last use
    (`this->_data = std::move(d);`) -- the container sibling of the F1
    record-name field write."""
    v = stmt.value
    if not isinstance(v, TpyName):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(stmt.target))))
    if isinstance(ft, OptionalType):
        # An Optional[container] FIELD stores `std::optional<T>` whose
        # operator= absorbs the same bare/moved name render the plain
        # container field gets (`h.s = std::move(initial);`).
        ft = unwrap_readonly(ft.inner)
    if not (is_dict(ft) or is_list(ft) or is_set(ft) or is_array(ft)):
        return False
    if v.name in pointers or v.name in narrowed or v.name not in declared:
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    # An `Own[...]` param binds the same storage the field wants; the wrapper
    # only decides whether the value render MOVES it at its last use
    # (`this->_items = std::move(v);`), not what family it is. A property
    # setter's param is the shape that made this load-bearing.
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = unwrap_readonly(own.wrapped)
    return (((is_dict(ft) and is_dict(vt)) or (is_list(ft) and is_list(vt))
             or (is_set(ft) and is_set(vt))
             # An Array field copies a same-typed param bare (or moves it at
             # last use) exactly like the container families -- `std::array`
             # is a plain value member. Callable is NOT here: its store has no
             # borrow->storage convert arm, so admitting it CRASHES the
             # emitter instead of falling back.
             or (is_array(ft) and is_array(vt)))
            and _witness("field_write.container_name"))


@dataclass(frozen=True)
class _OptNonePlan:
    """`recv.opt = None` at an Optional FIELD: storage is `std::optional<T>`
    whatever the inner repr, so None renders the storage-form `std::nullopt`.
    Keyed on the DECLARED field type -- a flow-narrowed write site retypes
    the read to the inner, but the storage stays optional."""
    ftype: TpyType


def _classify_opt_none(stmt: TpyAssign, lc: _LowerCtx,
                       declared: dict[str, TpyType],
                       pointers: AbstractSet[str]) -> _OptNonePlan | None:
    if not isinstance(stmt.value, TpyNoneLiteral):
        return None
    if not _field_receiver_ok(stmt.target, declared, lc.analyzer):
        return None
    fdt = _field_decl_type(stmt.target, declared, lc.analyzer)
    if fdt is None:
        return None
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(fdt)))
    if not isinstance(ft, OptionalType):
        return None
    return _OptNonePlan(ftype=ft)


def _lower_opt_none(stmt: TpyAssign, plan: _OptNonePlan, lc: _LowerCtx,
                    declared: dict[str, TpyType], loc) -> THIRAssign:
    _witness("field_write.opt_none")
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=THIRLiteral(result_type=plan.ftype, value=None,
                          form=Form.STORAGE, loc=loc),
        loc=loc)


@dataclass(frozen=True)
class _ClassConstPlan:
    """A class-constant / classvar write: the bare qualified
    `<owner>::<member>` lvalue (receiver eval split off), then the same
    target-typed value render as the scalar field write."""
    ftype: TpyType


def _classify_class_const(stmt: TpyAssign, lc: _LowerCtx,
                          declared: dict[str, TpyType],
                          pointers: AbstractSet[str]
                          ) -> _ClassConstPlan | None:
    if not _class_const_write_target_ok(stmt.target, declared, lc.pointers,
                                        lc.analyzer):
        return None
    return _ClassConstPlan(ftype=lc.analyzer.get_expr_type(stmt.target))


def _lower_class_const(stmt: TpyAssign, plan: _ClassConstPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    target, recv_eval, recv_wrap = _lower_class_const_write_target(
        stmt.target, lc, declared, loc)
    _witness("field_write.class_const")
    return THIRAssign(
        target=target,
        value=_slot_literal_retype(
            _flush_witness(
                "flush.field_write",
                _lower_expr(stmt.value, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True))),
            plan.ftype, lc),
        recv_eval=recv_eval, recv_wrap=recv_wrap, loc=loc)


class _ValueRender(Enum):
    PLAIN = auto()      # scalar / Char / enum / Ptr: target-typed flush
    VALUE_OPT = auto()  # value-repr Optional[scalar | owned-str literal]
    STR = auto()        # owned-str family: operator=(string_view) absorbs
    BYTES = auto()      # owned bytes: a view (span) source takes bytes_copy


@dataclass(frozen=True)
class _ValueFieldPlan:
    """A value-family field write: no borrow<->storage lift beyond the bytes
    view copy; the render kind is decided from the field type once, in chain
    order, so overlapping slices (value-opt owned-str vs plain str) resolve
    by position exactly as the arm chain did."""
    render: _ValueRender
    ftype: TpyType
    bytes_ft: TpyType | None = None


def _classify_value(stmt: TpyAssign, lc: _LowerCtx,
                    declared: dict[str, TpyType],
                    pointers: AbstractSet[str]) -> _ValueFieldPlan | None:
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    # Kind first, admission second: a shape no render row claims (a
    # type-param slot, a readonly-wrapped elem field) must fall through to
    # the residual family WITHOUT evaluating the admission predicates --
    # some fire witnesses on success, and a witness must fire at most once
    # per statement.
    plan = None
    if (_eligible_scalar(ftype) or _eligible_char(ftype)
            or _eligible_enum(ftype, analyzer) is not None
            or _eligible_ptr_value(ftype, analyzer)):
        plan = _ValueFieldPlan(_ValueRender.PLAIN, ftype)
    elif ((_value_opt_scalar(ftype, analyzer) is not None
           or (_value_opt_owned_str(ftype, analyzer)
               and isinstance(_peel_coerce(stmt.value), TpyStrLiteral)))
          and not isinstance(stmt.value, TpyNoneLiteral)):
        plan = _ValueFieldPlan(_ValueRender.VALUE_OPT, ftype)
    elif _resolved_str_value(ftype, analyzer) is not None:
        plan = _ValueFieldPlan(_ValueRender.STR, ftype)
    else:
        bytes_ft = _resolved_bytes_value(ftype, analyzer)
        if bytes_ft is not None and is_bytes_type(bytes_ft):
            plan = _ValueFieldPlan(_ValueRender.BYTES, ftype,
                                   bytes_ft=bytes_ft)
    if plan is None:
        return None
    narrowed = lc.narrow.narrowed.keys()
    if not (_scalar_field_write_ok(stmt, declared, analyzer, pointers)
            or _user_deref_field_write_ok(stmt, declared, narrowed, analyzer,
                                          pointers)
            or _str_field_write_ok(stmt, declared, analyzer)
            or _bytes_field_write_ok(stmt, declared, analyzer)
            or _btuple_elem_field_write_ok(stmt, declared, lc, analyzer)):
        return None
    return plan


def _lower_value_field(stmt: TpyAssign, plan: _ValueFieldPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    # Each render keeps its arm's target-vs-value evaluation order: temp
    # numbering follows lowering order, so swapping them moves `__tmp_N`s.
    if plan.render is _ValueRender.STR:
        _witness("field_write.str")
        return THIRAssign(target=_lower_field_write_target(stmt, lc, declared),
                          value=_lower_expr(stmt.value, lc, declared), loc=loc)
    if plan.render is _ValueRender.BYTES:
        _witness("field_write.bytes")
        bval = _lower_expr(stmt.value, lc, declared)
        if bval.form is Form.BORROW:
            bval = THIRFormConvert(result_type=plan.bytes_ft, value=bval,
                                   form=Form.STORAGE, move=False, loc=loc)
        return THIRAssign(target=_lower_field_write_target(stmt, lc, declared),
                          value=bval, loc=loc)
    if plan.render is _ValueRender.VALUE_OPT:
        _witness("field_write.value_opt_scalar")
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=_slot_literal_retype(
            _flush_witness(
                "flush.field_write",
                _lower_expr(stmt.value, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True))),
            plan.ftype, lc), loc=loc)


class _RecordSlot(Enum):
    PLAIN = auto()  # F1-record field: the AST's default field assign
    OPT = auto()    # value-storage Optional[record]: operator= absorbs inner


@dataclass(frozen=True)
class _RecordFieldPlan:
    """A record-family field write. The source-row cascade (copy() /
    ctor-peel / name / rvalue) stays in the lowerer because its first step is
    itself a lowering attempt (_lower_copy_record); the slot verdict -- the
    only fact the two arms decided differently -- is made once here.
    slot_type is the copy-construct slot and name-convert target: the record
    itself at a PLAIN slot, the Optional's inner at an OPT slot."""
    slot: _RecordSlot
    slot_type: TpyType


def _classify_record(stmt: TpyAssign, lc: _LowerCtx,
                     declared: dict[str, TpyType],
                     pointers: AbstractSet[str]) -> _RecordFieldPlan | None:
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    narrowed = lc.narrow.narrowed.keys()
    if _f1_record(ftype, analyzer):
        if not _record_field_write_ok(stmt, declared, analyzer, pointers,
                                      narrowed, lc.prescan):
            return None
        return _RecordFieldPlan(_RecordSlot.PLAIN, ftype)
    opt_inner = (_optional_record_field_inner(ftype, analyzer)
                 or _optional_value_record_field_inner(ftype, analyzer))
    # A `copy()` over a pointer-repr Optional is the identity, so the
    # pointer-local exclusion has to see THROUGH it -- otherwise the wrapper
    # hides a borrow `T*` source and the OPT arm emits it with no
    # borrow->storage lift.
    opt_src = copy_ptr_optional_peel(stmt.value, analyzer) or stmt.value
    # The OPT slot serves sources typed as the INNER record (which
    # `optional::operator=` absorbs). A source typed as the WHOLE pointer-repr
    # Optional is a borrow `T*` and belongs to the lifting tail
    # (`ptr_to_optional`), like the pointer-local and `None` sources.
    opt_src_t = analyzer.get_expr_type(opt_src)
    if (opt_inner is not None
            and not isinstance(opt_src, TpyNoneLiteral)
            and not (isinstance(opt_src_t, OptionalType)
                     and opt_src_t.uses_pointer_repr())
            and not (isinstance(opt_src, TpyName)
                     and opt_src.name in lc.pointers)):
        if (_optional_record_field_write_ok(stmt, declared, pointers,
                                            analyzer, narrowed, lc.prescan)
                or _optional_record_field_upcast_write_ok(stmt, declared,
                                                          analyzer)):
            return _RecordFieldPlan(_RecordSlot.OPT, opt_inner)
        return None
    # A covariant-upcast rvalue always lands on the OPT slot: at emit the
    # inner is F1 (generation context qualifies its dyn-protocol arg via
    # native_cpp_names), so `opt_inner` is non-None wherever the gate
    # admitted. If that assumption ever breaks (a spelling-divergent inner),
    # fall the body back rather than let a later family's unrelated
    # FormConvert render claim it.
    if _optional_record_field_upcast_write_ok(stmt, declared, analyzer):
        note_detail("field_write.optrec_upcast_inner")
        raise ThirUnsupported(stmt_reject_reason(stmt))
    return None


def _lower_record_field(stmt: TpyAssign, plan: _RecordFieldPlan,
                        lc: _LowerCtx, declared: dict[str, TpyType],
                        loc) -> THIRAssign:
    analyzer = lc.analyzer
    plain = plan.slot is _RecordSlot.PLAIN
    copy_row = _lower_copy_record(stmt.value, lc, declared,
                                  slot_type=plan.slot_type, loc=loc)
    if copy_row is not None:
        _witness("field_write.record_copy" if plain
                 else "field_write.optrec_copy")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=copy_row, loc=loc)
    ctor_peel = copy_ctor_rvalue_source(stmt.value, analyzer)
    if ctor_peel is not None:
        # The peel's whole claim is that `copy(T(...))` and `T(...)` render
        # the same, so each slot lowers it exactly like its own bare-rvalue
        # arm below: target-typed with no arg-temps at a PLAIN slot, the
        # flushable-position temps at an OPT slot.
        if plain:
            _witness("field_write.record_copy_ctor")
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=_lower_expr(ctor_peel, lc, declared,
                                  target_type=plan.slot_type), loc=loc)
        _witness("field_write.optrec_copy_ctor")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=_flush_witness(
                "flush.assign",
                _lower_expr(ctor_peel, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True))),
            loc=loc)
    if isinstance(stmt.value, TpyName):
        _witness("field_write.record_name" if plain
                 else "field_write.optrec_name")
        lowered = _lower_expr(stmt.value, lc, declared)
        if plain:
            # A pointer-local source reads through the deref
            # (`this->result = (*saved);` -- gen_expr_deref's indirect name;
            # pointers are never movable).
            if (stmt.value.name in lc.pointers
                    and isinstance(lowered, THIRName)):
                lowered = replace(lowered, deref=True)
            if _is_move_source(stmt.value, lc):
                lowered = THIRFormConvert(result_type=plan.slot_type,
                                          value=lowered,
                                          form=Form.STORAGE,
                                          move=True, loc=loc)
        else:
            mv = _is_move_source(stmt.value, lc)
            # A BORROW source (a borrow record param / REF_ALIAS) lifts to
            # the INNER record storage -- the `is_plain_nonvalue` STORAGE arm
            # renders it bare, optional::operator= then absorbs it -- so the
            # pointer-lifted-sink validator sees no BORROW form lie. A move
            # (an owned name at last use) wraps to render `std::move(inner)`.
            # A move-free STORAGE source (an Own param NOT at last use)
            # copies bare -- no (no-op) convert. Convert to the INNER record,
            # NOT the Optional: that would spell the F2b `ptr_to_optional`
            # lift.
            if lowered.form is Form.BORROW or mv:
                lowered = THIRFormConvert(result_type=plan.slot_type,
                                          value=lowered,
                                          form=Form.STORAGE, move=mv,
                                          loc=loc)
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=lowered, loc=loc)
    if plain:
        _witness("field_write.record_rvalue")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=_lower_expr(stmt.value, lc, declared,
                              target_type=plan.slot_type,
                              use=_ExprUse(record_copy_sink=True)),
            loc=loc)
    _witness("field_write.optrec_rvalue")
    # The assign is a statement-position flush point, so the rvalue's arg
    # rows that hoist a `__tmp_N` (the Own-slot copy+move row) are
    # admissible here -- same flushable use as the generic assign tail.
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=_flush_witness(
            "flush.assign",
            _lower_expr(stmt.value, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE,
                                     allow_temps=True))),
        loc=loc)


@dataclass(frozen=True)
class _ContainerFieldPlan:
    """A container-family field write: the copy() / literal / name rows in
    chain order. The name row delegates to the shared tail render, whose
    Optional[container] convert-to-inner arm serves the value-repr
    `std::optional<T>` fields (their operator= absorbs the same bare/moved
    renders the plain container field gets)."""
    container_ft: bool  # raw list/dict/set ftype -- the copy() row's guard
    ftype: TpyType


def _classify_container(stmt: TpyAssign, lc: _LowerCtx,
                        declared: dict[str, TpyType],
                        pointers: AbstractSet[str]
                        ) -> _ContainerFieldPlan | None:
    analyzer = lc.analyzer
    narrowed = lc.narrow.narrowed.keys()
    if not (_container_field_write_ok(stmt, declared, analyzer)
            or _container_copy_field_write_ok(stmt, declared, pointers,
                                              analyzer)
            or _container_name_field_write_ok(stmt, declared, pointers,
                                              narrowed, analyzer)):
        return None
    ftype = analyzer.get_expr_type(stmt.target)
    return _ContainerFieldPlan(
        container_ft=bool(is_list(ftype) or is_dict(ftype) or is_set(ftype)),
        ftype=ftype)


def _lower_container_field(stmt: TpyAssign, plan: _ContainerFieldPlan,
                           lc: _LowerCtx, declared: dict[str, TpyType],
                           loc) -> THIRAssign:
    # `self.items = copy(data)` -- the copy-construct row
    # (`this->items = std::vector<int32_t>(data);`), the container sibling
    # of the record `copy()` field write.
    if plan.container_ft:
        ccopy = _lower_copy_container(stmt.value, lc, declared, loc=loc)
        if ccopy is not None:
            _witness("field_write.container_copy")
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=ccopy, loc=loc)
    # A container-literal write: the target-threaded literal render assigns
    # bare (a literal is never a movable name) -- the same
    # THIRContainerLiteral emit a decl init gets, consumed by the field
    # lvalue.
    if isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                               TpySetLiteral)):
        _witness("field_write.container_lit")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=_lower_expr(stmt.value, lc, declared), loc=loc)
    # The name row: bare copy (`field = v;`) or `std::move(v)` at an owned
    # local's last use. The shared tail's lift/whole-Optional arms are inert
    # for container ftypes; its Optional-inner convert target serves the
    # value-repr Optional[container] fields.
    fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=fvalue, loc=loc)


def _lower_tail_value(stmt: TpyAssign, ftype: TpyType, lc: _LowerCtx,
                      declared: dict[str, TpyType], loc) -> THIRExpr:
    """The shared borrow/name value render: borrow->storage FormConvert vs
    bare copy, the F2b `ptr_to_optional` lift, the pointer-slot-global
    RECEIVER quirk, and the copy()-peel identity. The container, tuple,
    union, opt-lift and residual families all route their name/borrow rows
    here; per-family facts (Optional repr, container-inner convert target)
    are derived from ftype so the render cannot drift between them.

    A pointer-slot GLOBAL source feeds the lift as a POINTER
    (`h->v = ptr_to_optional(g);`), so the value-position deref it takes at
    every other sink must not fire -- RECEIVER says exactly that. `copy()`
    over a pointer-repr Optional is the identity, so the peeled source
    renders (the `_gen_copy_expr` early return); the sink's own lift is what
    copies, and the explicit copy() suppresses the move even at the peeled
    argument's last use (which is also what copy() means)."""
    peeled = copy_ptr_optional_peel(stmt.value, lc.analyzer)
    tail_src = peeled if peeled is not None else stmt.value
    ptr_src = (isinstance(tail_src, TpyName)
               and tail_src.name in lc.prescan.global_slots)
    val_opt_container = (
        isinstance(ftype, OptionalType)
        and (_plain_container_read(unwrap_readonly(ftype.inner))
             or is_array(unwrap_readonly(ftype.inner))))
    ptr_opt_field = (isinstance(ftype, OptionalType)
                     and not val_opt_container
                     and ftype.uses_pointer_repr())
    lowered = _lower_expr(
        tail_src, lc, declared,
        use=(_ExprUse(result=_ExprResultUse.RECEIVER)
             if ptr_src
             else _ExprUse(ptr_opt_lift=ptr_opt_field)),
        # A FIELD source of the same Optional is consumed WHOLE (the
        # `std::optional<T>` member copies bare); the read must not take the
        # narrowing deref a value position gets.
        allow_whole_optional=(ptr_opt_field and not ptr_src
                              and isinstance(tail_src, TpyFieldAccess)))
    mv = peeled is None and _is_move_source(tail_src, lc)
    # A storage-form source of the field's own type needing no move is a
    # bare copy (`field = v`); the borrow->storage convert would be a no-op
    # (validate.py rejects it). This is the value-bound `Own[T]` field
    # write: the param is passed by value and copied here, mirroring the
    # AST method body -- unlike the ctor MIL, which moves. A whole-Optional
    # FIELD read is `std::optional<T>` member STORAGE (the read arm's VALUE
    # tag is the str/bytes view-vs-owned axis, which says nothing here), so
    # it copies bare; the `ptr_to_optional` lift belongs to the borrow `T*`
    # sources.
    whole_opt_field = (ptr_opt_field
                       and isinstance(tail_src, TpyFieldAccess))
    cnv_t = (unwrap_readonly(ftype.inner) if val_opt_container
             else ftype)
    if (not mv and lowered.result_type == cnv_t
            and (lowered.form is Form.STORAGE or whole_opt_field)):
        return lowered
    return THIRFormConvert(result_type=cnv_t, value=lowered,
                           form=Form.STORAGE, move=mv, loc=loc)


class _TupleRow(Enum):
    LITERAL = auto()      # spelled value-form brace-init
    BORROW_NAME = auto()  # borrow tuple param: the `tuple_to_storage` lift


@dataclass(frozen=True)
class _TupleFieldPlan:
    """A tuple-field write. A VALUE-tuple field takes the spelled brace-init
    directly -- borrow and storage coincide (an Optional[value-tuple] field
    absorbs the same render) -- while an F3 tuple field wraps the literal in
    the assign's `tuple_to_storage` convert; a borrow-tuple NAME source
    takes the same lift through the shared tail render."""
    row: _TupleRow
    ftype: TpyType
    slot_t: TpyType | None = None  # LITERAL: the brace-init's element slots
    value_tuple: bool = False      # LITERAL: bare (True) vs F3 convert


def _classify_tuple(stmt: TpyAssign, lc: _LowerCtx,
                    declared: dict[str, TpyType],
                    pointers: AbstractSet[str]) -> _TupleFieldPlan | None:
    analyzer = lc.analyzer
    if not (_value_tuple_field_literal_write_ok(stmt, declared, analyzer)
            or _f1_tuple_field_write_ok(stmt, declared,
                                        lc.storage_tuple_locals, analyzer)):
        return None
    ftype = analyzer.get_expr_type(stmt.target)
    if isinstance(stmt.value, TpyTupleLiteral):
        ft_lit = (unwrap_readonly(ftype.inner)
                  if isinstance(ftype, OptionalType) else ftype)
        vt_field = _value_tuple(ft_lit, analyzer)
        ft_tuple = (None if vt_field is not None
                    else _f1_tuple(ftype, analyzer))
        slot_t = vt_field if vt_field is not None else ft_tuple
        if slot_t is None:
            return None
        return _TupleFieldPlan(_TupleRow.LITERAL, ftype, slot_t,
                               value_tuple=vt_field is not None)
    return _TupleFieldPlan(_TupleRow.BORROW_NAME, ftype)


def _lower_tuple_field(stmt: TpyAssign, plan: _TupleFieldPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    if plan.row is _TupleRow.LITERAL:
        lit = _lower_tuple_literal(stmt.value, plan.slot_t, lc, declared)
        _witness("field_write.tuple_literal")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=(lit if plan.value_tuple else
                   THIRFormConvert(result_type=plan.ftype, value=lit,
                                   form=Form.STORAGE, move=False, loc=loc)),
            loc=loc)
    fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=fvalue, loc=loc)


@dataclass(frozen=True)
class _UnionFieldPlan:
    """An F4 U2 value-variant field write: None stores monostate, a field
    source copies storage-to-storage bare, a member-typed ctor rvalue stores
    bare (the variant assignment absorbs the member -- a to_value_variant
    lift would be ill-formed), and a borrow pointer-variant name takes the
    `to_value_variant` lift through the shared tail render."""
    ftype: TpyType
    union_t: TpyType


def _classify_union(stmt: TpyAssign, lc: _LowerCtx,
                    declared: dict[str, TpyType],
                    pointers: AbstractSet[str]) -> _UnionFieldPlan | None:
    analyzer = lc.analyzer
    if not _ptr_union_field_write_ok(stmt, declared, analyzer):
        return None
    ftype = analyzer.get_expr_type(stmt.target)
    union_t = _eligible_ptr_union(ftype, analyzer)
    if union_t is None:
        return None
    return _UnionFieldPlan(ftype, union_t)


def _lower_union_field(stmt: TpyAssign, plan: _UnionFieldPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    if isinstance(stmt.value, TpyNoneLiteral):
        fvalue: THIRExpr = THIRLiteral(result_type=plan.ftype, value=None,
                                       form=Form.STORAGE, loc=loc)
    elif isinstance(stmt.value, TpyFieldAccess):
        fvalue = _lower_field_source(stmt.value, lc, declared)
    elif _union_member_ctor_rvalue(stmt.value, plan.union_t, lc.analyzer):
        _witness("field_write.union_member_ctor")
        fvalue = _flush_witness(
            "flush.assign",
            _lower_expr(stmt.value, lc, declared,
                        use=_ExprUse(result=_ExprResultUse.STORAGE,
                                     allow_temps=True)))
    else:
        fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=fvalue, loc=loc)


@dataclass(frozen=True)
class _OptLiftPlan:
    """An F2b pointer-repr Optional[record] field write: a borrow `T*` local
    or same-Optional call takes the `ptr_to_optional[_move]` lift, a
    same-Optional FIELD read copies bare (already storage), all through the
    shared tail render picking lift-vs-bare off the LOWERED form."""
    ftype: TpyType


def _classify_opt_lift(stmt: TpyAssign, lc: _LowerCtx,
                       declared: dict[str, TpyType],
                       pointers: AbstractSet[str]) -> _OptLiftPlan | None:
    if not _f2b_optional_field_write_ok(stmt, declared, pointers,
                                        lc.analyzer):
        return None
    return _OptLiftPlan(lc.analyzer.get_expr_type(stmt.target))


def _lower_opt_lift_field(stmt: TpyAssign, plan: _OptLiftPlan, lc: _LowerCtx,
                          declared: dict[str, TpyType], loc) -> THIRAssign:
    # The None row normally belongs to the opt-none family (keyed on the
    # DECLARED type); this arm only sees it when _field_decl_type cannot
    # resolve the field, and then renders against the READ type.
    if isinstance(stmt.value, TpyNoneLiteral):
        fvalue: THIRExpr = THIRLiteral(result_type=plan.ftype, value=None,
                                       form=Form.STORAGE, loc=loc)
    else:
        fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=fvalue, loc=loc)


@dataclass(frozen=True)
class _ResidualPlan:
    """The shapes the value family's render rows do not claim but whose
    predicates admit: a generic record's type-param `T` field (a plain
    assign whose BORROW->STORAGE convert renders the source bare/moved per
    instantiation) and the readonly-wrapped borrow-tuple-element edge. Both
    take the shared tail render; ftype is never Optional or a union here,
    so it collapses to the bare-copy-vs-FormConvert verdict."""
    ftype: TpyType


def _classify_residual(stmt: TpyAssign, lc: _LowerCtx,
                       declared: dict[str, TpyType],
                       pointers: AbstractSet[str]) -> _ResidualPlan | None:
    analyzer = lc.analyzer
    if not (_scalar_field_write_ok(stmt, declared, analyzer, pointers)
            or _btuple_elem_field_write_ok(stmt, declared, lc, analyzer)):
        return None
    return _ResidualPlan(analyzer.get_expr_type(stmt.target))


def _lower_residual_field(stmt: TpyAssign, plan: _ResidualPlan,
                          lc: _LowerCtx, declared: dict[str, TpyType],
                          loc) -> THIRAssign:
    if isinstance(stmt.value, TpyNoneLiteral):
        fvalue: THIRExpr = THIRLiteral(result_type=plan.ftype, value=None,
                                       form=Form.STORAGE, loc=loc)
    else:
        fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=fvalue, loc=loc)


_FAMILIES: list[tuple[Callable, Callable]] = [
    (_classify_opt_none, _lower_opt_none),
    (_classify_class_const, _lower_class_const),
    (_classify_value, _lower_value_field),
    (_classify_record, _lower_record_field),
    (_classify_container, _lower_container_field),
    (_classify_tuple, _lower_tuple_field),
    (_classify_union, _lower_union_field),
    (_classify_opt_lift, _lower_opt_lift_field),
    (_classify_residual, _lower_residual_field),
]


def lower_field_write(stmt: TpyAssign, lc: _LowerCtx,
                      declared: dict[str, TpyType],
                      pointers: AbstractSet[str],
                      loc) -> THIRAssign:
    """First matching family lowers the statement; a statement no family
    claims raises with the shared field-write reject tag."""
    for classify, lower in _FAMILIES:
        plan = classify(stmt, lc, declared, pointers)
        if plan is not None:
            return lower(stmt, plan, lc, declared, loc)
    note_detail("assign.field_write_shape")
    raise ThirUnsupported(stmt_reject_reason(stmt))
