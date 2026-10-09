"""Structural form validator: a lowering-time check the snapshots cannot make.

Snapshot parity is corpus-observational -- a node carrying a wrong `form` tag
survives it unless some case happens to RENDER the difference (a passthrough
defaulting `form=VALUE` over a STORAGE source emits identical C++). Wherever
form drives a real conversion this walk makes a form lie fail loudly at the
function that carries it, instead of waiting for a corpus witness.

Node-local checks:
  * `THIRFormConvert` must convert -- change the form or the (family-internal)
    result type. A no-op convert is a lie: emit would render a conversion
    helper around an already-converted value.
  * `THIRCoerce` is an emit passthrough -- it must carry its inner form,
    EXCEPT the view-target disposition (str_to_strview / string_to_strview):
    the result is a view into the source's buffer whatever the source's form,
    so lowering sets BORROW itself.

Sink-position checks (where borrow/storage conversions become
load-bearing across union slots):
  * A field write into a POINTER-LIFTED storage slot (pointer-repr Optional /
    pointer-variant union / pointer-repr tuple) never takes a BORROW value --
    the borrow->storage converts (`ptr_to_optional` / `to_value_variant` /
    `tuple_to_storage`) must have wrapped it. A plain record borrow (`T&`)
    copy-constructs implicitly and form alone cannot tell `T&` from `T*`, so
    plain-record sinks are left unchecked.
  * A MIL cell's value gets the value-side version of the same test (the ctor
    node carries no field types; an unconverted Optional borrow's
    `result_type` is the pointee record, a known blind spot).
  * A BORROW return value requires a borrow-legal return type: a non-value
    type (pointer/pointer-variant), a pointer-repr tuple, or a str/bytes VIEW
    (a `std::string_view` / span return is a legitimate borrow of a value
    type) -- or else a borrow whose OWN type is a value record, whose `T&`
    render copy-constructs into the by-value `T` slot.
`THIRBytesLiteral`'s render verdict rides the `form` tag rather than a bespoke
`owned` flag, so bytes literals sit on the validated axis like every other
expression.

Every lowered body is validated, whatever its shape: ordinary functions and
constructors through `validate_function` / `validate_constructor`, and the
resumable-frame bodies -- whose leaves the skeleton holds apart in seam
tables rather than one linear body -- through `validate_resumable_body`.
A module's callee definitions are checked together by
`validate_definitions`, once the codegen pass has lowered every function
body of the module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from contextvars import ContextVar

from ..codegen_cpp.forms import (LocalBinding, LoopBinding, is_plain_nonvalue,
                                 is_ptr_variant_union, loop_binding_kind)
from ..type_def_registry import (
    ParamPassing,
    is_basic_slice_type, is_bytearray_type, is_bytes_type, is_bytes_view_type,
    is_slice_type, is_span, is_str_type, is_str_view_type, is_string_type,
    zero_value_of,
)
from ..typesys import pointer_repr_optional
from ..typesys import (
    AnyType, NominalType, OptionalType, OwnType, PtrType, ReadonlyType, Representation,
    TupleType, receiver_neutral_return,
    UnionType, TpyType,
    TypeParamRef,
    is_inert_leaf, return_representation, is_void_like_type, unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
)
from .scalar_leaves import (
    binds_cursor, binds_element, container_view, declared_members, holds_elements, leaf_constant, leaf_global,
    modeled_ancestors, native_container_subject, native_container_type, optional_record_field, owned_leaf,
    plain_record_element, readonly_elements, storage_leaf,
)
from .nodes import (
    FLUSHING_REBIND_KINDS, Form, THIRArgTemp, THIRAssign, THIRCall, THIRChainedCompareStmtExpr,
    THIRCoerce, THIRConstructor, THIRCopy, THIRMove,
    THIRCtorCall, THIRErrorReturnBind, THIRErrorReturnDiscard,
    THIRErrorReturnUnwrap, THIRExprStmt, THIRFieldAccess, THIRFormConvert,
    THIRBinOp, THIRExpr, THIRForIterProto, THIRFunction, THIRIf,
    THIRIfExpr, THIRMethodCall, declared_param_type, effective_params,
    THIRNode, THIRName, THIRInplaceContainerOp, THIRParam, THIRWhile, THIRModuleVar, THIRWalrus, THIRGlobalBinding,
    THIRPrint, THIRRaise, THIRReturn, THIRSetItem, THIRSliceAssign,
    THIRFinallyDeferredReturn, DeferredPartKind,
    THIRSubscript, THIRTupleUnpack, THIRCoroHandleMove, THIRMatch,
    THIRMatchBinding, THIRMatchFoldBind, THIRWith, THIRWithItem,
    WithTargetArm,
    THIRFrameSlotWrite,
    THIRPtrLocalDecl, THIRPtrLocalRebind, THIRResumableBody, THIRSelf, PtrSlotKind,
    THIRUnionArgLift, THIRValueSelect, THIRVarDecl,
    THIRBorrowedRecord, THIRBorrowTupleLiteral, THIRLiteral, THIRTupleLiteral, THIRTupleLayout,
    THIROwnedRecord, THIRStoragePlacement, THIRTupleStorageAlias,
    THIRNativeContainer, THIRNativeIteration, THIRForEach, THIRForRange,
    THIROptionalLayout, THIROptionalRead,
    THIRUnionLayout, THIRUnionTest, THIRUnionExtraction, THIRUnionLiteral, THIRWrapperDefault,
    THIRIsinstance, THIRNarrowAlias, THIRNarrowedRead,
    THIRResolvedCallee, THIRFunctionIdentity, THIRCallableSignature,
    THIRStubCallee, THIRStubContract, THIRStubIdentity,
    THIRLambda, THIRNestedDef, THIRClosureIdentity, THIRClosureKind,
    THIRCapture, THIRCaptureSlot, THIRCaptureSourceKind, THIRCaptureRelation,
    THIRArrayComprehension, THIRComprehensionBlock, THIRCompInsert,
    hoists_declaration, record_rvalue_storage,
)
from .temp_plan import if_chain


class THIRValidationError(Exception):
    """A lowered node violates a THIR structural invariant -- a lowering bug,
    never an unsupported shape (those must raise during lowering, not produce
    inconsistent THIR)."""


def _user_container_call(node: object, container: TpyType) -> bool:
    """A user call returning the native container `container` by reference."""
    return (isinstance(node, (THIRCall, THIRMethodCall)) and node.resolved_callee is not None
            and node.resolved_callee.signature.return_representation is Representation.REFERENCE
            and native_container_subject(node.resolved_callee.signature.return_type) == container)


def _check_native_container(owner: str, node: object, fact: THIRNativeContainer, typ: TpyType) -> None:
    """A container or container view fact names the element its stub
    declares (`declared_members`), at the access the type and binding give."""
    bare = native_container_subject(typ)
    members = declared_members(bare)
    if (not isinstance(fact, THIRNativeContainer) or fact.type != bare or members is None
            or type(fact.readonly) is not bool
            or isinstance(unwrap_ref_type(typ), ReadonlyType) and not fact.readonly):
        _fail(owner, node, "invalid native container fact")
    member = fact.element
    declared, value, _ = members
    view = container_view(bare)
    # A view yielding both members at once binds no single element.
    if view and not binds_cursor(bare):
        _fail(owner, node, "invalid native container fact")
    if value is not None and not (storage_leaf(value) or owned_leaf(value)):
        _fail(owner, node, "invalid native container arguments")
    if view:
        # A view's element access is its element argument's.
        if readonly_elements(bare) and not fact.readonly:
            _fail(owner, node, "invalid native element fact")
        declared = unwrap_readonly(declared)
    element_type = member.type if isinstance(member, THIRBorrowedRecord) else member
    if (element_type != declared
            or not (not isinstance(member, THIRBorrowedRecord)
                    and (storage_leaf(element_type) or owned_leaf(element_type))) and not (
                isinstance(member, THIRBorrowedRecord) and plain_record_element(member.type)
                and value is None and member.readonly is fact.readonly)):
        _fail(owner, node, "invalid native element fact")


def _check_tuple(owner: str, node: object, layout: THIRTupleLayout, typ: TpyType) -> None:
    readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
    typ = unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
    if (not isinstance(layout, THIRTupleLayout) or not isinstance(typ, TupleType)
            or len(layout.elements) != len(typ.element_types)):
        _fail(owner, node, "tuple layout disagrees with its type")
    for member, element in zip(layout.elements, typ.element_types):
        if isinstance(member, (THIRBorrowedRecord, THIROwnedRecord)):
            valid = (isinstance(member.type, NominalType)
                     and member.type.qualified_name() is not None
                     and not member.type.type_args and not member.type.is_protocol
                     and not is_inert_leaf(member.type)
                     and type(member.readonly) is bool
                     and unwrap_readonly(element) == member.type
                     and (not readonly and not isinstance(element, ReadonlyType) or member.readonly))
        else:
            valid = storage_leaf(member) and element == member
        if not valid:
            _fail(owner, node, "invalid tuple member fact")
    if any(isinstance(m, THIROwnedRecord) for m in layout.elements):
        if not isinstance(node, (THIRVarDecl, THIRTupleLiteral)):
            _fail(owner, node, "owned tuple fact needs local constructor literal")


def _iter_children(node: THIRNode):
    for f in dataclasses.fields(node):
        v = getattr(node, f.name)
        if isinstance(v, THIRNode):
            yield v
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, THIRNode):
                    yield item
                elif dataclasses.is_dataclass(item) and not isinstance(item, type):
                    # MIL/base-init cells (THIRMilInit / THIRBaseInit) are
                    # plain dataclasses holding THIR exprs.
                    yield from _iter_children(item)


def _deferred_leaf_parts(parts):
    for part in parts:
        if part.kind is DeferredPartKind.TUPLE:
            yield from _deferred_leaf_parts(part.parts)
        else:
            yield part


def _fail(owner: str, node: THIRNode, why: str) -> None:
    loc = getattr(node, "loc", None)
    where = f" at {loc}" if loc is not None else ""
    raise THIRValidationError(
        f"{owner}: {type(node).__name__}{where}: {why}")


def _check_optional(owner: str, node: THIRNode, layout: THIROptionalLayout,
                    typ: TpyType | None = None) -> None:
    if not isinstance(layout, THIROptionalLayout):
        _fail(owner, node, "invalid optional layout")
    payload = layout.payload
    if isinstance(payload, THIRBorrowedRecord):
        valid = (isinstance(payload.type, NominalType) and payload.type.qualified_name() is not None
                 and not payload.type.type_args and not payload.type.is_protocol
                 and not is_inert_leaf(payload.type) and type(payload.readonly) is bool)
        inner = payload.type
    else:
        valid = storage_leaf(payload)
        inner = payload
    if not valid:
        _fail(owner, node, "invalid optional payload")
    if typ is not None:
        outer_readonly = unwrap_readonly(unwrap_ref_type(typ)) != unwrap_ref_type(typ)
        typ = unwrap_readonly(unwrap_ref_type(typ))
        if (not isinstance(typ, OptionalType) or typ.force_pointer_repr
                or unwrap_readonly(typ.inner) != inner):
            _fail(owner, node, "optional layout disagrees with type")
        if (isinstance(payload, THIRBorrowedRecord) and not payload.readonly
                and (outer_readonly or unwrap_readonly(typ.inner) != typ.inner)):
            _fail(owner, node, "optional layout increases access")


def _check_union(owner: str, node: object, layout: THIRUnionLayout,
                 typ: TpyType | None = None) -> None:
    if (not isinstance(layout, THIRUnionLayout) or not isinstance(layout.type, UnionType)
            or len(layout.elements) != len(layout.type.members) or len(layout.elements) < 2):
        _fail(owner, node, "invalid union layout")
    if typ is not None and unwrap_readonly(unwrap_ref_type(typ)) != layout.type:
        _fail(owner, node, "union layout disagrees with type")
    kinds = set()
    for payload, member in zip(layout.elements, layout.type.members):
        if payload is None:
            if not is_void_like_type(member):
                _fail(owner, node, "invalid union absence alternative")
        elif isinstance(payload, THIRBorrowedRecord):
            _check_optional(owner, node, THIROptionalLayout(payload))
            if (payload.type != unwrap_readonly(member) or not payload.readonly
                    and (member != payload.type or typ is not None and unwrap_readonly(typ) != typ)):
                _fail(owner, node, "union layout increases access or changes type")
            kinds.add("reference")
        else:
            if not storage_leaf(payload) or payload != member:
                _fail(owner, node, "invalid union scalar alternative")
            kinds.add("scalar")
    if len(kinds) != 1:
        _fail(owner, node, "mixed or empty union layout")


def _signature_problem(signature: object) -> bool:
    if (not isinstance(signature, THIRCallableSignature)
            or not isinstance(signature.param_types, tuple)
            or not all(isinstance(t, TpyType) for t in signature.param_types)
            or not isinstance(signature.return_type, TpyType)):
        return True
    passings = signature.passings
    if passings is not None and (
            not isinstance(passings, tuple) or len(passings) != len(signature.param_types)
            or not all(isinstance(p, ParamPassing) for p in passings)):
        return True
    return (signature.return_representation is not None
            and signature.return_representation is not return_representation(signature.return_type))


def _check_callee(owner: str, node: object, fact: THIRResolvedCallee) -> None:
    if (not isinstance(fact, THIRResolvedCallee)
            or not isinstance(fact.identity, THIRFunctionIdentity)
            or not isinstance(fact.identity.module, str) or not fact.identity.module
            or not isinstance(fact.identity.name, str) or not fact.identity.name
            or not (fact.identity.owner is None or isinstance(fact.identity.owner, str) and fact.identity.owner)
            or _signature_problem(fact.signature)):
        _fail(owner, node, "invalid resolved callee")
    accessor = fact.identity.accessor
    signature = fact.signature
    # A getter binds the receiver alone, a setter the receiver and the value.
    if (accessor not in (None, "fget", "fset")
            or accessor is not None and (fact.identity.owner is None
                                         or len(signature.param_types) != (1 if accessor == "fget" else 2))):
        _fail(owner, node, "invalid accessor identity")
    # The body of a follows-receiver callable is certified against a const receiver.
    if type(signature.result_follows_receiver) is not bool or signature.result_follows_receiver and (
            fact.identity.owner is None or signature.passings is None
            or signature.passings[0] is not ParamPassing.CONST_REF):
        _fail(owner, node, "follows-receiver signature needs a const receiver")
    # One value at the definition and every call: the result that const
    # receiver binds. A call derives its own access from its receiver.
    if (signature.result_follows_receiver and signature.borrowed_result is not None
            and not signature.borrowed_result.readonly):
        _fail(owner, node, "follows-receiver signature needs a readonly result")


def _check_method_callee(owner: str, node: THIRMethodCall, fact: THIRResolvedCallee) -> None:
    """A user method binds the call's receiver record as parameter 0, at
    one of the two accesses that record type is passed at, and renders as
    the plain member call."""
    _check_callee(owner, node, fact)
    types = fact.signature.param_types
    passings = fact.signature.passings
    receiver = unwrap_readonly(unwrap_ref_type(node.receiver.result_type))
    # Parameter 0 is the declaring record: the receiver's or a struct-base ancestor's.
    declaring = types[0] if types else None
    ancestors = modeled_ancestors(receiver) if declaring != receiver else ()
    if (fact.identity.owner is None
            or len(types) != len(node.args) + 1 or ancestors is None
            or declaring != receiver and declaring not in ancestors
            or not isinstance(declaring, NominalType) or declaring.qualified_name() != fact.identity.owner
            or passings is None or passings[0] not in (declaring.param_passing(False), declaring.param_passing(True))
            or not node.renders_plain_member_call):
        _fail(owner, node, "resolved method callee on incompatible call")
    # The receiver's emitted access: never wider than its type lends.
    access = node.receiver_access
    if (not isinstance(access, THIRBorrowedRecord) or access.type != receiver or type(access.readonly) is not bool
            or not access.readonly and unwrap_ref_type(node.receiver.result_type) != receiver):
        _fail(owner, node, "receiver access disagrees with the receiver")


def _stub_callee_problem(fact: THIRStubCallee) -> bool:
    return (not isinstance(fact, THIRStubCallee) or not isinstance(fact.identity, THIRStubIdentity)
            or not isinstance(fact.identity.qualified_name, str) or not fact.identity.qualified_name
            or _signature_problem(fact.signature)
            or fact.identity.param_types != fact.signature.param_types
            or fact.signature.passings is None or not isinstance(fact.signature.return_representation, Representation)
            or fact.signature.borrowed_result is not None
            or not (fact.contract is None or isinstance(fact.contract, THIRStubContract))
            or not isinstance(fact.readonly, tuple) or len(fact.readonly) != len(fact.signature.param_types)
            or not all(type(r) is bool for r in fact.readonly)
            or type(fact.mutates_elements) is not bool or type(fact.receiver) is not bool
            # A declared element write on a method that declares no write.
            or fact.mutates_elements and (not fact.readonly or fact.readonly[0]
                                          or fact.contract is THIRStubContract.PURE)
            or not isinstance(fact.bound_arguments, tuple)
            or not all(isinstance(t, TpyType) for t in fact.bound_arguments))


def _check_method_stub_callee(owner: str, node: THIRMethodCall | THIRSetItem, fact: THIRStubCallee,
                              receiver: TpyType, arity: int) -> None:
    """A native method stub binds the call's receiver as parameter 0, at
    the passing its type has under the method's receiver verdict."""
    if _stub_callee_problem(fact) or not fact.receiver:
        _fail(owner, node, "invalid stub callee")
    types = fact.signature.param_types
    if (len(types) != arity + 1 or types[0] != unwrap_send_sync(native_container_subject(receiver))
            or fact.signature.passings[0] is not types[0].param_passing(fact.readonly[0])):
        _fail(owner, node, "method stub callee disagrees with its receiver")


