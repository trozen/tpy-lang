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
    TpyDictComprehension,
    TpyListComprehension,
    TpySetComprehension,
    TpyArrayLiteral,
    TpyAssign,
    TpyCall,
    TpyDictLiteral,
    TpyFieldAccess,
    TpyListRepeat,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
)
from ...type_def_registry import (
    is_bytes_type,
    type_def_of,
)
from ...typesys import (
    NoneType,
    OptionalType,
    TpyType,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ..faces import witness as _witness
from ..reject import (
    ThirUnsupported,
    note_detail,
    stmt_reject_reason,
)
from ..nodes import (
    Form,
    THIRAssign,
    THIRContainerLiteral,
    THIRExpr,
    THIRFieldAccess,
    THIRFormConvert,
    THIRLiteral,
    THIRMove,
    THIRName,
)
from .context import (
    _NO_FORMS, _ExprResultUse, _ExprUse, _LowerCtx,
                      _ONLY_TUPLE_SOURCE, _slot_lift_forms, SinkPos)
from .checks import (
    _borrow_tuple_local_type,
    _bytes_field_write_ok,
    _field_over_container_subscript_ok,
    _class_const_write_target_ok,
    _container_field_write_ok,
    _container_field_write_slot,
    _container_prvalue_field_write_ok,
    _optional_container_storage_inner,
    _optional_record_field_inner,
    _optional_record_field_upcast_write_ok,
    _optional_record_field_write_ok,
    _optional_value_record_field_inner,
    _ptr_union_field_write_ok,
    _ref_field_write_ok,
    _scalar_field_write_ok,
    _str_field_write_ok,
    _union_member_ctor_rvalue,
    _user_deref_field_write_ok,
    copy_ctor_rvalue_source,
)
from .predicates import (
    _comp_shadow_pointers,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _f1_container_ref,
    _f1_ref,
    _f1_tuple,
    _f1_tuple_field_write_ok,
    _nested_storage_tuple,
    _nested_tuple_field_literal_write_ok,
    _f2b_optional_field_write_ok,
    _field_decl_type,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _owned_optional_call_source,
    _callable_value,
    _peel_coerce,
    _plain_container_read,
    _resolved_bytes_value,
    _resolved_str_value,
    _storage_tuple_write_source,
    _value_opt_owned_str,
    _value_opt_scalar,
    _value_tuple,
    _value_tuple_field_literal_write_ok,
    copy_ptr_optional_peel,
)
from . import comprehensions as _comprehensions
from .expressions import (
    _flush_witness,
    _is_move_source,
    _lower_class_const_write_target,
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
    """A plain-assign FIELD target renders WITHOUT the value-position deref:
    a narrowed Optional field assigns into the bare optional storage
    (`this->f = v;`), so the narrowed deref is stripped from
    the top-level node only -- a narrowed RECEIVER inside the chain keeps
    its unwrap (`(*this->opt).x = v;`)."""
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
    char field slots admit (record/container slots carry write machinery
    this family does not render)."""
    target = stmt.target
    if not (isinstance(target, TpyFieldAccess)
            and isinstance(target.obj, TpySubscript)
            and isinstance(target.obj.obj, TpyName)
            and _borrow_tuple_local_type(
                target.obj.obj.name, declared,
                lc.storage_tuple_locals) is not None
            # Plain borrow elements only: an Optional element write needs
            # the deref_check machinery (rejects, pinned).
            and _subscript_yields_borrow_ptr(target.obj, lc)):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(target))))
    return bool((_eligible_scalar(ft) or _eligible_char(ft))
                and _witness("assign.btuple_elem_field"))


def _opt_ref_name_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """`recv.opt = name` at an `Optional[<reference>]` FIELD the record OPT
    rows do not claim -- a same-family container inner today. The field is
    STORAGE form (`std::optional<C>`, whatever the inner's borrow repr), and
    its operator= absorbs the same bare/moved name render a plain field gets
    (`h.s = std::move(initial);`); the shared tail picks bare-vs-convert off
    the LOWERED form.

    Same-FAMILY is spelled as one TypeDef identity, not a per-family pair
    ladder: a `bytes` source into a `bytearray` field is the view coerce,
    which arrives as its own node, and a container-to-container crossing
    carries a conversion the bare-name render does not spell. Callable stays
    out because it is not on the reference axis at all -- its store has no
    borrow->storage convert arm, so admitting it CRASHES the emitter.

    The PLAIN (non-Optional) slot is NOT here: `_ref_field_write_ok`'s name
    row claims it for both halves of the axis."""
    v = stmt.value
    if not isinstance(v, TpyName):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(stmt.target))))
    if not isinstance(ft, OptionalType):
        return False
    ft = unwrap_readonly(ft.inner)
    if not _f1_container_ref(ft):
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
    return (_f1_container_ref(vt) and type_def_of(ft) is type_def_of(vt)
            and _witness("field_write.container_name"))


def _narrowed_optptr_field_write_ok(
        stmt: TpyAssign, declared: dict[str, TpyType], pointers: set[str],
        narrowed: AbstractSet[str], analyzer) -> bool:
    """`self.items = items` where `items` is a NARROWED ptr-repr
    `Optional[<reference>]` PARAM: the binding is a `T*`, so the value
    position derefs and the field copies (`this->items = (*items);`) -- the
    deref the merged NAME row spells for any pointer-bound source. Keyed on
    the OCCURRENCE type being non-Optional -- an UN-narrowed source would
    need a null check the plain copy omits. The field itself is a PLAIN
    reference slot: an `Optional[...]` field takes the `ptr_to_optional`
    lift row.

    Its own admission row rather than a leg of `_ref_field_write_ok`'s
    pointer-local row, because that row pins the source's DECLARED type to
    the field's; here the declared type is the Optional and only the
    narrowed occurrence matches."""
    v = stmt.value
    if not (isinstance(v, TpyName) and v.name in pointers
            and v.name in declared and v.name not in narrowed):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(stmt.target))))
    if not _f1_ref(ft, analyzer):
        return False
    dt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    if not (isinstance(dt, OptionalType) and dt.uses_pointer_repr()):
        return False
    # The occurrence must be PROVEN non-None (sema retypes the read to the
    # inner); an Optional occurrence keeps rejecting.
    ot = analyzer.get_expr_type(v)
    otu = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ot)))
           if ot is not None else None)
    if isinstance(otu, OptionalType) or otu is None:
        return False
    inner = unwrap_readonly(dt.inner)
    # Same slot type, or the same TypeDef for the container half -- the
    # membership test the container leg shipped, kept verbatim so no shape
    # it admitted starts rejecting. A user record has no TypeDef, so the
    # record leg is the exact-type one.
    return (_f1_ref(inner, analyzer)
            and (inner == ft
                 or (type_def_of(ft) is not None
                     and type_def_of(ft) is type_def_of(inner)))
            and _witness("field_write.container_narrowed_optptr"))


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
    # A SUBSCRIPT receiver -- a container element or a record-typed tuple
    # element -- is a plain record borrow lvalue, so the member spells the
    # same postfix off the bare element read that the scalar field write
    # already renders there. Only the target varies with the receiver; the
    # `std::nullopt` value render is receiver-blind.
    if not (_field_receiver_ok(stmt.target, declared, lc.analyzer)
            or _field_over_subscript_ok(stmt.target, declared, lc.analyzer)
            or _field_over_container_subscript_ok(stmt.target, declared,
                                                  lc.analyzer, lc.pointers)):
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
    PLAIN = auto()      # scalar / char / enum / Ptr: target-typed flush
    VALUE_OPT = auto()  # value-repr Optional[scalar | owned-str literal]
    STR = auto()        # owned-str family: operator=(string_view) absorbs
    BYTES = auto()      # owned bytes: a view (span) source takes `Bytes(x)`


@dataclass(frozen=True)
class _ValueFieldPlan:
    """A value-family field write: no borrow<->storage lift beyond the bytes
    view copy; the render kind is decided from the field type once, in chain
    order, so overlapping slices (value-opt owned-str vs plain str) resolve
    by position exactly as the arm chain did."""
    render: _ValueRender
    ftype: TpyType
    bytes_ft: TpyType | None = None


def _opt_callable_field(ftype) -> bool:
    """An `Optional[Callable]` field slot (`std::optional<std::function>`
    by value): the operator= absorbs the bare callable-name render the
    plain Callable field row gets."""
    u = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ftype)))
         if ftype is not None else None)
    return (isinstance(u, OptionalType)
            and _callable_value(unwrap_readonly(u.inner)))


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
            or _eligible_ptr_value(ftype, analyzer)
            # A Callable field written from a hoisted-lambda NAME
            # (`self.callback = add_offset;` -- std::function copies the
            # closure bare). NAMES only; other sources are unwitnessed.
            or (_callable_value(ftype)
                and isinstance(_peel_coerce(stmt.value), TpyName))
            # ... and the Optional[Callable] FIELD flavor (`self.on_event
            # = cb;` -- `std::optional<std::function<..>>`'s operator=
            # absorbs the same bare name; None rides the opt-none family
            # ahead of this chain). Witnessed at the lower row.
            or (_opt_callable_field(ftype)
                and isinstance(_peel_coerce(stmt.value), TpyName))):
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
            or _str_field_write_ok(stmt, declared, analyzer, pointers)
            or _bytes_field_write_ok(stmt, declared, analyzer, pointers)
            or _btuple_elem_field_write_ok(stmt, declared, lc, analyzer)):
        return None
    return plan


def _lower_value_field(stmt: TpyAssign, plan: _ValueFieldPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    # Each render keeps its arm's target-vs-value evaluation order: temp
    # numbering follows lowering order, so swapping them moves `__tmp_N`s.
    # The `forms=_NO_FORMS` beside each `pos=SinkPos.FIELD_WRITE` below is
    # the position's OWN row restated, not a narrowing of it: a field write
    # keeps the value past the statement, so the dying-source lend is refused
    # here on purpose, and restating it is what makes the site readable
    # beside the arms that DO hand out a verdict.
    if plan.render is _ValueRender.STR:
        _witness("field_write.str")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=_lower_expr(stmt.value, lc, declared,
                              use=_ExprUse(pos=SinkPos.FIELD_WRITE, forms=_NO_FORMS)), loc=loc)
    if plan.render is _ValueRender.BYTES:
        _witness("field_write.bytes")
        bval = _lower_expr(stmt.value, lc, declared, use=_ExprUse(pos=SinkPos.FIELD_WRITE, forms=_NO_FORMS))
        if bval.form is Form.BORROW:
            bval = THIRFormConvert(result_type=plan.bytes_ft, value=bval,
                                   form=Form.STORAGE, move=False, loc=loc)
        return THIRAssign(target=_lower_field_write_target(stmt, lc, declared),
                          value=bval, loc=loc)
    if plan.render is _ValueRender.VALUE_OPT:
        _witness("field_write.value_opt_scalar")
    elif _opt_callable_field(plan.ftype):
        _witness("field_write.opt_callable_name")
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=_slot_literal_retype(
            _flush_witness(
                "flush.field_write",
                _lower_expr(stmt.value, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True),
                            # A FIELD source of the same value-repr Optional
                            # is consumed WHOLE (the `std::optional<T>`
                            # member copies bare) -- the same rule the
                            # ptr-repr tail applies to its field sources.
                            allow_whole_optional=(
                                plan.render is _ValueRender.VALUE_OPT
                                and isinstance(stmt.value, TpyFieldAccess)))),
            plan.ftype, lc), loc=loc)


class _RefSlot(Enum):
    PLAIN = auto()     # `_f1_ref` field: the default field assign
    OPT = auto()       # value-storage Optional[record]: operator= absorbs
                       # the inner
    OPT_TAIL = auto()  # an Optional[reference] slot the OPT rows do not
                       # claim: the shared tail render


@dataclass(frozen=True)
class _RefFieldPlan:
    """A reference-axis field write -- records and the builtin containers,
    one plan. The source-row cascade (copy() / ctor-peel / literal / repeat /
    owned rvalue / name / rvalue) stays in the lowerer because its first step
    is itself a lowering attempt (_lower_copy_record); the slot verdict --
    the only fact the arms decided differently -- is made once here.

    slot_type is the copy-construct slot and name-convert target: the
    reference itself at a PLAIN slot, the Optional's inner at an OPT slot,
    the whole Optional at an OPT_TAIL one (the shared tail derives its own
    convert target from it)."""
    slot: _RefSlot
    slot_type: TpyType
    # The DECLARED field slot, threaded by the literal and repeat rows.
    # Distinct from the read type: a flow-narrowed `Optional[C]` field reads
    # as the bare container, one unwrap shallower than the storage it holds.
    decl_ftype: TpyType | None = None
    # The container inner of a storage-form `Optional[C]` declared slot --
    # the literal row's lowering target, unwrapped from the Optional.
    opt_inner: TpyType | None = None
    # The value materializes its own owned container (`[e] * n`, a
    # container-returning call rvalue): assigned bare, no move verdict.
    prvalue_src: bool = False


def _classify_ref(stmt: TpyAssign, lc: _LowerCtx,
                  declared: dict[str, TpyType],
                  pointers: AbstractSet[str]) -> _RefFieldPlan | None:
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    narrowed = lc.narrow.narrowed.keys()
    if _f1_ref(ftype, analyzer):
        if not (_ref_field_write_ok(stmt, declared, analyzer, pointers,
                                    narrowed, lc.prescan)
                or _narrowed_optptr_field_write_ok(stmt, declared, pointers,
                                                   narrowed, analyzer)):
            return None
        if getattr(stmt.target, "deref_depth", 0):
            _witness("field_write.record_user_deref")
        decl_ftype = _container_field_write_slot(stmt, declared, analyzer)
        return _RefFieldPlan(
            _RefSlot.PLAIN, ftype, decl_ftype=decl_ftype,
            opt_inner=_optional_container_storage_inner(decl_ftype),
            prvalue_src=_container_prvalue_field_write_ok(stmt, declared,
                                                          analyzer))
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
            return _RefFieldPlan(_RefSlot.OPT, opt_inner)
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
    # An Optional slot whose inner is on the reference axis but which the OPT
    # rows above do not claim (a container inner today): its
    # `optional::operator=` absorbs a bare/moved name, so the shared tail
    # picks lift-vs-bare off the LOWERED form, and a literal is classified
    # and lowered against the Optional's INNER. Reached only after the OPT
    # rows decline, so ordering -- not a family test -- keeps the record
    # inners on their own render.
    if (_container_field_write_ok(stmt, declared, analyzer)
            or _opt_ref_name_field_write_ok(stmt, declared, pointers,
                                            narrowed, analyzer)):
        decl_ftype = _container_field_write_slot(stmt, declared, analyzer)
        return _RefFieldPlan(
            _RefSlot.OPT_TAIL, ftype, decl_ftype=decl_ftype,
            opt_inner=_optional_container_storage_inner(decl_ftype))
    return None


def _lower_container_literal_value(stmt: TpyAssign, plan: _RefFieldPlan,
                                   lc: _LowerCtx,
                                   declared: dict[str, TpyType]) -> THIRExpr:
    """The container-LITERAL value render, shared by the plain and the
    storage-form `Optional[C]` field slots: the target-threaded literal
    assigns bare (a literal is never a movable name) -- the same
    THIRContainerLiteral emit a decl init gets, consumed by the field
    lvalue."""
    if plan.opt_inner is not None:
        # A storage-form `std::optional<C>` field: lowered against the
        # INNER (threading the Optional would derive the element targets
        # from it). A bare list brace-init additionally self-describes --
        # the optional's converting ctor has no type to deduce from
        # `{10, 20}`; dict/set literals spell their container already.
        value = _lower_expr(stmt.value, lc, declared,
                            use=_ExprUse(slot_target=plan.opt_inner))
        if isinstance(stmt.value, TpyArrayLiteral):
            if not isinstance(value, THIRContainerLiteral):
                # The prefix has nowhere to live; reject rather than let a
                # `replace` TypeError crash the compiler.
                note_detail("assign.field_write_shape")
                raise ThirUnsupported(stmt_reject_reason(stmt))
            value = replace(value,
                            typed_brace_cpp=lc.render_type(plan.opt_inner))
        _witness("field_write.opt_container_lit")
        return value
    _witness("field_write.container_lit")
    return _lower_expr(stmt.value, lc, declared, use=_ExprUse(pos=SinkPos.FIELD_WRITE, forms=_NO_FORMS))


def _lower_container_comp_value(stmt: TpyAssign, slot: 'TpyType | None',
                                lc: _LowerCtx,
                                declared: dict[str, TpyType]) -> THIRExpr:
    """The comprehension value render at a container field, plain or the
    storage-form `Optional[C]` one unwrap down: the stmt-expr assigns bare
    (`this->data = ({ ... });`), the member-init prefix's render at the body
    position."""
    return _comprehensions._lower_comprehension(
        stmt.value, slot, lc, declared,
        _comp_shadow_pointers(lc.pointers, declared, lc.analyzer))


def _lower_ref_field(stmt: TpyAssign, plan: _RefFieldPlan,
                     lc: _LowerCtx, declared: dict[str, TpyType],
                     loc) -> THIRAssign:
    analyzer = lc.analyzer
    if plan.slot is _RefSlot.OPT_TAIL:
        # The Optional slot the OPT rows do not claim: a literal or a
        # comprehension against the Optional's inner, everything else through
        # the shared tail, whose Optional-inner convert target serves exactly
        # these fields.
        if isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral)):
            fvalue: THIRExpr = _lower_container_literal_value(
                stmt, plan, lc, declared)
        elif isinstance(stmt.value, (TpyListComprehension,
                                     TpySetComprehension,
                                     TpyDictComprehension)):
            _witness("field_write.opt_container_comp")
            fvalue = _lower_container_comp_value(
                stmt, plan.opt_inner if plan.opt_inner is not None
                else plan.decl_ftype, lc, declared)
        else:
            fvalue = _lower_tail_value(stmt, plan.slot_type, lc, declared,
                                       loc)
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=fvalue, loc=loc)
    plain = plan.slot is _RefSlot.PLAIN
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
                value=_lower_expr(
                    ctor_peel, lc, declared,
                    use=_ExprUse(slot_target=plan.slot_type)), loc=loc)
        _witness("field_write.optrec_copy_ctor")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=_flush_witness(
                "flush.assign",
                _lower_expr(ctor_peel, lc, declared,
                            use=_ExprUse(result=_ExprResultUse.STORAGE,
                                         allow_temps=True))),
            loc=loc)
    if plain:
        # The three CONSTRUCTION rows: a container literal, a `[e] * n`
        # repeat, and an owned-rvalue call. Each builds the field's own
        # storage, so the value lands bare with no move verdict to make.
        if isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral)):
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=_lower_container_literal_value(stmt, plan, lc,
                                                     declared), loc=loc)
        if isinstance(stmt.value, (TpyListComprehension, TpySetComprehension,
                                   TpyDictComprehension)):
            _witness("field_write.container_comp")
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=_lower_container_comp_value(stmt, plan.decl_ftype, lc,
                                                  declared), loc=loc)
        if plan.prvalue_src:
            if isinstance(stmt.value, TpyListRepeat):
                # The repeat threads the FIELD type -- an untargeted resolve
                # demotes it to the Array flavor.
                _witness("field_write.container_repeat")
                value = _lower_expr(
                    stmt.value, lc, declared,
                    use=_ExprUse(slot_target=plan.decl_ftype))
            else:
                # STORAGE: the field owns its container, so the call's
                # owned-rvalue result lands by value -- the same sink the
                # `parts = s.split(sep)` decl init threads. A FREE call's
                # `Own[container]` return lands through the identical row.
                _witness("field_write.container_free_call"
                         if isinstance(stmt.value, TpyCall)
                         else "field_write.container_method_call")
                value = _lower_expr(
                    stmt.value, lc, declared,
                    use=_ExprUse(result=_ExprResultUse.STORAGE,
                                 pos=SinkPos.FIELD_WRITE, forms=_NO_FORMS))
            return THIRAssign(
                target=_lower_field_write_target(stmt, lc, declared),
                value=value, loc=loc)
    if isinstance(stmt.value, TpyName):
        _witness("field_write.record_name" if plain
                 else "field_write.optrec_name")
        lowered = _lower_expr(stmt.value, lc, declared, use=_ExprUse(pos=SinkPos.FIELD_WRITE, forms=_NO_FORMS))
        if plain:
            # A pointer-local source reads through the deref
            # (`this->result = (*saved);` -- an indirect name derefs at a
            # value-consuming read; pointers are never movable).
            if (stmt.value.name in lc.pointers
                    and isinstance(lowered, THIRName)):
                lowered = replace(lowered, deref=True)
            if _is_move_source(stmt.value, lc):
                lowered = THIRFormConvert(
                    result_type=plan.slot_type, value=lowered,
                    form=Form.STORAGE, move=True,
                    materialize=_object_source_materialize(lowered,
                                                           lc.analyzer),
                    loc=loc)
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
            value=_lower_expr(
                stmt.value, lc, declared,
                use=_ExprUse(pos=SinkPos.FIELD_WRITE,
                             slot_target=plan.slot_type)),
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


def _object_source_materialize(lowered: THIRExpr, analyzer) -> 'bool | None':
    """The ONE fact a field write's borrow->storage convert cannot derive
    from (family, form): whether the SOURCE arrived as a view to copy into
    the slot's buffer, or as the object itself. A source on the REFERENCE
    axis is the object -- there is nothing to materialize, so the convert
    moves or copies it -- and `False` pins that. `None` for every other
    source leaves the emit's own (family, form) rule in charge, which is
    what a VALUE-form view source needs: `bytes` / `str` / `BytesView` /
    `StrView` all read as a span or a string_view and owe the owning slot
    the copy.

    Keyed on the source rather than on the slot because the slot's family
    is exactly what does not decide it: `bytearray` is a reference type
    whose storage is the same owned buffer `bytes` has, so at a bytearray
    slot the (family, BORROW->STORAGE) pair has two correct renders and the
    emitter refuses a convert that carries neither (validate.py). Read by
    both convert sites of the reference field write -- the NAME row's move
    wrap and the shared tail -- because such a write reaches whichever of
    the two its slot shape routes to."""
    rt = lowered.result_type
    if rt is None:
        return None
    return (False
            if _f1_ref(unwrap_readonly(unwrap_ref_type(
                unwrap_send_sync(rt))), analyzer)
            else None)


def _lower_tail_value(stmt: TpyAssign, ftype: TpyType, lc: _LowerCtx,
                      declared: dict[str, TpyType], loc, *,
                      union_divergent_ok: bool = False) -> THIRExpr:
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
    renders (copy() early-returns on it); the sink's own lift is what
    copies, and the explicit copy() suppresses the move even at the peeled
    argument's last use (which is also what copy() means)."""
    peeled = copy_ptr_optional_peel(stmt.value, lc.analyzer)
    tail_src = peeled if peeled is not None else stmt.value
    # An `Own[T] | None`-returning call is ALREADY the field's
    # `std::optional<T>` by value, so it assigns bare -- the lift the field's
    # pointer repr implies would hand `ptr_to_optional` an optional where it
    # takes a `T*`, which is ill-formed C++. Same predicate as the admission,
    # so the two cannot drift apart.
    if _owned_optional_call_source(tail_src, ftype, lc.analyzer):
        lowered = _lower_expr(tail_src, lc, declared,
                              use=_ExprUse(result=_ExprResultUse.STORAGE))
        _witness("field_write.owned_opt_call")
        return lowered
    ptr_src = (isinstance(tail_src, TpyName)
               and tail_src.name in lc.prescan.global_slots)
    val_opt_container = (
        isinstance(ftype, OptionalType)
        and _plain_container_read(unwrap_readonly(ftype.inner)))
    ptr_opt_field = (isinstance(ftype, OptionalType)
                     and not val_opt_container
                     and ftype.uses_pointer_repr())
    union_field = (not isinstance(ftype, OptionalType)
                   and _eligible_ptr_union(ftype, lc.analyzer) is not None)
    # An Own[container] param NAME (the property-setter shape the container
    # gate names): the write IS its routed read -- `this->_items =
    # std::move(v);` -- so the own_read fence (which guards bare reads with
    # no arm) does not apply at this sink.
    _ts_own_container = False
    if isinstance(tail_src, TpyName) and tail_src.name in declared:
        _ts_b = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[tail_src.name])))
        _ts_own = unwrap_optional_own(_ts_b)
        if _ts_own is not None:
            _ts_own_container = _f1_container_ref(
                unwrap_readonly(_ts_own.wrapped))
    lowered = _lower_expr(
        tail_src, lc, declared,
        use=(_ExprUse(result=_ExprResultUse.RECEIVER)
             if ptr_src
             else _ExprUse(pos=SinkPos.FIELD_WRITE,
                           forms=_slot_lift_forms(ptr_opt_field,
                                                  union_field))),
        allow_unrouted_name=_ts_own_container,
        # A FIELD source of the same Optional is consumed WHOLE (the
        # `std::optional<T>` member copies bare); the read must not take the
        # narrowing deref a value position gets.
        allow_whole_optional=(ptr_opt_field and not ptr_src
                              and isinstance(tail_src, TpyFieldAccess)),
        allow_union_divergent=union_divergent_ok)
    mv = peeled is None and _is_move_source(tail_src, lc)
    # A storage-form source of the field's own type needing no move is a
    # bare copy (`field = v`); the borrow->storage convert would be a no-op
    # (validate.py rejects it). This is the value-bound `Own[T]` field
    # write: the param is passed by value and copied here -- unlike the
    # ctor MIL, which moves. A whole-Optional
    # FIELD read is `std::optional<T>` member STORAGE (the read arm's VALUE
    # tag is the str/bytes view-vs-owned axis, which says nothing here), so
    # it copies bare; the `ptr_to_optional` lift belongs to the borrow `T*`
    # sources.
    whole_opt_field = (ptr_opt_field
                       and isinstance(tail_src, TpyFieldAccess))
    cnv_t = (unwrap_readonly(ftype.inner) if val_opt_container
             else ftype)
    if (not mv and lowered.result_type == cnv_t
            and (lowered.form is Form.STORAGE or whole_opt_field
                 # A NoneType source has no borrow form (monostate), so
                 # any form is already the storage value -- bare copy.
                 or isinstance(unwrap_readonly(cnv_t), NoneType))):
        return lowered
    if mv and lowered.result_type == cnv_t and lowered.form is Form.STORAGE:
        # An already-storage source at last use (an `Own[P | None]` param
        # name): the sink absorbs the whole optional -- `field =
        # std::move(p);` -- never the `ptr_to_optional_move` lift, which is
        # the borrow `T*` sources' spelling.
        return THIRMove(value=lowered, result_type=cnv_t, form=Form.STORAGE,
                        loc=loc)
    # The union STORAGE conversion can never move (to_value_variant takes
    # the ptr-variant by const& and deref-copies), so keep the node's move
    # flag a real-move claim for future consumers -- today's union render
    # ignores it, so this is invariant hygiene, not a render change. The
    # THIRMove arm above is untouched: a storage-form same-type source
    # still moves whole.
    return THIRFormConvert(result_type=cnv_t, value=lowered,
                           form=Form.STORAGE, move=mv and not union_field,
                           materialize=_object_source_materialize(
                               lowered, lc.analyzer),
                           loc=loc)