def _check_stub_callee(owner: str, node: THIRCall, fact: THIRStubCallee) -> None:
    if _stub_callee_problem(fact) or fact.receiver or fact.mutates_elements or fact.bound_arguments:
        _fail(owner, node, "invalid stub callee")
    # A stub renders through its bound symbol or template, never a TPy
    # callee spelling of its own.
    if (len(node.args) != len(fact.signature.param_types)
            or node.callee_expr is not None or node.template_args_cpp is not None
            or (node.native_name is None and node.cpp_template is None and node.callee_cpp is None)):
        _fail(owner, node, "stub callee on incompatible call")


def _check_captures(owner: str, node: THIRLambda | THIRNestedDef) -> None:
    identity = node.closure_id
    kind = THIRClosureKind.LAMBDA if isinstance(node, THIRLambda) else THIRClosureKind.NESTED_DEF
    if identity is not None and (
            not isinstance(identity, THIRClosureIdentity) or type(identity.index) is not int
            or identity.index < 0 or identity.kind is not kind):
        _fail(owner, node, "invalid closure identity")
    if node.captures is None:
        return
    if identity is None or not isinstance(node.captures, tuple):
        _fail(owner, node, "capture inventory needs closure identity and tuple")
    names: set[str] = set()
    for index, fact in enumerate(node.captures):
        if (not isinstance(fact, THIRCapture) or not isinstance(fact.slot, THIRCaptureSlot)
                or fact.slot.closure != identity or type(fact.slot.index) is not int or fact.slot.index != index
                or not isinstance(fact.source_name, str) or not fact.source_name or fact.source_name in names
                or not isinstance(fact.source_kind, THIRCaptureSourceKind)
                or not isinstance(fact.relation, THIRCaptureRelation) or type(fact.readonly) is not bool):
            _fail(owner, node, "invalid capture slot or source")
        names.add(fact.source_name)
        match fact.relation:
            case THIRCaptureRelation.SCALAR_BINDING | THIRCaptureRelation.SCALAR_SNAPSHOT:
                if (not storage_leaf(fact.type) or fact.source_kind is THIRCaptureSourceKind.RECEIVER
                        or (fact.relation is THIRCaptureRelation.SCALAR_SNAPSHOT and not fact.readonly)):
                    _fail(owner, node, "invalid scalar capture")
            case THIRCaptureRelation.RECORD_REFERENT | THIRCaptureRelation.RECEIVER_ALIAS:
                expected = (THIRCaptureSourceKind.PARAMETER
                            if fact.relation is THIRCaptureRelation.RECORD_REFERENT
                            else THIRCaptureSourceKind.RECEIVER)
                if (fact.source_kind is not expected or not isinstance(fact.type, NominalType)
                        or not fact.type.qualified_name() or fact.type.type_args or fact.type.is_protocol
                        or is_inert_leaf(fact.type)):
                    _fail(owner, node, "invalid reference capture")


def _check_hoists(owner: str, node: THIRIf | THIRWhile | THIRForRange | THIRForEach) -> None:
    for binding in node.hoisted_bindings:
        record = binding.optional_record_storage
        if record is not None:
            if (not isinstance(record, THIRBorrowedRecord) or record.readonly is not False
                    or record.type != binding.type or binding.initially_assigned is not False
                    or any(f is not None for f in (binding.borrowed_record, binding.optional_layout,
                                                   binding.tuple_layout, binding.union_layout,
                                                   binding.physical_default))):
                _fail(owner, node, "invalid optional record backing fact")
        if binding.optional_layout is not None:
            _check_optional(owner, node, binding.optional_layout, binding.type)
        if binding.union_layout is not None:
            _check_union(owner, node, binding.union_layout, binding.type)
        default = binding.physical_default
        if default is not None:
            optional = binding.optional_layout
            union = binding.union_layout
            scalar = (optional is not None and storage_leaf(optional.payload) and union is None
                      or union is not None and optional is None
                      and all(m is None or storage_leaf(m) for m in union.elements))
            first = union.elements[0] if union is not None else None
            expected = None if first is None else zero_value_of(first)
            # A None value only defaults the None alternative.
            if (not scalar or first is not None and expected is None
                    or binding.borrowed_record is not None or binding.tuple_layout is not None
                    or not isinstance(default, THIRWrapperDefault)
                    or type(default.alternative) is not int or default.alternative != 0
                    or type(default.value) is not type(expected) or default.value != expected):
                _fail(owner, node, "invalid physical wrapper default")


def _select_ref_type(t: TpyType) -> TpyType:
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    return unwrap_readonly(t.wrapped) if isinstance(t, OwnType) else t


# Whether the body being validated came out of the lowering, which stamps a
# record on every binding-emitting node (`statements.stamp_bindings`):
# there a node without one is a lowering bug; hand-built THIR may omit it.
_LOWERED: ContextVar[bool] = ContextVar("thir_validate_lowered", default=False)

# The pointer-local declaration shapes (`T*`), and the PtrSlotKinds that
# declare a pointer-variant union rather than a pointer.
_POINTER_DECLS = frozenset({LocalBinding.POINTER, LocalBinding.OPTIONAL_TO_PTR,
                            LocalBinding.REBIND_SLOT, LocalBinding.OPT_PTR_SLOT})
_UNION_SLOT_KINDS = frozenset({PtrSlotKind.UNION_NONE, PtrSlotKind.UNION_RVALUE,
                               PtrSlotKind.UNION_INLINE_SLOT,
                               PtrSlotKind.UNION_ADDR})
_POINTER_UNPACK_BINDS = frozenset({"ptr", "opt_ptr", "frame_ptr_elem",
                                   "frame_opt_ptr"})


def _parked_tuple_global(node: 'THIRPtrLocalDecl | THIRPtrLocalRebind',
                         rec) -> bool:
    """A module global held as a tuple of pointer slots whose write parks
    its value in a static slot of its own: the global's record holds every
    reference element through a pointer (`THIRBorrowedRecord`), none
    inline, and the node is one of the two parking writes."""
    layout = rec.tuple_layout
    if (rec.row != "global.module" or layout is None or layout.owns_records
            or not any(isinstance(m, THIRBorrowedRecord)
                       for m in layout.elements)):
        return False
    if isinstance(node, THIRPtrLocalRebind):
        return (node.kind is PtrSlotKind.GLOBAL_HOIST_RVALUE
                and node.tuple_cpp is not None)
    return (node.kind is PtrSlotKind.GLOBAL_RVALUE
            and isinstance(node.resolved_type, TupleType))


def _contract_problem(node: object, rec) -> 'str | None':
    """The first representation fact of a binding-emitting node its record
    contradicts. The checks read the record's fields, never its read form
    (a BORROW binding cannot tell `T&` from `T*`)."""
    if isinstance(node, THIRVarDecl):
        rep = node.cpp_local_representation
        if (rep is LocalBinding.REF_ALIAS) != rec.ref_alias:
            return f"a {rep} declaration against ref_alias={rec.ref_alias}"
        if rep in _POINTER_DECLS and not rec.pointer:
            return f"a {rep} declaration of a non-pointer binding"
        if rep is LocalBinding.REBIND_SLOT and not rec.rebind_slot:
            return "a rebind-slot declaration of a binding without one"
        if node.btuple_opt_borrow_cpp is not None and not rec.optional_borrow_tuple:
            return "an optional borrow tuple declared for a binding that is none"
        if rec.coro_frame and not (node.cpp_type or "").startswith("std::optional<"):
            return "a coroutine frame binding declared outside its optional"
        if rec.iterator_object and node.cpp_type not in ("auto", "auto&"):
            return "an iterator object declared with a spelled type"
        if rec.frame_slot:
            return "a local declaration of a frame slot"
        if (rep in (LocalBinding.REF_ALIAS, LocalBinding.POINTER,
                    LocalBinding.OPTIONAL_TO_PTR)
                and node.is_const != rec.const):
            return f"a {rep} declared const={node.is_const} against const={rec.const}"
        if (rec.tuple_layout is not None and node.tuple_layout is not None
                and node.tuple_layout != rec.tuple_layout):
            return "an owned tuple declared off its record's layout"
        if node.tuple_storage_alias is not None and not rec.storage_tuple:
            return "a storage-tuple alias of a binding that is no storage tuple"
        if ((rep in _POINTER_DECLS or rep is LocalBinding.REF_ALIAS)
                and (rec.storage_tuple or rec.value_opt is not None)):
            return f"a {rep} declaration of a storage tuple or value optional"
    elif isinstance(node, THIRPtrLocalDecl):
        if node.kind in _UNION_SLOT_KINDS:
            if not rec.ptr_variant:
                return f"a {node.kind} declaration of a non-pointer-variant binding"
        elif not (rec.pointer or _parked_tuple_global(node, rec)):
            return f"a {node.kind} declaration of a non-pointer binding"
    elif isinstance(node, THIRPtrLocalRebind):
        if node.kind in _UNION_SLOT_KINDS:
            if not rec.ptr_variant:
                return f"a {node.kind} reseat of a non-pointer-variant binding"
        elif not (rec.pointer or _parked_tuple_global(node, rec)):
            return f"a {node.kind} reseat of a non-pointer binding"
    elif isinstance(node, THIRErrorReturnBind):
        if node.ptr_rebind and not rec.pointer:
            return "a pointer error-return bind of a non-pointer binding"
    elif isinstance(node, THIRFrameSlotWrite):
        if not (rec.frame_slot or rec.coro_frame):
            return "an emplace into a binding that is no frame slot"
    elif isinstance(node, THIRCoroHandleMove):
        if not (rec.coro_frame or rec.frame_slot):
            return "a coroutine handle move into a binding holding no frame"
    elif isinstance(node, THIRForEach) and not node.hoist_loop_var:
        if node.consuming and not rec.movable:
            return "a consuming loop variable whose record is not movable"
    elif isinstance(node, THIRWithItem):
        arm = node.target_arm
        if arm in (WithTargetArm.PTR_DECL, WithTargetArm.ASSIGN_PTR) and not rec.pointer:
            return f"a {arm} with target that is no pointer"
        if arm is WithTargetArm.ASSIGN_OPT and not rec.optional_storage:
            return "an optional-storage with target that is no optional storage"
        if arm is WithTargetArm.FRAME_SLOT and not rec.frame_slot:
            return "a frame-slot with target that is no frame slot"
    elif isinstance(node, THIRMatchBinding):
        if node.mode == "frame_emplace" and not rec.frame_slot:
            return "a frame-slot match capture that is no frame slot"
    return None


def _contract_fail(owner: str, node: object, why: str) -> None:
    _fail(owner, node, f"binding contract: {why}")


def _check_contract(owner: str, node: object, name: 'str | None', rec) -> None:
    if name is None:
        return
    if rec is None:
        if _LOWERED.get():
            _contract_fail(owner, node, f"{type(node).__name__} {name!r} carries no record")
        return
    if rec.row == "unbound":
        return
    why = _contract_problem(node, rec)
    if why is not None:
        _contract_fail(owner, node, f"{type(node).__name__} {name!r} ({rec.row}): {why}")


def _check_binding_contracts(owner: str, node: object) -> None:
    """A binding-emitting node against the binding table's record it carries
    (`THIRVarDecl.binding` and its siblings)."""
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl, THIRPtrLocalRebind,
                         THIRErrorReturnBind, THIRFrameSlotWrite)):
        _check_contract(owner, node, node.name, node.binding)
    elif isinstance(node, THIRMatchFoldBind):
        _check_contract(owner, node, node.name_cpp, node.binding)
    elif isinstance(node, THIRCoroHandleMove):
        _check_contract(owner, node, node.target, node.binding)
    elif isinstance(node, (THIRForEach, THIRForIterProto, THIRForRange)):
        _check_contract(owner, node, node.var, node.binding)
    elif isinstance(node, THIRTupleUnpack):
        recs = node.bindings or (None,) * len(node.targets)
        if len(recs) != len(node.targets):
            _contract_fail(owner, node, "one record per unpack target")
        for i, (n, rec) in enumerate(zip(node.targets, recs)):
            _check_contract(owner, node, n, rec)
            bind = node.binds[i] if i < len(node.binds) else None
            if rec is not None and n is not None and rec.row != "unbound":
                if bind in _POINTER_UNPACK_BINDS and not rec.pointer:
                    _contract_fail(owner, node, f"unpack target {n!r} ({rec.row}) bound {bind} is no pointer")
                if bind == "frame_emplace" and not rec.frame_slot:
                    _contract_fail(owner, node, f"unpack target {n!r} ({rec.row}) emplaced is no frame slot")
    elif isinstance(node, THIRWith):
        for item in node.items:
            _check_contract(owner, item, item.target, item.binding)
    elif isinstance(node, THIRMatch):
        for arm in node.arms:
            for entry in arm.entries:
                for mb in (entry.binding, *entry.pre_bindings, *entry.field_bindings):
                    if mb is not None:
                        _check_contract(owner, mb, mb.name, mb.binding)