class _TupleRow(Enum):
    LITERAL = auto()      # spelled value-form brace-init
    BORROW_NAME = auto()  # borrow tuple param: the `tuple_to_storage` lift
    STORAGE_SRC = auto()  # subscript / field / storage name: bare copy


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
            or _nested_tuple_field_literal_write_ok(stmt, declared, analyzer)
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
        # A NESTED-storage tuple literal assigns its bare spelled
        # brace-init (no outer wrap -- the outer has no borrow form);
        # nested members carry their own per-level lifts.
        nested_t = (None if slot_t is not None
                    else _nested_storage_tuple(ftype, analyzer))
        if slot_t is None:
            slot_t = nested_t
        if slot_t is None:
            return None
        return _TupleFieldPlan(_TupleRow.LITERAL, ftype, slot_t,
                               value_tuple=(vt_field is not None
                                            or nested_t is not None))
    ft = _f1_tuple(ftype, analyzer)
    if ft is not None and _storage_tuple_write_source(
            stmt.value, ft, lc.storage_tuple_locals, analyzer):
        return _TupleFieldPlan(_TupleRow.STORAGE_SRC, ftype)
    return _TupleFieldPlan(_TupleRow.BORROW_NAME, ftype)


def _lower_tuple_field(stmt: TpyAssign, plan: _TupleFieldPlan, lc: _LowerCtx,
                       declared: dict[str, TpyType], loc) -> THIRAssign:
    if plan.row is _TupleRow.STORAGE_SRC:
        # A storage-form source copies bare into the field's own storage
        # (`this->pair = ::tpy::__getitem__(items, 0);` / `= other.pair;`
        # / `= g_pair;`) -- `needs_tuple_storage_lift` is False, no wrap.
        v = stmt.value
        if isinstance(v, TpyFieldAccess):
            fvalue: THIRExpr = _lower_field_source(v, lc, declared)
        elif isinstance(v, TpySubscript):
            fvalue = _lower_expr(
                v, lc, declared,
                use=_ExprUse(result=_ExprResultUse.STORAGE,
                             pos=SinkPos.FIELD_WRITE, forms=_ONLY_TUPLE_SOURCE))
        else:  # a storage-form tuple name (loop var / seeded global)
            fvalue = _lower_expr(v, lc, declared)
        _witness("field_write.tuple_storage_copy")
        return THIRAssign(
            target=_lower_field_write_target(stmt, lc, declared),
            value=fvalue, loc=loc)
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
        # An assign-narrowed same-union NAME (`new_pet: Dog | Cat = Cat(..);
        # z.pet = new_pet`): the sink is the UNION slot, so the read consumes
        # the whole variant through the to_value_variant lift -- the
        # member-typed-sink miscompile the divergent fence guards cannot
        # arise (the container-literal elem row's twin).
        v = stmt.value
        vb = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            declared[v.name])))
              if isinstance(v, TpyName) and v.name in declared else None)
        divergent_ok = (vb is not None and vb == plan.union_t
                        and v.name in lc.ptr_variant_locals
                        and _witness("field_write.union_name_lift"))
        fvalue = _lower_tail_value(stmt, plan.ftype, lc, declared, loc,
                                   union_divergent_ok=divergent_ok)
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
class _AnyFieldPlan:
    """An `Any` FIELD write: the default field assign with the value's
    sema-inserted `into_any` coerce rendered by `_lower_into_any`'s `{0}`
    template (`h.payload = ::tpy::make_any(n);`; a movable inner name's last
    use moves the whole make_any value), or an
    already-Any NAME copied bare. Same value slice as the Any-dict setitem
    row:
    placeholder-transparent inners only (bare declared name / str/int
    literal); container-literal inners reject."""
    ftype: TpyType