def _check_node(owner: str, node: THIRNode) -> None:
    _check_binding_contracts(owner, node)
    if (isinstance(node, (THIRIfExpr, THIRValueSelect))
            and node.form is Form.VALUE):
        # A prvalue `?:` of a reference type initializes its storage by
        # guaranteed elision only when every arm is that type: an arm of
        # another type would be converted into a temporary and copied or
        # sliced, with nothing in the source saying so.
        want = _select_ref_type(node.result_type)
        if not want.is_value_type():
            arms = ((node.then, node.orelse) if isinstance(node, THIRIfExpr)
                    else (node.lhs, node.rhs))
            if any(_select_ref_type(arm.result_type) != want for arm in arms):
                _fail(owner, node, "prvalue select arm is not the select's own type")
    if (fact := record_rvalue_storage(node)) is not None:
        # A call's storage is its callee's result handed over, never a borrow.
        source = (not node.brace_init if isinstance(node, THIRCtorCall)
                  else node.resolved_callee is not None
                  and node.resolved_callee.signature.hands_over(node.result_type))
        if (not isinstance(fact, THIROwnedRecord) or not isinstance(fact.type, NominalType)
                or is_inert_leaf(fact.type) or not fact.type.qualified_name()
                or fact.type.type_args or fact.type.is_protocol or fact.type != node.result_type
                or fact.readonly is not False or node.form is not Form.STORAGE or not source):
            _fail(owner, node, "full-expression storage disagrees with its record rvalue")
    if isinstance(node, THIRVarDecl) and node.native_container is not None:
        _check_native_container(owner, node, node.native_container, node.resolved_type)
        others = (node.alias_binding, node.storage_borrow, node.owned_storage, node.tuple_layout,
                  node.tuple_storage_alias, node.optional_layout, node.union_layout, node.storage_placement)
        if node.form is not Form.BORROW:
            # Container storage of the local's own, from an initializer
            # yielding the declared container.
            if (node.init is None or node.resolved_type != node.native_container.type
                    or not native_container_type(node.resolved_type)
                    or native_container_subject(node.init.result_type) != node.native_container.type
                    or node.is_const is not node.native_container.readonly
                    or any(f is not None for f in others)):
                _fail(owner, node, "owned native container disagrees with binding")
        elif (not (isinstance(node.init, THIRName) and node.init.form is Form.BORROW
                     or isinstance(node.init, THIRFieldAccess) and node.init.field_identity is not None
                     and unwrap_readonly(node.init.field_identity.type) == node.native_container.type
                     or _user_container_call(node.init, node.native_container.type))
                or unwrap_readonly(unwrap_ref_type(node.init.result_type)) != node.native_container.type
                or node.is_const is not node.native_container.readonly
                or any(f is not None for f in others)):
            _fail(owner, node, "native container alias disagrees with binding")
    if isinstance(node, (THIRIf, THIRWhile, THIRForRange, THIRForEach)):
        _check_hoists(owner, node)
    if isinstance(node, THIRIf):
        # The emitter prints only the chain head's predecls: a flattened
        # `else if` link has no block of its own to hold one.
        for link in if_chain(node)[1:]:
            if link.hoist_decls or link.hoist_slots:
                _fail(owner, link, "elif link carries a predeclaration")
    if isinstance(node, THIRForEach) and node.iteration is not None:
        fact = node.iteration
        source = node.iterable
        # A named container, a container field of a named record, or a
        # container view a method stub returns.
        view = isinstance(source, THIRMethodCall) and source.stub_callee is not None
        call = isinstance(fact, THIRNativeIteration) and isinstance(fact.source, THIRNativeContainer) and (
            _user_container_call(source, fact.source.type))
        place = isinstance(source, THIRName) or (
            isinstance(source, THIRFieldAccess) and source.field_identity is not None
            and isinstance(source.receiver, (THIRName, THIRSelf))
            and isinstance(fact, THIRNativeIteration) and isinstance(fact.source, THIRNativeContainer)
            and unwrap_readonly(source.field_identity.type) == fact.source.type
            and (not isinstance(source.field_identity.type, ReadonlyType) or fact.source.readonly))
        if (not isinstance(fact, THIRNativeIteration) or not ((place or call) and node.iterable_lvalue or view)
                or node.consuming or node.str_literal_iterable
                or fact.binding is not loop_binding_kind(node.elem_type, node.const_loop_var,
                                                         hoisted=node.hoist_loop_var)):
            _fail(owner, node, "native iteration fact disagrees with emitted binding")
        _check_native_container(owner, node, fact.source, node.iterable.result_type)
        # A stub's result is a place only as a view of its receiver's elements.
        if view and not container_view(fact.source.type):
            _fail(owner, node, "native iteration fact disagrees with emitted binding")
        if not binds_cursor(fact.source.type):
            _fail(owner, node, "native iteration source binds no element")
        element = fact.source.element
        if not binds_element(node.elem_type, (
                element.type if isinstance(element, THIRBorrowedRecord) else element)):
            _fail(owner, node, "native iteration element mismatch")
    if isinstance(node, (THIRLambda, THIRNestedDef)):
        _check_captures(owner, node)
    if (isinstance(node, (THIRCall, THIRMethodCall))
            and node.resolved_callee is not None and node.stub_callee is not None):
        _fail(owner, node, "call carries both a resolved and a stub callee")
    if isinstance(node, THIRCall) and node.stub_callee is not None:
        _check_stub_callee(owner, node, node.stub_callee)
    if isinstance(node, THIRMethodCall) and node.stub_callee is not None:
        _check_method_stub_callee(owner, node, node.stub_callee, node.receiver.result_type, len(node.args))
    if isinstance(node, THIRSetItem) and node.stub_callee is not None:
        target = node.target
        if not isinstance(target, THIRSubscript):
            _fail(owner, node, "setitem stub callee needs a subscript target")
        _check_method_stub_callee(owner, node, node.stub_callee, target.receiver.result_type, 2)
    if isinstance(node, THIRCall) and node.resolved_callee is not None:
        _check_callee(owner, node, node.resolved_callee)
        if (len(node.args) != len(node.resolved_callee.signature.param_types)
                or node.resolved_callee.identity.owner is not None
                or any(value is not None for value in (
                    node.native_name, node.cpp_template, node.callee_expr, node.template_args_cpp))):
            _fail(owner, node, "resolved callee on incompatible call")
    if isinstance(node, THIRMethodCall) and (node.receiver_access is None) != (node.resolved_callee is None):
        _fail(owner, node, "receiver access needs exactly a resolved callee")
    if isinstance(node, THIRMethodCall) and node.resolved_callee is not None:
        _check_method_callee(owner, node, node.resolved_callee)
    if isinstance(node, (THIRName, THIRModuleVar, THIRWalrus)) and node.global_binding is not None:
        fact = node.global_binding
        if (not isinstance(fact, THIRGlobalBinding) or not fact.module or not fact.name
                or not (leaf_global(fact.type) and (node.form is Form.VALUE or owned_leaf(fact.type)))
                or fact.type != node.result_type or type(fact.writable) is not bool):
            _fail(owner, node, "invalid leaf global binding")
        if isinstance(node, THIRWalrus) and not fact.writable:
            _fail(owner, node, "global walrus needs writable binding")
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl, THIRPtrLocalRebind, THIRAssign)):
        if isinstance(node, THIRAssign) and node.optional_record_assignment is not None:
            fact = node.optional_record_assignment
            match node.value:
                case THIRCtorCall() | THIRCopy():
                    valid_form = node.value.form is Form.STORAGE
                case THIRMove(value=source):
                    valid_form = node.value.form is source.form
                case _:
                    valid_form = False
            if (not isinstance(fact, THIRBorrowedRecord) or fact.readonly is not False
                    or not isinstance(node.target, THIRName)
                    or node.target.result_type != fact.type
                    or unwrap_readonly(unwrap_ref_type(node.value.result_type)) != fact.type
                    or not valid_form
                    or any(f is not None for f in (node.optional_layout, node.union_layout,
                                                   node.alias_binding, node.storage_borrow, node.rebind_storage))):
                _fail(owner, node, "invalid optional record assignment fact")
        if node.union_layout is not None:
            _check_union(owner, node, node.union_layout,
                         node.resolved_type if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) else None)
        if node.union_literal is not None:
            fact = node.union_literal
            if (not isinstance(fact, THIRUnionLiteral) or fact.layout != node.union_layout
                    or fact.layout is None or type(fact.alternative) is not int
                    or not 0 <= fact.alternative < len(fact.layout.elements)):
                _fail(owner, node, "invalid union literal fact")
            value = node.init if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) else node.value
            if isinstance(value, THIRCoerce) and value.coercion_name == "int_literal_to_fixed_int" and value.wrap is None:
                value = value.expr
            member = fact.layout.elements[fact.alternative]
            if (not isinstance(value, THIRLiteral) or type(value.value) is not type(fact.value)
                    or value.value != fact.value or not (
                        member is None and fact.value is None
                        or leaf_constant(member, fact.value))):
                _fail(owner, node, "union literal payload mismatch")
    if isinstance(node, THIRName) and node.union_read is not None:
        _check_union(owner, node, node.union_read)
    if isinstance(node, (THIRIsinstance, THIRNarrowAlias, THIRNarrowedRead)):
        fact = node.union_test if isinstance(node, THIRIsinstance) else node.union_extraction
        if fact is not None:
            if not isinstance(fact, THIRUnionTest if isinstance(node, THIRIsinstance) else THIRUnionExtraction):
                _fail(owner, node, "invalid union selection fact")
            _check_union(owner, node, fact.layout)
            alternatives = fact.alternatives if isinstance(fact, THIRUnionTest) else (fact.alternative,)
            if (not fact.source or not alternatives or len(set(alternatives)) != len(alternatives)
                    or any(type(i) is not int or not 0 <= i < len(fact.layout.elements) for i in alternatives)):
                _fail(owner, node, "invalid union selection alternatives")
            if isinstance(fact, THIRUnionExtraction) and fact.layout.elements[fact.alternative] is None:
                _fail(owner, node, "absent union alternative has no payload")
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl, THIRPtrLocalRebind, THIRAssign)):
        if node.optional_layout is not None:
            _check_optional(owner, node, node.optional_layout,
                            node.resolved_type if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) else None)
    if isinstance(node, THIRName) and node.optional_read is not None:
        read = node.optional_read
        if not isinstance(read, THIROptionalRead) or type(read.extract) is not bool:
            _fail(owner, node, "invalid optional read")
        _check_optional(owner, node, read.layout)
        if node.opt_deref_check:
            _fail(owner, node, "checked optional read cannot claim plain extraction")
    if isinstance(node, THIRVarDecl) and node.tuple_storage_alias is not None:
        fact = node.tuple_storage_alias
        if (not isinstance(fact, THIRTupleStorageAlias) or not fact.source
                or not isinstance(node.init, THIRName) or node.init.name != fact.source
                or node.init.result_type != node.resolved_type
                or node.form is not Form.STORAGE or node.init.form is not Form.STORAGE
                or node.is_const
                or any(f is not None for f in (node.tuple_layout, node.storage_placement,
                    node.owned_storage, node.alias_binding, node.storage_borrow,
                    node.optional_layout, node.union_layout))):
            _fail(owner, node, "tuple storage alias disagrees with its binding")
        _check_tuple(owner, node, fact.layout, node.resolved_type)
        if not fact.layout.owns_records:
            _fail(owner, node, "tuple storage alias needs owned backing")
    if isinstance(node, (THIRVarDecl, THIRTupleLiteral, THIRBorrowTupleLiteral)):
        layout = node.tuple_layout
        if layout is not None:
            typ = node.resolved_type if isinstance(node, THIRVarDecl) else node.result_type
            _check_tuple(owner, node, layout, typ)
            if not isinstance(node, THIRVarDecl) and len(node.elements) != len(layout.elements):
                _fail(owner, node, "tuple capture arity mismatch")
            if any(isinstance(m, THIROwnedRecord) for m in layout.elements):
                literal = node.init if isinstance(node, THIRVarDecl) else node
                if (not isinstance(literal, THIRTupleLiteral) or literal.tuple_layout != layout
                        or len(literal.elements) != len(layout.elements)
                        or any(isinstance(m, THIRBorrowedRecord) for m in layout.elements)):
                    _fail(owner, node, "owned tuple needs consistent constructor literal")
                if isinstance(node, THIRVarDecl) and (
                        node.form is not Form.STORAGE or node.storage_placement is not THIRStoragePlacement.SCOPE
                        or any(f is not None for f in (node.owned_storage, node.alias_binding, node.storage_borrow,
                                                      node.optional_layout, node.union_layout))):
                    _fail(owner, node, "owned tuple needs direct storage declaration")
                for member, value in zip(layout.elements, literal.elements):
                    if isinstance(member, THIROwnedRecord) and (
                            not isinstance(value, THIRCtorCall) or value.result_type != member.type
                            or value.form is not Form.STORAGE):
                        _fail(owner, node, "owned tuple member needs matching constructor")
    if isinstance(node, THIRSubscript) and node.tuple_index is not None:
        typ = unwrap_readonly(unwrap_ref_type(node.receiver.result_type))
        if (not isinstance(typ, TupleType) or type(node.tuple_index) is not int
                or not 0 <= node.tuple_index < len(typ.element_types)
                or not isinstance(node.index, THIRLiteral)
                or type(node.index.value) is not int or node.index.value != node.tuple_index):
            _fail(owner, node, "invalid normalized tuple index")
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) and node.owned_storage is not None:
        fact = node.owned_storage
        typ = unwrap_readonly(unwrap_ref_type(node.resolved_type))
        if isinstance(typ, OptionalType):
            if (not isinstance(node, THIRPtrLocalDecl) or node.kind is not PtrSlotKind.OPT_RVALUE
                    or not isinstance(node.init, THIRCtorCall) or node.init.form is not Form.STORAGE
                    or node.init.result_type != fact.type
                    or node.optional_layout != THIROptionalLayout(fact)):
                _fail(owner, node, "owned optional storage disagrees with its payload")
            typ = unwrap_readonly(typ.inner)
        if (node.alias_binding is not None or node.init is None
                or typ != fact.type
                or type(fact.readonly) is not bool or node.is_const != fact.readonly):
            _fail(owner, node, "owned storage disagrees with its declaration")
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl, THIRPtrLocalRebind, THIRAssign)):
        fact = node.alias_binding
        if fact is not None:
            source = node.init if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) else node.value
            if isinstance(source, THIRFormConvert):
                if (source.form is not Form.BORROW or source.move
                        or source.materialize is not None or source.generic_return
                        or source.is_const != fact.reference.readonly
                        or unwrap_readonly(unwrap_ref_type(source.result_type)) != fact.reference.type):
                    _fail(owner, node, "alias binding has a non-borrow conversion")
                source = source.value
            name = source.name if isinstance(source, THIRName) else "self" if isinstance(source, THIRSelf) else None
            if (name is None or fact.source != name
                    or unwrap_readonly(unwrap_ref_type(source.result_type)) != fact.reference.type
                    or type(fact.reference.readonly) is not bool):
                _fail(owner, node, "alias binding disagrees with its source")
            if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)):
                if (unwrap_readonly(unwrap_ref_type(node.resolved_type)) != fact.reference.type
                        or node.is_const != fact.reference.readonly):
                    _fail(owner, node, "alias binding disagrees with its destination")
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl, THIRAssign, THIRPtrLocalRebind)):
        fact = node.storage_borrow
        if fact is not None:
            if (not isinstance(fact, THIRBorrowedRecord) or type(fact.readonly) is not bool
                    or node.alias_binding is not None or getattr(node, "owned_storage", None) is not None):
                _fail(owner, node, "invalid storage borrow fact")
            source = node.init if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)) else node.value
            if isinstance(source, THIRFormConvert):
                if (source.form is not Form.BORROW or source.move or source.materialize is not None
                        or source.generic_return or source.is_const != fact.readonly
                        or unwrap_readonly(unwrap_ref_type(source.result_type)) != fact.type):
                    _fail(owner, node, "storage borrow has a non-borrow conversion")
                source = source.value
            if isinstance(source, THIRFieldAccess):
                valid = (source.field_identity is not None and source.form is Form.STORAGE
                         and unwrap_readonly(source.field_identity.type) == fact.type)
            elif isinstance(source, THIRSubscript):
                typ = unwrap_readonly(unwrap_ref_type(source.receiver.result_type))
                valid = (source.form is Form.BORROW and source.deref
                         and isinstance(source.receiver, THIRName) and isinstance(typ, TupleType)
                         and type(source.tuple_index) is int and 0 <= source.tuple_index < len(typ.element_types)
                         and unwrap_readonly(unwrap_ref_type(typ.element_types[source.tuple_index])) == fact.type)
            else:
                valid = False
            if not valid or unwrap_readonly(unwrap_ref_type(source.result_type)) != fact.type:
                _fail(owner, node, "storage borrow disagrees with its source")
            if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)):
                if (unwrap_readonly(unwrap_ref_type(node.resolved_type)) != fact.type
                        or node.is_const != fact.readonly):
                    _fail(owner, node, "storage borrow disagrees with its destination")
    if isinstance(node, THIRFieldAccess) and node.field_identity is not None:
        fact = node.field_identity
        direct = isinstance(node.receiver, (THIRName, THIRSelf)) or (
            isinstance(node.receiver, THIRFieldAccess) and node.receiver.field_identity is not None) or (
            isinstance(node.receiver, THIRSubscript) and (
                node.receiver.tuple_index is not None or holds_elements(node.receiver.receiver.result_type))) or (
            isinstance(node.receiver, THIRNarrowedRead) and node.receiver.union_extraction is not None) or (
            record_rvalue_storage(node.receiver) is not None and storage_leaf(fact.type) and not node.is_arrow)
        typ = unwrap_readonly(fact.type)
        # A container member is reached in place like a record member.
        record = (isinstance(typ, NominalType) and not is_inert_leaf(typ)
                  and (not typ.type_args or native_container_type(typ)) and not typ.is_protocol)
        receiver = unwrap_readonly(unwrap_ref_type(node.receiver.result_type))
        # An Optional record member is its own storage: read as its payload
        # record once narrowed (`narrowed_deref`), else whole. Without the
        # flag the payload type is accepted too, at any position: an assign
        # target inside a narrowed region carries it while it writes the
        # whole storage (BUGS.md#narrowed-optional-field-assign-target-type),
        # and MIR keys on the flag, never on that type.
        payload = optional_record_field(fact.type)
        result = unwrap_readonly(unwrap_ref_type(node.result_type))
        # The declaring owner: the receiver's record or one of its struct-base ancestors.
        if (not direct or not fact.name
                or receiver != fact.owner and fact.owner not in (modeled_ancestors(receiver) or ())
                or (payload is not None and (node.form is not Form.STORAGE or result not in (
                    (payload,) if node.narrowed_deref else (typ, payload))))
                or (payload is None and not record and (not storage_leaf(fact.type)
                                                        or node.result_type != fact.type or node.form is not Form.VALUE))
                or (record and result != typ)):
            _fail(owner, node, "field identity disagrees with its access")
    if isinstance(node, (THIRFieldAccess, THIRMethodCall)):
        # A plain method's receiver read carries its own value-position
        # deref (`(*this)`), so the member reached THROUGH the pointer must
        # take the raw receiver -- `(*this)->x` is not valid C++. Keeping
        # this a structural rule is what stops the deref from drifting back
        # into a fact each consumer re-applies by hand.
        recv = node.receiver
        if (isinstance(recv, THIRSelf) and recv.deref
                and node.receiver_through_pointer):
            _fail(owner, node,
                  "dereferenced receiver behind an arrow member access")
    if isinstance(node, THIRCall):
        # Explicit template args ride only the plain / imported spellings: a
        # native or `cpp_template` callee spells its own arguments.
        if node.template_args_cpp and (node.native_name is not None
                                       or node.cpp_template is not None):
            _fail(owner, node,
                  "template_args_cpp combined with a native/template callee")
    if isinstance(node, THIRFormConvert):
        # `move` is part of the node's identity (its helper is a pure function of
        # family / form / is_const / move), so a same-form same-type convert that
        # carries a move is NOT a no-op -- it materializes `std::move(x)` (an
        # Own[T] param written into a `T` field is already STORAGE form, so the
        # move is the whole operation).
        cv = node.result_type
        cv = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(cv)))
              if cv is not None else None)
        # A plain-non-value BORROW convert is the `T&` -> reseatable `T*`
        # address-of lift, and BORROW spells BOTH of those for such a type,
        # so form + type cannot tell the two apart -- the same blind spot
        # the pointer-lifted field sink rule names. Its same-form same-type
        # shape is the lift doing its job, not a dead node.
        ref_to_ptr_lift = (node.form is Form.BORROW and cv is not None
                           and is_plain_nonvalue(cv))
        if (node.form is node.value.form
                and node.result_type == node.value.result_type
                and not node.move and not ref_to_ptr_lift):
            _fail(owner, node,
                  f"no-op form convert (form={node.form.name}, "
                  f"type={node.result_type})")
        if node.materialize:
            # The view->owned copy: only the view families own the render,
            # and a fresh buffer never moves.
            if not (cv is not None
                    and (is_str_type(cv) or is_string_type(cv)
                         or is_bytes_type(cv) or is_bytearray_type(cv))):
                _fail(owner, node,
                      f"materialize convert with non-view-family result "
                      f"{node.result_type}")
            if node.move:
                _fail(owner, node, "materialize convert carrying a move "
                                   "(a fresh buffer never moves)")
        if (cv is not None and is_bytearray_type(cv)
                and node.form is Form.STORAGE and node.materialize is None):
            # bytearray is the one view-family member whose (family, form)
            # pair does NOT determine the render (see the node docstring):
            # the meaning must be decided at lowering.
            _fail(owner, node,
                  "bytearray STORAGE convert without an explicit "
                  "materialize decision")
    elif isinstance(node, THIRCoerce):
        rt = node.result_type
        rt = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
              if rt is not None else None)
        view_target = rt is not None and is_str_view_type(rt)
        # The ptr/span/slice coercion families produce VALUE results (`T*`,
        # std::span, Slice) whatever the inner's form; lowering tags VALUE.
        # An `into_any` coerce materializes a fresh `tpy::Any` VALUE cell via
        # make_any whatever the wrapped source's form (a STORAGE `std::monostate`
        # None, a VALUE literal), so it is form-producing like the ptr/span
        # families, not a form passthrough.
        value_target = rt is not None and (
            isinstance(rt, (PtrType, AnyType)) or is_span(rt)
            or is_slice_type(rt) or is_basic_slice_type(rt)
            # An `Optional[Span[...]]` slot coerce produces the same VALUE
            # result (`std::optional<span>` absorbs the as_span rvalue).
            or (isinstance(rt, OptionalType)
                and is_span(unwrap_readonly(rt.inner))))
        # The async return slot's borrow/trait lifts (`&(x)`,
        # `to_val_or_ptr<val_or_ptr_t<T>>(x)`) materialize a pointer or
        # trait-selected prvalue out of any source form, so they are
        # form-producing like the ptr/span families. Keyed by NAME, not by
        # result type: both deliberately keep the SOURCE's type spelling
        # (the pointer/trait spelling lives in the wrap), so the ptr row
        # above structurally cannot see them.
        value_wrap_target = node.coercion_name in (
            "async_ret_addr_of", "async_ret_val_or_ptr")
        # The optional-borrow-tuple wrap (`std::optional<B>{<borrow rhs>}`)
        # materializes a STORAGE optional out of the borrow tuple -- form-
        # producing like the ptr/span families.
        opt_btuple_target = node.coercion_name == "opt_btuple_wrap"
        if node.form is not node.expr.form and not (
                (view_target and node.form is Form.BORROW)
                or ((value_target or value_wrap_target)
                    and node.form is Form.VALUE)
                or (opt_btuple_target and node.form is Form.STORAGE)):
            _fail(owner, node,
                  f"coerce form {node.form.name} != inner "
                  f"{node.expr.form.name} (non-view-target passthrough)")


def _borrow_legal_return(rt) -> bool:
    if rt is None:
        return True
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rt)))
    # `return name;` into a by-value RECORD or CONTAINER return (`Own[Box]` ->
    # `Box`, `Own[list[T]]` -> `std::vector<T>`; containers are plain
    # NominalTypes): C++ materializes the storage from the borrow source
    # (NRVO / implicit move / copy-construct) with no spelled convert -- the
    # borrow value is legal.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), NominalType)
            and not t.wrapped.is_value_type()):
        return True
    # `Own[T]` with T an open type param: the C++ argument above is the same
    # at every instantiation (`val_or_cref_t<T>` -> `T` is the same NRVO /
    # implicit-move materialization), but a TypeParamRef is not a
    # NominalType, so without this row the walk would reject a legal return.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), TypeParamRef)):
        return True
    # `return name;` / a slice rvalue into a by-value `Own[A | B]` variant
    # return (`std::variant<...>`): the variant's converting ctor
    # materializes from the borrow source (implicit move for a returned
    # local under P1825), no spelled convert -- the return arm gates the
    # admitted source shapes.
    if (isinstance(t, OwnType)
            and isinstance(unwrap_readonly(t.wrapped), UnionType)):
        return True
    if not t.is_value_type():
        return True
    if isinstance(t, TupleType) and t.has_pointer_repr_element():
        return True
    # A REFERENCE-element tuple return (`std::tuple<Tree&, int32_t>` -- a
    # wrapper element with no Own marker): the slot itself is the borrow
    # form, so a BORROW value is exactly right (ret.wrapper_ref_tuple).
    if isinstance(t, TupleType) and t.has_ref_elements():
        return True
    if isinstance(t, OptionalType) and not t.uses_pointer_repr():
        # A VIEW-inner value optional (`std::optional<std::string_view>`):
        # the view converts into the optional implicitly, exactly as it does
        # into the bare view slot below -- no spelled convert to miss.
        inner = unwrap_readonly(t.inner)
        if is_str_view_type(inner) or is_bytes_view_type(inner):
            return True
        # A nullable tuple return holds the tuple's return layout
        # (`std::optional<std::tuple<Box*, int32_t>>`), so a borrow-form
        # value of it is the slot's own form.
        if isinstance(inner, TupleType) and inner.has_pointer_repr_element():
            return True
    return is_str_view_type(t) or is_bytes_view_type(t)