def _classify_any(stmt: TpyAssign, lc: _LowerCtx,
                  declared: dict[str, TpyType],
                  pointers: AbstractSet[str]) -> _AnyFieldPlan | None:
    analyzer = lc.analyzer
    ftype = analyzer.get_expr_type(stmt.target)
    if not _statements._is_any_type(ftype):
        return None
    narrowed = lc.narrow.narrowed.keys()
    if _statements._any_write_value_shape(stmt.value, declared, pointers,
                                          narrowed, analyzer) is None:
        return None
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return None
    return _AnyFieldPlan(ftype)


def _lower_any_field(stmt: TpyAssign, plan: _AnyFieldPlan, lc: _LowerCtx,
                     declared: dict[str, TpyType], loc) -> THIRAssign:
    _witness("field_write.any")
    value = _lower_expr(stmt.value, lc, declared)
    if _is_move_source(stmt.value, lc):
        # The move peels the coerce and hands make_any's result to the
        # field as an rvalue. The SOURCE name is still copy-constructed into
        # make_any's by-value parameter, so a last-use write copies the
        # payload once (BUGS.md#any-field-write-copies-source).
        value = THIRMove(result_type=plan.ftype, value=value,
                         form=Form.STORAGE, loc=loc)
    return THIRAssign(
        target=_lower_field_write_target(stmt, lc, declared),
        value=value, loc=loc)


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
    (_classify_ref, _lower_ref_field),
    (_classify_tuple, _lower_tuple_field),
    (_classify_union, _lower_union_field),
    (_classify_opt_lift, _lower_opt_lift_field),
    (_classify_any, _lower_any_field),
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