def _pointer_lifted_storage(t) -> bool:
    """A storage slot whose borrow form is a POINTER shape with no implicit
    C++ conversion back: `optional<T>` (vs `T*`), value-variant (vs pointer
    variant), storage tuple (vs pointer tuple). A BORROW value at such a slot
    is a form lie -- the convert must have wrapped it."""
    if t is None:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if pointer_repr_optional(t) is not None:
        return True
    if is_ptr_variant_union(t):
        return True
    return isinstance(t, TupleType) and t.has_pointer_repr_element()


def _value_record_borrow(v: THIRExpr) -> bool:
    """A BORROW whose own type is a VALUE record, so its render is a `T&` /
    `const T&` lvalue (`ps[i]`, an lvalue ternary, a `T&`-returning call).

    Such a value materializes into any slot that accepts a `T` -- the return
    object copy-constructs from the reference with no spelled convert, exactly
    as the non-value `Own[record]` row of `_borrow_legal_return` argues. That
    row keys on the return TYPE alone, which cannot see this: a value record
    returned by value is spelled `-> T`, so every borrow source at it reads as
    a form lie. The pointer-lifted shapes a BORROW genuinely could not
    initialize from are other types (Optional / variant / tuple), never a
    plain record."""
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(v.result_type)))
    return (isinstance(t, NominalType) and t.is_value_type()
            and t.is_user_record)


def _check_stmt(owner: str, stmt: THIRNode, return_type) -> None:
    if isinstance(stmt, THIRAssign):
        if (isinstance(stmt.target, THIRFieldAccess)
                and stmt.value.form is Form.BORROW
                and _pointer_lifted_storage(stmt.target.result_type)):
            _fail(owner, stmt,
                  "BORROW value at a pointer-lifted field-write sink "
                  "(missing a borrow->storage convert)")
    elif isinstance(stmt, THIRReturn):
        if (stmt.value is not None and stmt.value.form is Form.BORROW
                and not _borrow_legal_return(return_type)
                and not _value_record_borrow(stmt.value)):
            _fail(owner, stmt,
                  f"BORROW return value for value-typed return {return_type}")


def _walk_arg_list(owner: str, args: 'Sequence[THIRExpr]',
                   return_type=None, *,
                   argtemp_ok: bool = False,
                   eager_only: bool = False) -> None:
    """Walk one call-shaped argument list -- the call/ctor/method args, a
    raise's ctor args, an await emplace's args.

    All three are the same position: a temp there flushes at the enclosing
    statement iff that statement is a flush point, and a temp's own SOURCE
    ctor flushes its nested temps at the SAME point (`describe(Canvas(
    Circle(5)))` -- __tmp_1 innermost-first, then __tmp_2), so the right
    propagates through call-arg nesting."""
    for a in args:
        if isinstance(a, THIRArgTemp):
            if not argtemp_ok:
                _fail(owner, a, "THIRArgTemp under a non-flushable "
                                "statement position")
            if eager_only and a.movable is None:
                _fail(owner, a, "unaudited THIRArgTemp under "
                                "a conditional operand")
            _walk(owner, a.init, return_type, argtemp_ok=argtemp_ok,
                  eager_only=eager_only)
        elif isinstance(a, THIRUnionArgLift) and a.temp_cpp is not None:
            # The temp-bearing lift hoists a decl like THIRArgTemp does, so
            # it needs the same flush right.
            if not argtemp_ok:
                _fail(owner, a, "temp-bearing THIRUnionArgLift under a "
                                "non-flushable statement position")
            if a.value is not None:
                _walk(owner, a.value, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
        elif isinstance(a, THIRBorrowTupleLiteral):
            # A borrow tuple AT an argument position is built in place there,
            # so an element hoisted for a frame-capturing callee (`&(__tmp_N)`)
            # flushes exactly as a direct argument's temp does.
            _check_node(owner, a)
            _walk_arg_list(owner, a.elements, return_type,
                           argtemp_ok=argtemp_ok, eager_only=eager_only)
        else:
            _walk(owner, a, return_type, argtemp_ok=argtemp_ok,
                  eager_only=eager_only)


# The wrappers this walk treats as pass-through for flushability -- the ONE
# place that says "transparent": a coerce is a pure inline wrap around its
# source (`::tpy::as_mut_span({0})`) and an error-return unwrap only composes
# the `({ ... })` render around its call. Neither moves where an inner temp's
# decl lands, so a temp reached through one keeps the flush right (and the
# conditional-operand rule) of the position the wrapper sits in. Both reach
# their child through the generic tail, which reads this tuple.
_TRANSPARENT_WRAPPERS = (THIRCoerce, THIRErrorReturnUnwrap)


def _walk(owner: str, node: THIRNode, return_type=None, *,
          argtemp_ok: bool = False, eager_only: bool = False,
          via_transparent: bool = False) -> None:
    """`argtemp_ok` marks the value expression of a flushable statement
    (expr stmt / var-decl init / assign value / return value / print arg /
    tuple-unpack source) -- the only
    region where a THIRArgTemp may appear, and there only under call-arg
    nesting (the flush right rides through call-shaped args / receivers /
    operands). If and while CONDITIONS are also flushable: the emit places
    their temps (pre-`if` flush, the nested-elif block, the restructured
    `while (true)` loop head -- never a pre-loop stale snapshot), and so are
    for-loop sources and range bounds. Anywhere else (a MIL cell) a temp has
    no flush point at all, so reaching one is a lowering bug.

    `eager_only` marks a CONDITIONAL operand position (a ternary arm, a
    logical RHS): only an AUDITED temp -- one whose creator recorded the
    movable fact -- is legal there, since the emit's conditional region
    banks it or, where the payload cannot move, knowingly keeps it eager
    under sema's warning. An UNAUDITED temp never decided that, so
    reaching one here is a lowering bug."""
    _check_node(owner, node)
    _check_stmt(owner, node, return_type)
    if isinstance(node, THIRArgTemp):
        # Reached through a transparent wrapper the temp keeps the arg-list
        # rules, since the wrapper only re-renders it in place; anywhere else
        # a temp outside a call-arg position has no flush point at all.
        if not (via_transparent and argtemp_ok):
            _fail(owner, node, "THIRArgTemp outside a call arg position")
        if eager_only and node.movable is None:
            _fail(owner, node, "unaudited THIRArgTemp under "
                               "a conditional operand")
        _walk(owner, node.init, return_type, argtemp_ok=argtemp_ok,
              eager_only=eager_only)
        return
    if isinstance(node, THIRUnionArgLift) and node.temp_cpp is not None:
        # The temp-bearing lift hoists a decl like THIRArgTemp does, so it
        # is legal only where a temp has a flush point (checked in the
        # call-arm loop below, which returns before re-reaching this node).
        _fail(owner, node, "temp-bearing THIRUnionArgLift outside a call "
                           "arg position")
    if isinstance(node, (THIRCall, THIRMethodCall, THIRCtorCall)):
        # A ctor call carries a temp only for its mutated-ref-slot record
        # rvalue (the ctor_mutated arm); const-slot rvalues inline temp-free.
        if isinstance(node, THIRMethodCall):
            # A call-shaped receiver's arg temps flush at the same statement
            # (allow_temps rides into receivers at lowering). The receiver
            # itself may BE a temp: the generator-factory ctor-rvalue lift
            # (`Counter __tmp_N = Counter(..);` + `__tmp_N.each()`), flushed
            # at the consuming for-head / iterator-object decl.
            if isinstance(node.receiver, THIRArgTemp):
                if not argtemp_ok:
                    _fail(owner, node.receiver,
                          "receiver THIRArgTemp under a non-flushable "
                          "statement position")
                if eager_only and node.receiver.movable is None:
                    _fail(owner, node.receiver,
                          "unaudited THIRArgTemp receiver under "
                          "a conditional operand")
                _walk(owner, node.receiver.init, return_type,
                      argtemp_ok=argtemp_ok, eager_only=eager_only)
            else:
                _walk(owner, node.receiver, return_type,
                      argtemp_ok=argtemp_ok, eager_only=eager_only)
        _walk_arg_list(owner, node.args, return_type, argtemp_ok=argtemp_ok,
                       eager_only=eager_only)
        return
    if isinstance(node, (THIRErrorReturnBind, THIRErrorReturnDiscard)):
        # Statement-level unwrap blocks: the call renders and its temps
        # flush before the block line (the statement has one flush point), so
        # the call is a flushable value position.
        _walk(owner, node.call, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRExprStmt):
        _walk(owner, node.expr, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRTupleUnpack):
        # Every holder form that renders an expression source flushes its
        # temps before the `__tup_N` line, so the source is a flushable
        # value position.
        if node.source_expr is not None:
            _walk(owner, node.source_expr, return_type, argtemp_ok=True)
        return
    if isinstance(node, (THIRIf, THIRWhile)):
        # Conditions are flushable: _emit_if flushes before the `if (` /
        # inside the nested-elif block, _emit_while restructures the loop
        # head (`while (true) { <temps> if (!cond) break;`).
        _walk(owner, node.condition, return_type, argtemp_ok=True)
        for child in _iter_children(node):
            if child is not node.condition:
                _walk(owner, child, return_type)
        return
    if isinstance(node, (THIRForEach, THIRForIterProto, THIRForRange)):
        # A for-each / iterator-protocol source flushes its temps right
        # before its one capture line (inside the rvalue brace scope for the
        # protocol loop), and each range bound right before its own
        # `__start_N` / `__stop_N` / `__step_N` capture, so every loop source
        # is a flushable value position. A comprehension's
        # first loop evaluates its source in the enclosing statement, so
        # there the source keeps the right the comprehension itself was
        # given (passed down by the block arm).
        sources = _loop_sources(node)
        for child in sources:
            if node.source_in_enclosing:
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
            else:
                _walk(owner, child, return_type, argtemp_ok=True)
        for child in _iter_children(node):
            if not any(child is c for c in sources):
                _walk(owner, child, return_type)
        return
    if isinstance(node, THIRPrint):
        # A print statement is a flush position (arg temps
        # hoist before the `std::cout` chain).
        for a in node.args:
            _walk(owner, a.expr, return_type, argtemp_ok=True)
        # sep=/end=/file= are call arguments too, so their temps take the
        # same flush right.
        _walk_arg_list(owner,
                       [x for x in (node.sep_expr, node.end_expr,
                                    node.sink_expr) if x is not None],
                       return_type, argtemp_ok=True)
        return
    if isinstance(node, (THIRVarDecl, THIRPtrLocalDecl)):
        # Both decl flavors are flush positions: a slot init's arg temps
        # hoist BEFORE the decl / `__slot_N` line.
        if node.init is not None:
            _walk(owner, node.init, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRAssign):
        # The receiver eval is lowered with the default (temp-free) use, so
        # no argtemp exemption -- a temp reaching it is a lowering bug.
        if node.recv_eval is not None:
            _walk(owner, node.recv_eval, return_type)
        _walk(owner, node.target, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRPtrLocalRebind):
        # A flushing reseat kind makes the value a flushable position like
        # THIRAssign's; the rest keep the default.
        if node.value is not None:
            _walk(owner, node.value, return_type,
                  argtemp_ok=node.kind in FLUSHING_REBIND_KINDS)
        return
    if isinstance(node, THIRSetItem):
        # The value AND the target's INDEX are flushable positions: the
        # write sits at a statement, so an index-call's arg temps hoist
        # before the setitem line exactly like the value's (threaded via
        # the setitem arm's allow_temps target use). The RECEIVER stays
        # temp-free -- the lowering never
        # forwards flushability there, so a temp reaching it is a
        # lowering bug (the THIRAssign discipline).
        if isinstance(node.target, THIRSubscript):
            _walk(owner, node.target.receiver, return_type)
            _walk(owner, node.target.index, return_type, argtemp_ok=True)
        else:
            _walk(owner, node.target, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRSliceAssign):
        # Same flush semantics as a subscript write: the RHS is a flushable
        # position; the receiver and slice bounds never carry temps.
        _walk(owner, node.receiver, return_type)
        for b in (node.lower, node.upper, node.step):
            if b is not None:
                _walk(owner, b, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRFrameSlotWrite):
        # `name.emplace(value);` -- a statement, so the value's arg temps
        # hoist before the emplace line exactly like a THIRAssign value.
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRInplaceContainerOp):
        _walk(owner, node.receiver, return_type)
        _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRReturn):
        if node.value is not None:
            _walk(owner, node.value, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRFinallyDeferredReturn):
        # A tuple part evaluated before the chain is its own declaration
        # line, which the sync emit flushes its temps ahead of.
        if node.capture is not None:
            _walk(owner, node.capture, return_type)
        for part in _deferred_leaf_parts(node.tuple_parts):
            _walk(owner, part.expr, return_type,
                  argtemp_ok=part.kind is DeferredPartKind.TEMP)
        return
    if isinstance(node, THIRRaise):
        # `raise X(args)` flushes its ctor arg temps before the throw line, so
        # the args are a flush position exactly like a call's. (They are the
        # ctor's directly -- no intermediate THIRCtorCall node carries them.)
        _walk_arg_list(owner, node.args, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRComprehensionBlock):
        # Every statement of the body flushes its own temps inside the
        # block, so each grants its positions as it would anywhere -- except
        # the first loop's source, which keeps this position's right.
        for stmt in node.body:
            _walk(owner, stmt, return_type, argtemp_ok=argtemp_ok,
                  eager_only=eager_only)
        return
    if isinstance(node, THIRCompInsert):
        # The insert flushes its operands' temps right above itself.
        for child in (node.element, node.key, node.value):
            if child is not None:
                _walk(owner, child, return_type, argtemp_ok=True)
        return
    if isinstance(node, THIRArrayComprehension):
        # The range arm's element flushes its temps inside the per-index
        # lambda, so it is a flushable position whatever the comprehension
        # sits in. The source arm's element and its source, and the range
        # bounds, have no flush of their own: they keep the enclosing right.
        own_flush = _own_flush_children(node)
        for child in own_flush:
            _walk(owner, child, return_type, argtemp_ok=True)
        for child in _iter_children(node):
            if not any(child is c for c in own_flush):
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
        return
    if isinstance(node, THIRIfExpr):
        # Ternary ARMS evaluate lazily: only a NON-DEFERRING temp may hoist
        # there, hoisted eagerly at the statement; a deferring temp would
        # need a conditional-region render THIR does not carry. The
        # CONDITION evaluates exactly once unconditionally, so it inherits
        # the enclosing flush right (`Gate __tmp_1 = Gate(true);` hoists
        # before `((check(__tmp_1)) ? (1) : (0))`).
        for child in _iter_children(node):
            if child is node.cond:
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=eager_only)
            else:
                _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                      eager_only=True)
        return
    if isinstance(node, THIRValueSelect):
        # Value-position `and`/`or`: the RHS sits in the ternary branch the
        # emit renders, so it is the same conditional-operand position as a
        # logical THIRBinOp's right (which lowering grants cond_eager from
        # the same site). The LHS evaluates once, unconditionally.
        for child in _iter_children(node):
            _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                  eager_only=(True if child is node.rhs else eager_only))
        return
    if isinstance(node, THIRChainedCompareStmtExpr):
        # Operands 0 and 1 always evaluate; every later one sits behind a
        # passed compare, so inits[2:] are conditional-operand positions --
        # the ternary-arm rule again (the lowering-time cond_eager check is
        # the primary gate; this keeps the structural net symmetric).
        for idx, init in enumerate(node.inits):
            _walk(owner, init, return_type, argtemp_ok=argtemp_ok,
                  eager_only=(True if idx >= 2 else eager_only))
        return
    if (isinstance(node, THIRBinOp) and node.resolved is None
            and node.op in ("&&", "||")):
        # Short-circuit RHS: the conditional-operand rule (audited temps
        # bank into the emit region, unaudited ones are a
        # lowering bug). The LHS always evaluates: it keeps the plain
        # inherited right (its temp hoists at the statement or banks into
        # an enclosing region).
        for child in _iter_children(node):
            _walk(owner, child, return_type, argtemp_ok=argtemp_ok,
                  eager_only=(True if child is node.right else eager_only))
        return
    # The flush right propagates through EXPRESSION nesting (binop operands,
    # coerce wraps, ...): every sub-position of a flushable value expression
    # flushes at the same statement. Statement nodes reset it
    # -- each statement handler above grants the right per position.
    for child in _iter_children(node):
        _walk(owner, child, return_type,
              argtemp_ok=argtemp_ok and isinstance(node, THIRExpr),
              eager_only=eager_only and isinstance(node, THIRExpr),
              via_transparent=isinstance(node, _TRANSPARENT_WRAPPERS))


def _check_param(owner: str, param: THIRParam) -> None:
    """The storage facts of one function or constructor parameter: each
    agrees with the parameter's type, and at most one describes it."""
    if param.native_container is not None:
        _check_native_container(owner, param, param.native_container, param.type)
        if any(f is not None for f in (param.borrowed_record, param.optional_layout,
                                      param.union_layout, param.tuple_layout)):
            _fail(owner, param, "conflicting parameter facts")
    if param.tuple_layout is not None:
        if any(f is not None for f in (param.borrowed_record, param.optional_layout, param.union_layout)):
            _fail(owner, param, "conflicting parameter facts")
        _check_tuple(owner, param, param.tuple_layout, param.type)
    if param.union_layout is not None:
        _check_union(owner, param, param.union_layout, param.type)
    if param.optional_layout is not None:
        _check_optional(owner, param, param.optional_layout, param.type)
    fact = param.borrowed_record
    if fact is not None and (
            unwrap_readonly(unwrap_ref_type(param.type)) != fact.type
            or type(fact.readonly) is not bool):
        _fail(owner, param, "borrowed record fact disagrees with parameter")


def validate_function(fn: THIRFunction, *, lowered: bool = False) -> None:
    """`lowered`: the lowering built `fn`, so every binding-emitting node
    carries its record (`_LOWERED`)."""
    token = _LOWERED.set(lowered)
    try:
        _validate_function(fn)
    finally:
        _LOWERED.reset(token)


def _validate_function(fn: THIRFunction) -> None:
    if fn.receiver is not None:
        fact = fn.receiver
        if (not isinstance(fact, THIRBorrowedRecord) or not isinstance(fact.type, NominalType)
                or fact.type.qualified_name() is None or is_inert_leaf(fact.type)
                or fact.type.type_args or fact.type.is_protocol or type(fact.readonly) is not bool
                or any(p.name == "self" for p in fn.params)):
            _fail(fn.name, fn, "invalid receiver fact")
    for param in fn.params:
        _check_param(fn.name, param)
    if fn.resolved_callee is not None:
        _check_callee(fn.name, fn, fn.resolved_callee)
        signature = fn.resolved_callee.signature
        owner = fn.resolved_callee.identity.owner
        params = effective_params(fn)
        # The access twin binds a mutable receiver where the callable it
        # publishes binds a const one; every other parameter is the same.
        skip = 1 if fn.access_twin else 0
        if ((owner is None) != (fn.receiver is None)
                or fn.receiver is not None and (
                    owner != fn.receiver.type.qualified_name() or signature.passings is None
                    or signature.param_types[:1] != (fn.receiver.type,)
                    or not fn.access_twin and signature.passings[0] is not params[0].passing)
                or fn.error_return_cpp is not None
                or fn.name != fn.resolved_callee.identity.name
                or tuple(declared_param_type(p.type) for p in params)
                != tuple(declared_param_type(t) for t in signature.param_types)
                or signature.return_type not in (
                    fn.return_type, receiver_neutral_return(
                        fn.return_type, signature.result_follows_receiver))
                or (signature.passings is not None and all(p.passing is not None for p in params)
                    and signature.passings[skip:] != tuple(p.passing for p in params)[skip:])):
            _fail(fn.name, fn, "resolved callee disagrees with definition")
    if type(fn.access_twin) is not bool or fn.access_twin and (
            fn.resolved_callee is None or not fn.resolved_callee.signature.result_follows_receiver
            or fn.receiver is None or fn.receiver.readonly):
        _fail(fn.name, fn, "access twin needs a mutable receiver and a follows-receiver callee")
    for stmt in fn.body:
        _walk(fn.name, stmt, fn.return_type)


def _loop_sources(node: 'THIRForEach | THIRForIterProto | THIRForRange'
                  ) -> 'tuple[THIRNode, ...]':
    if isinstance(node, THIRForRange):
        return tuple(c for c in (node.start, node.stop, node.step)
                     if c is not None)
    return (node.iterable,)


def _own_flush_children(node: THIRNode) -> 'tuple[THIRNode, ...]':
    """The children of `node` that flush their own temps: every statement of
    a comprehension's body, and an Array range comprehension's element
    (flushed inside its per-index lambda). A comprehension's first loop
    source does not: `_check_no_statement_temp` reaches it separately."""
    if isinstance(node, THIRComprehensionBlock):
        return node.body
    if (isinstance(node, THIRArrayComprehension) and node.arm == "range"
            and node.element is not None):
        return (node.element,)
    return ()


def _check_no_statement_temp(owner: str, node: THIRNode) -> None:
    """A member-init or base-init cell runs before the ctor body, so no
    statement exists to host a hoisted declaration: lowering demotes an
    init whose source needs one. A comprehension's body flushes its own
    temps and is skipped, all but its first loop's source, which evaluates
    in the cell; a lambda body is not a flush region, so it is walked."""
    if hoists_declaration(node):
        _fail(owner, node, "statement temp in a member-init / base-init cell")
    if isinstance(node, THIRComprehensionBlock):
        for stmt in node.body:
            if (isinstance(stmt, (THIRForEach, THIRForIterProto, THIRForRange))
                    and stmt.source_in_enclosing):
                for src in _loop_sources(stmt):
                    _check_no_statement_temp(owner, src)
    skip = _own_flush_children(node)
    for child in _iter_children(node):
        if not any(child is c for c in skip):
            _check_no_statement_temp(owner, child)


def validate_constructor(ctor: THIRConstructor, *,
                         lowered: bool = False) -> None:
    token = _LOWERED.set(lowered)
    try:
        _validate_constructor(ctor)
    finally:
        _LOWERED.reset(token)


def _validate_constructor(ctor: THIRConstructor) -> None:
    owner = f"{ctor.record_name}.__init__"
    for param in ctor.params:
        _check_param(owner, param)
    for mil in ctor.mil_inits:
        if mil.field_identity is not None and (
                ctor.record_layout is None
                or mil.field_identity not in ctor.record_layout.fields):
            _fail(owner, mil.value, "member identity disagrees with record layout")
        _check_no_statement_temp(owner, mil.value)
        _walk(owner, mil.value)
        if (mil.value.form is Form.BORROW
                and _pointer_lifted_storage(mil.value.result_type)):
            _fail(owner, mil.value,
                  "BORROW value at a MIL cell (missing a borrow->storage "
                  "convert)")
    for base in ctor.base_inits:
        for arg in base.args:
            _check_no_statement_temp(owner, arg)
            _walk(owner, arg)
    for stmt in ctor.body:
        _walk(owner, stmt)


def validate_definitions(functions: 'Sequence[THIRFunction]') -> None:
    """One module's bodies together: each callee identity has one defining
    body and at most one access twin, which publishes exactly the
    definition's callee."""
    defining: dict[THIRFunctionIdentity, THIRFunction] = {}
    twins: dict[THIRFunctionIdentity, THIRFunction] = {}
    for fn in functions:
        if fn.resolved_callee is None:
            continue
        table = twins if fn.access_twin else defining
        if fn.resolved_callee.identity in table:
            _fail(fn.name, fn, "two bodies define one callee identity")
        table[fn.resolved_callee.identity] = fn
    for identity, twin in twins.items():
        definition = defining.get(identity)
        if definition is not None and definition.resolved_callee != twin.resolved_callee:
            _fail(twin.name, twin, "access twin disagrees with its definition")


def validate_stmts(owner: str, stmts, return_type=None, *,
                   lowered: bool = False) -> None:
    """Validate a statement block that is not a whole function body -- a
    frame nested def's member body, a seam's leaf block."""
    token = _LOWERED.set(lowered)
    try:
        for stmt in stmts:
            _walk(owner, stmt, return_type)
    finally:
        _LOWERED.reset(token)


def validate_resumable_body(owner: str, body: THIRResumableBody, *,
                            lowered: bool = False) -> None:
    token = _LOWERED.set(lowered)
    try:
        _validate_resumable_body(owner, body)
    finally:
        _LOWERED.reset(token)


def _validate_resumable_body(owner: str, body: THIRResumableBody) -> None:
    """Same structural gate the ordinary bodies get, applied to a resumable
    frame's leaf tables.

    The frame skeleton holds the statements/expressions apart in identity-keyed
    maps instead of one linear body, so each seam is walked at the flush
    right its lowering grants. `return_type` stays out: a `return` in a
    resumable is a CFG terminator whose value renders through
    `return_values`, so no THIRReturn statement reaches a leaf, and the
    frame's declared return type is not the sink type of that value render
    (the scaffolding binds it to its own `__tpy_async_ret` slot).
    `nested_def_bodies` is validated where it is lowered, under the member's
    own return type."""
    for stmt in body.leaves.values():
        _walk(owner, stmt)
    for stmt in body.match_dispatches.values():
        _walk(owner, stmt)
    # Temp-free seams: their lowering never grants a flush right, so an
    # arg temp reaching one is a lowering bug.
    for expr in body.return_values.values():
        _walk(owner, expr)
    # A yield value is flushable (the skeleton flushes ahead of its `return`)
    # exactly where the lowering granted the right: a temp under any other
    # yield is a lowering bug.
    for ys, expr in body.yield_values.items():
        _walk(owner, expr, argtemp_ok=ys in body.yield_temp_rights)
    # The deferred-return recipe is consulted by the return scaffolding, which
    # renders the capture into an `auto* p = ...;` line with no flush point.
    for stmt in body.deferred_returns.values():
        if stmt.capture is not None:
            _walk(owner, stmt.capture)
        for part in _deferred_leaf_parts(stmt.tuple_parts):
            _walk(owner, part.expr)
    # Flushable seams: the Branch condition, the sub-coro emplace, the await
    # operand and the sync for-head source are positions where the skeleton
    # flushes temps ahead of the line. For a condition the flush lands INSIDE
    # the `case` block, so its temp is rebuilt on every re-entry -- which is
    # what a fresh container argument in a loop head means.
    for expr in body.conds.values():
        _walk(owner, expr, argtemp_ok=True)
    # Both maps below pool entries from several populate sites, not all of
    # which grant temps -- `suspend_exprs` holds the await operand
    # (flushable) plus the bound-method receiver; `region_exprs` the sync
    # for-head iterable and range bounds (flushable) plus the with-manager
    # and the async-for iterable. Pooling by expression identity leaves no way
    # to tell them apart here, so both are walked at the looser right: a temp
    # reaching one of the three temp-free seams pooled in (the bound-method
    # receiver, the with-manager and the async-for iterable), where the
    # skeleton has no flush point, is NOT caught.
    for args in body.await_args.values():
        # The tuple IS the emplace's arg list, so it is walked the way the
        # call-node arm walks a call's args.
        _walk_arg_list(owner, args, argtemp_ok=True)
    for expr in body.suspend_exprs.values():
        _walk(owner, expr, argtemp_ok=True)
    for expr in body.region_exprs.values():
        _walk(owner, expr, argtemp_ok=True)
