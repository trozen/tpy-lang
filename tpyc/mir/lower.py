"""All-or-nothing lowering of scalar and record-storage THIR operations."""

from dataclasses import dataclass, field

from ..identity_map import IdentityMap
from ..parse import RebindStorage, SourceLocation
from ..thir import nodes as th
from ..typesys import (
    BOOL, INT32, INT32_MAX, INT32_MIN, IntLiteralType, NominalType, TpyType,
    NoneType, OptionalType, ReadonlyType, TupleType, UnionType, VoidType, is_void_like_type, unwrap_readonly, unwrap_ref_type,
)
from .nodes import (
    MIRAlias, MIRAssign, MIRBlock, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBranch, MIRStorageInit, MIRStatement,
    MIRCompare, MIRConstant, MIRGoto, MIRFunction, MIRNot, MIRNotCovered, MIRReceiverInit, MIRGlobalId,
    MIRDeref, MIRField, MIRFieldId, MIRPlace, MIRRead, MIRReturn, MIRRvalue,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRTerminator, MIRValueKind, MIRStorageDuration,
    MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRRecordStorageInit, MIRRecordStorageKind,
    MIRRecordWrite, MIRRecordWriteMode, MIRPayloadWrite, MIRPayloadWriteMode,
    MIRRegion, MIRRegionId,
    MIRTupleConstruct, MIRTupleCopy, MIRTupleElement, MIRTupleIndex, MIRTupleLayout, MIRTupleInitialization,
    MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIROptionalLayout, MIROptionalPayload,
    MIRUnionLayout, MIRUnionPayload, MIRUnionConstruct, MIRUnionCopy, MIRIsAlternative, MIRUnionExtract,
)
from .coverage import MIRUnsupported, plain as _plain, require as _require, scalar_wrapper
from .definitions import MIRConstructorDefinition, MIRDefinitions, constructor_initialization
from .validate import MIRDefiniteAssignmentError, MIRPresenceError, statement_reads, successors, validate_function


def _literal(expr: th.THIRExpr) -> bool:
    return isinstance(expr, th.THIRLiteral) or (
        isinstance(expr, th.THIRCoerce) and isinstance(expr.expr, th.THIRLiteral))


class _Coverage:
    def __init__(self, fn: th.THIRFunction, definitions: MIRDefinitions) -> None:
        self.fn = fn
        self.bindings: dict[str, TpyType] = {}
        self.references: dict[str, th.THIRBorrowedRecord] = {}
        self.parameters = {p.name for p in fn.params}
        self.writes: IdentityMap[th.THIRExpr, bool] = IdentityMap()
        self.definitions = definitions
        self.records: dict[NominalType, MIRConstructorDefinition] = {}
        self.fixed_owned: set[str] = set()
        self.optional_record_storage: dict[str, th.THIRBorrowedRecord] = {}
        self.tuples: dict[str, th.THIRTupleLayout] = {}
        self.tuple_exprs: IdentityMap[th.THIRExpr, th.THIRTupleLayout] = IdentityMap()
        self.optionals: dict[str, th.THIROptionalLayout] = {}
        self.unions: dict[str, th.THIRUnionLayout] = {}
        self.payload_aliases: set[str] = set()
        self.globals: dict[MIRGlobalId, th.THIRGlobalBinding] = {}

    def global_binding(self, expr: th.THIRName | th.THIRModuleVar | th.THIRWalrus,
                       *, write: bool = False) -> TpyType:
        fact = expr.global_binding
        _require(expr, isinstance(fact, th.THIRGlobalBinding) and bool(fact.module and fact.name)
                 and fact.type in (BOOL, INT32) and fact.type == expr.result_type
                 and expr.form is th.Form.VALUE and type(fact.writable) is bool,
                 "missing or invalid scalar global binding")
        _require(expr, not write or fact.writable, "global binding is not writable")
        identity = MIRGlobalId(fact.module, fact.name)
        previous = self.globals.get(identity)
        _require(expr, previous is None or previous.type == fact.type, "inconsistent global type")
        if previous is None or fact.writable:
            self.globals[identity] = fact
        return fact.type

    def check(self) -> None:
        fn = self.fn
        _require(fn, fn.error_return_cpp is None, "error-return body")
        _require(fn, not fn.layout.hoisted_locals, "hoisted declarations")
        _require(fn, fn.return_type in (BOOL, INT32) or isinstance(fn.return_type, VoidType),
                 "unsupported return type")
        if fn.receiver is not None:
            self.reference(fn, fn.receiver, fn.receiver.type)
            self.bindings["self"] = fn.receiver.type
            self.references["self"] = fn.receiver
            self.parameters.add("self")
        for p in fn.params:
            _plain(p, {"name", "type", "borrowed_record", "optional_layout", "union_layout", "tuple_layout"})
            _require(fn, p.name not in self.bindings, "duplicate binding")
            if p.tuple_layout is not None:
                _require(p, all(f is None for f in (p.borrowed_record, p.optional_layout, p.union_layout)),
                         "conflicting parameter facts")
                self.tuple_layout(p, p.tuple_layout, p.type)
                self.tuples[p.name] = p.tuple_layout
                self.bindings[p.name] = unwrap_ref_type(unwrap_readonly(unwrap_ref_type(p.type)))
            elif p.union_layout is not None:
                _require(p, p.borrowed_record is None and p.optional_layout is None, "conflicting parameter facts")
                self.union_layout(p, p.union_layout, p.type)
                self.unions[p.name] = p.union_layout
                self.bindings[p.name] = p.union_layout.type
            elif p.optional_layout is not None:
                _require(p, p.borrowed_record is None, "conflicting parameter facts")
                self.optional_layout(p, p.optional_layout, p.type)
                self.optionals[p.name] = p.optional_layout
                self.bindings[p.name] = unwrap_readonly(unwrap_ref_type(p.type))
            elif p.borrowed_record is not None:
                self.reference(p, p.borrowed_record, p.type)
                self.references[p.name] = p.borrowed_record
                self.bindings[p.name] = p.borrowed_record.type
            else:
                _require(fn, p.type in (BOOL, INT32), "unsupported parameter type")
                self.bindings[p.name] = p.type
        self.declarations(fn.body, 0)

    def declarations(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> None:
        for stmt in stmts:
            if isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl)):
                _require(stmt, stmt.name not in self.bindings, "duplicate binding")
                if stmt.storage_placement is not None:
                    _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.SCOPE or (
                        isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                        and stmt.owned_storage is not None and stmt.storage_placement is th.THIRStoragePlacement.BODY),
                             "hoisted initial backing")
                if stmt.owned_storage is not None and loops:
                    _require(stmt, isinstance(stmt.init, th.THIRCtorCall), "copy or move initialization in loop")
                if stmt.union_layout is not None:
                    _require(stmt, isinstance(stmt, th.THIRVarDecl) and stmt.init is not None,
                             "unsupported union declaration")
                    _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "union_layout",
                                  "cpp_local_representation", "union_literal", "storage_placement"})
                    self.union_layout(stmt, stmt.union_layout, stmt.resolved_type)
                    reference = any(isinstance(m, th.THIRBorrowedRecord) for m in stmt.union_layout.elements)
                    _require(stmt, stmt.form is (th.Form.BORROW if reference else th.Form.VALUE)
                             and (reference or stmt.cpp_local_representation is None),
                             "union declaration representation mismatch")
                    self.union_source(stmt.init, stmt.union_layout, stmt.union_literal)
                    self.unions[stmt.name] = stmt.union_layout
                    self.bindings[stmt.name] = stmt.union_layout.type
                    continue
                if stmt.optional_layout is not None:
                    allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "optional_layout", "storage_placement"}
                    if isinstance(stmt, th.THIRVarDecl):
                        reference = isinstance(stmt.optional_layout.payload, th.THIRBorrowedRecord)
                        if reference:
                            allowed |= {"cpp_local_representation"}
                        _require(stmt, stmt.form is (th.Form.BORROW if reference else th.Form.VALUE)
                                 and stmt.init is not None, "optional declaration form or initializer")
                        _require(stmt, not reference or stmt.is_const == stmt.optional_layout.payload.readonly,
                                 "optional declaration access mismatch")
                    else:
                        allowed |= {"kind"}
                        if stmt.kind is th.PtrSlotKind.OPT_RVALUE:
                            allowed.add("owned_storage")
                            fact = stmt.owned_storage
                            _require(stmt, isinstance(fact, th.THIRBorrowedRecord)
                                     and fact == stmt.optional_layout.payload
                                     and stmt.is_const == fact.readonly
                                     and isinstance(stmt.init, th.THIRCtorCall), "optional backing storage")
                            self.record_value(stmt.init, fact.type)
                        else:
                            _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE and stmt.init is None,
                                     "optional backing storage")
                    _plain(stmt, allowed)
                    self.optional_layout(stmt, stmt.optional_layout, stmt.resolved_type)
                    if stmt.owned_storage is None:
                        self.optional_source(stmt.init, stmt.optional_layout)
                    self.optionals[stmt.name] = stmt.optional_layout
                    self.bindings[stmt.name] = unwrap_readonly(unwrap_ref_type(stmt.resolved_type))
                    continue
                if isinstance(stmt, th.THIRVarDecl) and isinstance(stmt.resolved_type, TupleType):
                    owning = isinstance(stmt.tuple_layout, th.THIRTupleLayout) and stmt.tuple_layout.owns_records
                    _plain(stmt, {"name", "resolved_type", "init", "cpp_type", "is_const", "tuple_layout"}
                           | ({"storage_placement"} if owning else set()))
                    _require(stmt, stmt.form in ((th.Form.STORAGE,) if owning else (th.Form.VALUE, th.Form.BORROW))
                             and stmt.init is not None, "unsupported tuple declaration")
                    self.tuple_layout(stmt, stmt.tuple_layout, stmt.resolved_type, allow_owned=owning)
                    if owning:
                        _require(stmt, isinstance(stmt.init, th.THIRTupleLiteral)
                                 and stmt.init.tuple_layout == stmt.tuple_layout
                                 and stmt.storage_placement is th.THIRStoragePlacement.SCOPE,
                                 "owned tuple needs direct constructor literal")
                    self.tuple_compatible(stmt, self.tuple_expr(stmt.init, allow_owned=owning), stmt.tuple_layout)
                    self.tuples[stmt.name] = stmt.tuple_layout
                    self.bindings[stmt.name] = stmt.resolved_type
                    continue
                if stmt.owned_storage is not None:
                    self.owned(stmt)
                    continue
                if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                    self.borrow_binding(stmt, declaration=True)
                    continue
                _require(stmt, isinstance(stmt, th.THIRVarDecl), "missing alias binding")
                _plain(stmt, {"name", "resolved_type", "init", "is_const"})
                _require(stmt, stmt.form is th.Form.VALUE and stmt.resolved_type in (BOOL, INT32),
                         "unsupported local type or form")
                _require(stmt, stmt.name not in self.bindings, "duplicate binding")
                if stmt.init is not None:
                    _require(stmt, self.expr(stmt.init) == stmt.resolved_type,
                             "initializer type mismatch")
                self.bindings[stmt.name] = stmt.resolved_type
            else:
                self.stmt(stmt, loops)

    def record_value(self, expr: th.THIRExpr, typ: NominalType) -> None:
        definition = self.definitions.get(expr, typ)
        self.records[typ] = definition
        _require(expr, unwrap_readonly(unwrap_ref_type(expr.result_type)) == typ,
                 "record initializer type mismatch")
        match expr:
            case th.THIRCtorCall():
                _plain(expr, {"type_cpp", "args"})
                _require(expr, expr.form is th.Form.STORAGE, "constructor form")
                params = definition.constructor.params
                _require(expr, len(expr.args) == len(params), "incomplete constructor arguments")
                for arg, param in zip(expr.args, params):
                    _require(arg, self.expr(arg) == param.type, "constructor argument type")
                    _require(arg, not self.writes[arg], "effectful constructor argument")
            case th.THIRCopy() | th.THIRMove():
                _plain(expr, {"value", "cpp_type"} if isinstance(expr, th.THIRCopy) else {"value"})
                source = self.reference_name(expr.value)
                _require(expr, self.references[source].type == typ, "record source type mismatch")
                if isinstance(expr, th.THIRCopy):
                    _require(expr, definition.layout.copyable and expr.form is th.Form.STORAGE,
                             "record is not copyable or copy form")
                else:
                    _require(expr, definition.layout.movable and source in self.fixed_owned
                             and not self.references[source].readonly
                             and expr.form is expr.value.form, "move needs fixed movable owned local")
            case _:
                raise MIRUnsupported(expr, "unsupported record initializer")

    def owned(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> None:
        fact = stmt.owned_storage
        self.reference(stmt, fact, stmt.resolved_type)
        allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", "owned_storage", "storage_placement"}
        if isinstance(stmt, th.THIRVarDecl):
            allowed.add("cpp_local_representation")
            _require(stmt, stmt.form in (th.Form.STORAGE, th.Form.BORROW), "owned declaration form")
        else:
            allowed.add("kind")
            _require(stmt, stmt.kind in (th.PtrSlotKind.RECORD_RVALUE, th.PtrSlotKind.RECORD_HOISTED),
                     "owned declaration kind")
            if stmt.kind is th.PtrSlotKind.RECORD_HOISTED:
                _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.BODY
                         and isinstance(stmt.init, th.THIRCtorCall), "hoisted backing needs body constructor site")
        _plain(stmt, allowed)
        _require(stmt, stmt.init is not None and stmt.is_const == fact.readonly,
                 "owned declaration initializer or access")
        self.record_value(stmt.init, fact.type)
        if isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED:
            _require(stmt, self.records[fact.type].layout.movable, "hoisted constructor needs movable record")
        self.bindings[stmt.name] = fact.type
        self.references[stmt.name] = fact
        if (isinstance(stmt, th.THIRVarDecl) and stmt.form is th.Form.STORAGE
                and stmt.name not in self.fn.layout.reassigned_locals):
            self.fixed_owned.add(stmt.name)

    def replacement(self, stmt: th.THIRAssign, loops: int) -> None:
        _plain(stmt, {"target", "value", "rebind_storage", "slot_cpp"})
        _require(stmt, loops == 0 or stmt.rebind_storage is RebindStorage.OWN, "owning operation in loop")
        name = self.reference_name(stmt.target)
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                 "unsupported replacement storage")
        _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE
                 or not self.references[name].readonly, "readonly in-place replacement")
        _require(stmt, isinstance(stmt.value, th.THIRCtorCall), "replacement needs constructor")
        self.record_value(stmt.value, self.references[name].type)
        _require(stmt, self.records[self.references[name].type].layout.movable,
                 "replacement needs movable record")
        self.fixed_owned.discard(name)

    def reference(self, node: object, ref: th.THIRBorrowedRecord, typ: TpyType) -> None:
        _require(node, isinstance(ref, th.THIRBorrowedRecord), "invalid reference fact")
        _require(node, isinstance(ref.type, NominalType) and ref.type.qualified_name() is not None
                 and ref.type not in (BOOL, INT32)
                 and not ref.type.type_args and not ref.type.is_protocol
                 and type(ref.readonly) is bool, "unsupported reference fact")
        _require(node, unwrap_readonly(unwrap_ref_type(typ)) == ref.type,
                 "reference type mismatch")

    def reference_name(self, expr: th.THIRExpr) -> str:
        match expr:
            case th.THIRName():
                _plain(expr, {"name", "is_last_use", "is_movable", "deref"})
                name = expr.name
            case th.THIRSelf():
                _plain(expr, {"deref"})
                _require(expr, self.fn.receiver is not None, "missing receiver fact")
                _require(expr, expr.form is th.Form.BORROW, "receiver read form")
                _require(expr, not isinstance(unwrap_ref_type(expr.result_type), ReadonlyType)
                         or self.fn.receiver.readonly, "receiver read increases access")
                name = "self"
            case _:
                raise MIRUnsupported(expr, "reference needs local name")
        _require(expr, name in self.references, "unknown reference source")
        self.reference(expr, self.references[name], expr.result_type)
        return name

    def borrow_binding(self, stmt: th.THIRStmt, *, declaration: bool = False) -> None:
        storage = stmt.storage_borrow is not None
        alias = stmt.alias_binding
        _require(stmt, (storage and alias is None) or isinstance(alias, th.THIRAliasBinding),
                 "missing or conflicting borrow binding")
        fact = stmt.storage_borrow if storage else alias.reference
        _require(stmt, isinstance(fact, th.THIRBorrowedRecord), "invalid borrow binding")
        operation = "storage_borrow" if storage else "alias_binding"
        if declaration:
            allowed = {"name", "resolved_type", "init", "cpp_type", "is_const", operation}
            if isinstance(stmt, th.THIRVarDecl):
                allowed.add("cpp_local_representation")
                _require(stmt, stmt.form is th.Form.BORROW, "alias declaration form")
            else:
                allowed.add("kind")
                _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported alias declaration")
            _plain(stmt, allowed)
            name, source = stmt.name, stmt.init
            self.reference(stmt, fact, stmt.resolved_type)
            _require(stmt, stmt.is_const == fact.readonly, "alias access mismatch")
        elif isinstance(stmt, th.THIRAssign):
            _plain(stmt, {"target", "value", operation})
            name = self.reference_name(stmt.target)
            source = stmt.value
        else:
            _plain(stmt, {"name", "kind", "value", operation})
            _require(stmt, stmt.kind is th.PtrSlotKind.PTR_ADDR, "unsupported alias reseat")
            name, source = stmt.name, stmt.value
        _require(stmt, name not in self.parameters, "reference parameter reseat")
        _require(stmt, source is not None, "missing alias source")
        if isinstance(source, th.THIRFormConvert):
            _plain(source, {"value", "is_const"})
            _require(source, source.form is th.Form.BORROW
                     and source.is_const == fact.readonly,
                     "unsupported alias conversion")
            self.reference(source, fact, source.result_type)
            source = source.value
        if storage:
            if isinstance(source, th.THIRSubscript):
                _require(source, declaration and isinstance(source.receiver, th.THIRName)
                         and source.receiver.name in self.parameters, "tuple borrow needs parameter capture")
                reference = self.projection(source, capture=True)
            else:
                _require(source, isinstance(source, th.THIRFieldAccess) and source.form is th.Form.STORAGE,
                         "storage borrow needs record field")
                reference = self.field(source)
            _require(source, isinstance(reference, th.THIRBorrowedRecord), "storage borrow needs record")
        else:
            source_name = self.reference_name(source)
            _require(stmt, alias.source == source_name, "alias source mismatch")
            reference = self.references[source_name]
        self.reference(stmt, fact, reference.type)
        _require(stmt, not reference.readonly or fact.readonly, "alias increases access")
        if declaration:
            self.bindings[name] = fact.type
            self.references[name] = fact
        else:
            _require(stmt, self.references.get(name) == fact, "alias destination mismatch")
            self.fixed_owned.discard(name)

    def union_layout(self, node: object, layout: th.THIRUnionLayout, typ: TpyType) -> None:
        _require(node, isinstance(layout, th.THIRUnionLayout)
                 and isinstance(layout.type, UnionType)
                 and layout.type == unwrap_readonly(unwrap_ref_type(typ))
                 and len(layout.elements) == len(layout.type.members), "unsupported union layout")
        kinds = set()
        for member, alternative in zip(layout.elements, layout.type.members):
            if member is None:
                _require(node, is_void_like_type(alternative), "union absence type mismatch")
            elif isinstance(member, th.THIRBorrowedRecord):
                self.reference(node, member, alternative)
                _require(node, member.readonly or (unwrap_readonly(typ) == typ
                         and unwrap_readonly(alternative) == alternative), "union layout increases access")
                kinds.add("reference")
            else:
                _require(node, member in (BOOL, INT32) and member == alternative, "unsupported union scalar")
                kinds.add("value")
        _require(node, len(kinds) == 1 and len(layout.elements) >= 2, "mixed or empty union layout")

    def union_name(self, expr: th.THIRName) -> th.THIRUnionLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "union_read"})
        fact = expr.union_read
        _require(expr, isinstance(fact, th.THIRUnionLayout) and self.unions.get(expr.name) == fact,
                 "missing or inconsistent union read")
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW)
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == fact.type,
                 "union read type or form mismatch")
        return fact

    def union_source(self, expr: th.THIRExpr, target: th.THIRUnionLayout,
                      literal: th.THIRUnionLiteral | None = None) -> None:
        if literal is not None:
            _require(expr, isinstance(literal, th.THIRUnionLiteral) and literal.layout == target
                     and type(literal.alternative) is int and 0 <= literal.alternative < len(target.elements),
                     "invalid union literal fact")
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name == "int_literal_to_fixed_int", "unsupported union literal coercion")
                expr = expr.expr
            _require(expr, isinstance(expr, th.THIRLiteral), "union literal fact needs literal source")
            _plain(expr, {"value", "int_cpp", "none_cpp"})
            member = target.elements[literal.alternative]
            _require(expr, type(expr.value) is type(literal.value) and expr.value == literal.value
                     and (member is None and literal.value is None
                          or member == BOOL and type(literal.value) is bool
                          or member == INT32 and type(literal.value) is int and INT32_MIN <= literal.value <= INT32_MAX),
                     "union literal payload mismatch")
            return
        if isinstance(expr, th.THIRName) and expr.name in self.unions:
            source = self.union_name(expr)
            _require(expr, source.type == target.type, "union copy type mismatch")
            for src, dst in zip(source.elements, target.elements):
                if src is None or dst is None:
                    _require(expr, src is dst, "union copy absence mismatch")
                else:
                    self.payload_compatible(expr, src, dst)
            return
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            _require(expr, None in target.elements, "union has no absence alternative")
            return
        if isinstance(expr, th.THIRName) and expr.name in self.references:
            source = self.references[self.reference_name(expr)]
            matches = [m for m in target.elements if isinstance(m, th.THIRBorrowedRecord) and m.type == source.type]
            _require(expr, len(matches) == 1, "union has no matching record alternative")
            self.payload_compatible(expr, source, matches[0])
        else:
            typ = self.expr(expr)
            _require(expr, typ in target.elements and not self.writes[expr], "unsupported union member construction")

    def union_extraction(self, node: object, fact: th.THIRUnionExtraction | None) -> TpyType | th.THIRBorrowedRecord:
        _require(node, isinstance(fact, th.THIRUnionExtraction)
                 and self.unions.get(fact.source) == fact.layout,
                 "missing or inconsistent union extraction")
        _require(node, type(fact.alternative) is int and 0 <= fact.alternative < len(fact.layout.elements),
                 "invalid union extraction alternative")
        member = fact.layout.elements[fact.alternative]
        _require(node, member is not None, "cannot extract absent union alternative")
        return member

    def inline_union(self, expr: th.THIRNarrowedRead) -> TpyType | th.THIRBorrowedRecord:
        _plain(expr, {"variant_cpp", "member_cpp", "is_ptr_variant", "union_extraction"})
        member = self.union_extraction(expr, expr.union_extraction)
        reference = isinstance(member, th.THIRBorrowedRecord)
        _require(expr, expr.is_ptr_variant is reference
                 and unwrap_readonly(unwrap_ref_type(expr.result_type)) == (member.type if reference else member)
                 and expr.form is (th.Form.BORROW if reference else th.Form.VALUE), "union extraction form mismatch")
        return member

    def optional_layout(self, node: object, layout: th.THIROptionalLayout, typ: TpyType) -> None:
        outer_readonly = unwrap_readonly(unwrap_ref_type(typ)) != unwrap_ref_type(typ)
        typ = unwrap_readonly(unwrap_ref_type(typ))
        _require(node, isinstance(layout, th.THIROptionalLayout)
                 and isinstance(typ, OptionalType) and not typ.force_pointer_repr,
                 "unsupported optional layout")
        member = layout.payload
        if isinstance(member, th.THIRBorrowedRecord):
            self.reference(node, member, typ.inner)
            _require(node, (not outer_readonly and unwrap_readonly(typ.inner) == typ.inner) or member.readonly,
                     "optional layout increases access")
        else:
            _require(node, member in (BOOL, INT32) and unwrap_readonly(typ.inner) == member,
                     "unsupported optional payload")

    def optional_name(self, expr: th.THIRName, *, extract: bool) -> th.THIROptionalLayout:
        _plain(expr, {"name", "is_last_use", "is_movable", "deref", "optional_read"})
        fact = expr.optional_read
        _require(expr, isinstance(fact, th.THIROptionalRead) and fact.extract is extract
                 and expr.name in self.optionals and fact.layout == self.optionals[expr.name],
                 "missing or inconsistent optional read")
        layout = fact.layout
        reference = isinstance(layout.payload, th.THIRBorrowedRecord)
        typ = layout.payload.type if reference else layout.payload
        actual = unwrap_readonly(unwrap_ref_type(expr.result_type))
        _require(expr, actual == typ if extract else (
            actual in (typ, self.bindings[expr.name]) or isinstance(actual, VoidType)),
            "optional read type mismatch")
        _require(expr, expr.form is (th.Form.BORROW if reference else th.Form.VALUE)
                 and (reference or expr.deref is extract), "optional read form mismatch")
        return layout

    def optional_source(self, expr: th.THIRExpr | None, target: th.THIROptionalLayout) -> None:
        if expr is None:
            return
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            _plain(expr, {"value", "none_cpp"})
            if isinstance(expr.result_type, OptionalType):
                self.optional_layout(expr, target, expr.result_type)
            else:
                _require(expr, isinstance(expr.result_type, (NoneType, VoidType)),
                         "optional absence type")
        elif isinstance(expr, th.THIRName) and expr.name in self.optionals and (
                expr.optional_read is not None and not expr.optional_read.extract):
            source = self.optional_name(expr, extract=False)
            self.payload_compatible(expr, source.payload, target.payload)
        elif isinstance(target.payload, th.THIRBorrowedRecord):
            _require(expr, isinstance(expr, th.THIRName), "optional capture needs local name")
            source = self.references[self.reference_name(expr)]
            _require(expr, source.type == target.payload.type
                     and (not source.readonly or target.payload.readonly), "optional capture access mismatch")
        elif _literal(expr):
            # Hoisted assignments contextualize the literal with the wrapper type.
            _require(expr, expr.form is th.Form.VALUE, "unsupported optional literal form")
            if isinstance(expr, th.THIRCoerce):
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, expr.coercion_name == "int_literal_to_fixed_int" and target.payload == INT32
                         and expr.result_type in (INT32, OptionalType(INT32)), "unsupported optional literal coercion")
                expr = expr.expr
            _plain(expr, {"value", "int_cpp"})
            typ = INT32 if isinstance(expr.result_type, IntLiteralType) else expr.result_type
            _require(expr, expr.form is th.Form.VALUE and typ in (target.payload, OptionalType(target.payload))
                     and (target.payload == BOOL and type(expr.value) is bool
                          or target.payload == INT32 and type(expr.value) is int and INT32_MIN <= expr.value <= INT32_MAX),
                     "optional literal payload mismatch")
        else:
            _require(expr, self.expr(expr) == target.payload and not self.writes[expr],
                     "effectful or mistyped optional payload")

    def tuple_layout(self, node: object, layout: th.THIRTupleLayout | None,
                     typ: TpyType, *, allow_owned: bool = False) -> None:
        readonly = isinstance(unwrap_ref_type(typ), ReadonlyType)
        typ = unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
        _require(node, isinstance(layout, th.THIRTupleLayout) and isinstance(typ, TupleType),
                 "missing tuple layout")
        _require(node, len(layout.elements) == len(typ.element_types), "tuple layout arity")
        _require(node, allow_owned or not layout.owns_records, "owning tuple position is unsupported")
        for member, element in zip(layout.elements, typ.element_types):
            if isinstance(member, (th.THIRBorrowedRecord, th.THIROwnedRecord)):
                self.reference(node, th.THIRBorrowedRecord(member.type, member.readonly), element)
                _require(node, (not readonly and unwrap_readonly(element) == element) or member.readonly,
                         "tuple layout increases access")
                if isinstance(member, th.THIROwnedRecord):
                    definition = self.definitions.get(node, member.type)
                    _require(node, definition.layout.movable, "tuple construction needs movable record")
                    self.records[member.type] = definition
            else:
                _require(node, member in (BOOL, INT32) and member == element,
                         "unsupported tuple scalar")

    def tuple_compatible(self, node: object, source: th.THIRTupleLayout,
                         target: th.THIRTupleLayout) -> None:
        _require(node, len(source.elements) == len(target.elements), "tuple copy arity")
        for src, dst in zip(source.elements, target.elements):
            self.payload_compatible(node, src, dst)

    def payload_compatible(self, node: object, source: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord,
                           target: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> None:
        if isinstance(source, th.THIRBorrowedRecord) and isinstance(target, th.THIRBorrowedRecord):
            _require(node, source.type == target.type and (not source.readonly or target.readonly),
                     "payload copy type or access mismatch")
        else:
            _require(node, source == target, "payload copy type mismatch")

    def tuple_expr(self, expr: th.THIRExpr, *, allow_owned: bool = False) -> th.THIRTupleLayout:
        _require(expr, expr.form in (th.Form.VALUE, th.Form.BORROW, th.Form.STORAGE), "unsupported tuple form")
        if isinstance(expr, th.THIRName):
            _plain(expr, {"name", "is_last_use", "is_movable"})
            _require(expr, expr.name in self.tuples, "tuple needs local payload")
            layout = self.tuples[expr.name]
            _require(expr, unwrap_ref_type(unwrap_readonly(unwrap_ref_type(expr.result_type)))
                     == self.bindings[expr.name], "tuple name type mismatch")
        else:
            _require(expr, isinstance(expr, (th.THIRTupleLiteral, th.THIRBorrowTupleLiteral)),
                     "unsupported tuple expression")
            allowed = {"elements", "tuple_layout"}
            if isinstance(expr, th.THIRBorrowTupleLiteral):
                allowed |= {"spelled_cpp", "addr_of"}
                _require(expr, len(expr.addr_of) == len(expr.elements), "tuple address arity")
            _plain(expr, allowed)
            layout = expr.tuple_layout
            self.tuple_layout(expr, layout, expr.result_type, allow_owned=allow_owned)
            _require(expr, len(expr.elements) == len(layout.elements), "tuple capture arity")
            for element, member in zip(expr.elements, layout.elements):
                if isinstance(member, th.THIROwnedRecord):
                    _require(element, isinstance(expr, th.THIRTupleLiteral)
                             and isinstance(element, th.THIRCtorCall), "inline tuple record needs constructor")
                    self.record_value(element, member.type)
                elif isinstance(member, th.THIRBorrowedRecord):
                    _require(expr, isinstance(expr, th.THIRBorrowTupleLiteral), "value tuple borrows record")
                    name = self.reference_name(element)
                    source = self.references[name]
                    _require(element, source.type == member.type and (not source.readonly or member.readonly),
                             "tuple capture type or access mismatch")
                else:
                    _require(element, self.expr(element) == member and not self.writes[element],
                             "effectful or mistyped tuple element")
        self.tuple_layout(expr, layout, expr.result_type, allow_owned=allow_owned)
        _require(expr, expr.form is th.Form.STORAGE if layout.owns_records and isinstance(expr, th.THIRName)
                 else expr.form in (th.Form.VALUE, th.Form.BORROW), "tuple form disagrees with layout")
        self.tuple_exprs[expr] = layout
        return layout

    def projection(self, expr: th.THIRSubscript, *, capture: bool = False) -> TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord:
        _plain(expr, {"receiver", "index", "tuple_index"} | ({"deref"} if capture else set()))
        _require(expr, not capture or expr.deref, "tuple capture needs dereferenced element")
        layout = self.tuple_expr(expr.receiver, allow_owned=True)
        _require(expr, not layout.owns_records or isinstance(expr.receiver, th.THIRName),
                 "owned tuple projection needs existing local")
        index = expr.tuple_index
        _require(expr, type(index) is int and 0 <= index < len(layout.elements),
                 "missing or invalid tuple index")
        _require(expr, isinstance(expr.index, th.THIRLiteral)
                 and type(expr.index.value) is int and expr.index.value == index,
                 "tuple index disagreement")
        _plain(expr.index, {"value", "int_cpp"})
        member = layout.elements[index]
        if isinstance(member, (th.THIRBorrowedRecord, th.THIROwnedRecord)):
            self.reference(expr, th.THIRBorrowedRecord(member.type, member.readonly), expr.result_type)
            _require(expr, expr.form is th.Form.BORROW, "tuple reference projection form")
            if isinstance(member, th.THIROwnedRecord):
                _require(expr, not capture, "owned tuple element capture is unsupported")
        else:
            _require(expr, not capture, "tuple capture needs record element")
            _require(expr, expr.result_type == member and expr.form is th.Form.VALUE,
                     "tuple scalar projection type or form")
        return member

    def field(self, expr: th.THIRFieldAccess, *, write: bool = False) -> TpyType | th.THIRBorrowedRecord:
        _plain(expr, {"receiver", "field_cpp", "field_identity", "is_arrow"})
        match expr.receiver:
            case th.THIRFieldAccess():
                reference = self.field(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs record storage")
            case th.THIRNarrowedRead():
                reference = self.inline_union(expr.receiver)
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs union record")
            case th.THIRName() if expr.receiver.name in self.optionals:
                reference = self.optional_name(expr.receiver, extract=True).payload
                _require(expr, isinstance(reference, th.THIRBorrowedRecord), "field needs optional record")
            case th.THIRSubscript():
                reference = self.projection(expr.receiver)
                _require(expr, isinstance(reference, (th.THIRBorrowedRecord, th.THIROwnedRecord)),
                         "field needs tuple reference")
            case _:
                reference = self.references[self.reference_name(expr.receiver)]
        fact = expr.field_identity
        _require(expr, isinstance(fact, th.THIRFieldIdentity), "missing field identity")
        _require(expr, fact.owner == reference.type and bool(fact.name),
                 "field owner mismatch")
        readonly = reference.readonly or isinstance(fact.type, ReadonlyType)
        _require(expr, not (write and readonly), "readonly field store")
        if fact.type in (BOOL, INT32):
            _require(expr, expr.result_type == fact.type and expr.form is th.Form.VALUE,
                     "unsupported field type or form")
            return fact.type
        _require(expr, not write, "record field replacement is unsupported")
        member = th.THIRBorrowedRecord(unwrap_readonly(fact.type), readonly)
        self.reference(expr, member, expr.result_type)
        return member

    def expr(self, expr: th.THIRExpr) -> TpyType:
        _require(expr, expr.form is th.Form.VALUE, "unsupported expression form")
        typ = expr.result_type
        if isinstance(expr, th.THIRLiteral) and isinstance(typ, IntLiteralType):
            typ = INT32
        _require(expr, typ in (BOOL, INT32), "unsupported expression type")
        writing = False
        match expr:
            case th.THIRLiteral():
                _plain(expr, {"value", "int_cpp"})
                _require(expr, (typ == BOOL and type(expr.value) is bool)
                         or (typ == INT32 and type(expr.value) is int
                             and INT32_MIN <= expr.value <= INT32_MAX),
                         "unsupported literal value")
            case th.THIRNarrowedRead():
                _require(expr, self.inline_union(expr) == typ, "union scalar extraction required")
            case th.THIRIsinstance():
                _plain(expr, {"variant_cpp", "member_cpps", "union_test"})
                fact = expr.union_test
                _require(expr, typ == BOOL and isinstance(fact, th.THIRUnionTest)
                         and self.unions.get(fact.source) == fact.layout
                         and bool(fact.alternatives) and len(set(fact.alternatives)) == len(fact.alternatives)
                         and all(type(i) is int and 0 <= i < len(fact.layout.elements)
                                 for i in fact.alternatives), "missing or invalid union test")
            case th.THIRModuleVar():
                _plain(expr, {"cpp", "global_binding"})
                self.global_binding(expr)
            case th.THIRName() if expr.global_binding is not None:
                _plain(expr, {"name", "cpp", "global_binding", "is_last_use", "is_movable"})
                self.global_binding(expr)
            case th.THIRName():
                if expr.name in self.optionals:
                    _require(expr, self.optional_name(expr, extract=True).payload == typ,
                             "optional scalar extraction required")
                    self.writes[expr] = False
                    return typ
                _plain(expr, {"name", "is_last_use", "is_movable"})
                _require(expr, expr.name in self.bindings, "non-local name")
                _require(expr, self.bindings[expr.name] == typ, "name type mismatch")
            case th.THIRFieldAccess():
                self.field(expr)
            case th.THIRSubscript():
                _require(expr, self.projection(expr) == typ, "scalar tuple projection required")
            case th.THIRIsNone():
                if expr.union_monostate:
                    _plain(expr, {"operand", "negate", "union_monostate"})
                    _require(expr, isinstance(expr.operand, th.THIRName) and typ == BOOL
                             and type(expr.negate) is bool, "unsupported union None test")
                    layout = self.union_name(expr.operand)
                    _require(expr, None in layout.elements, "union has no absence alternative")
                    self.writes[expr] = False
                    return typ
                _plain(expr, {"operand", "negate", "value_repr"})
                _require(expr, typ == BOOL and type(expr.negate) is bool
                         and isinstance(expr.operand, th.THIRName), "unsupported optional test")
                layout = self.optional_name(expr.operand, extract=False)
                _require(expr, expr.value_repr is (not isinstance(layout.payload, th.THIRBorrowedRecord)),
                         "optional test representation mismatch")
            case th.THIRCoerce():
                _plain(expr, {"expr", "coercion_name"})
                _require(expr, typ == INT32
                         and expr.coercion_name == "int_literal_to_fixed_int"
                         and isinstance(expr.expr, th.THIRLiteral), "unsupported coercion")
                _require(expr, self.expr(expr.expr) == INT32, "unsupported literal coercion")
            case th.THIRWalrus():
                _plain(expr, {"name", "cpp_name", "value", "global_binding"})
                if expr.global_binding is not None:
                    self.global_binding(expr, write=True)
                else:
                    _require(expr, self.bindings.get(expr.name) == typ, "walrus needs existing scalar local")
                _require(expr, self.expr(expr.value) == typ, "walrus type mismatch")
                writing = True
            case th.THIRUnaryNot():
                _plain(expr, {"operand"})
                _require(expr, typ == BOOL and self.expr(expr.operand) == BOOL,
                         "not requires bool")
                writing = self.writes[expr.operand]
            case th.THIRBinOp():
                _plain(expr, {"left", "right", "op", "resolved", "paren_wrap"})
                _require(expr, expr.op in ("&&", "||", "==", "!=", "<", "<=", ">", ">="),
                         "unsupported binary operation")
                left, right = self.expr(expr.left), self.expr(expr.right)
                _require(expr, typ == BOOL and left == right, "comparison operand type mismatch")
                if expr.op in ("&&", "||"):
                    _require(expr, left == BOOL and expr.resolved is None, "unsupported boolean operation")
                else:
                    if ((self.writes[expr.left] and not _literal(expr.right))
                            or (self.writes[expr.right] and not _literal(expr.left))):
                        raise MIRUnsupported(expr, "order-sensitive eager operands")
                    rb = expr.resolved
                    if rb is not None:
                        expected = {"<": "__lt__", "<=": "__le__", ">": "__gt__",
                                    ">=": "__ge__", "==": "__eq__", "!=": "__ne__"}
                        _require(expr, rb.receiver_type in (BOOL, INT32)
                                 and rb.method.name in (expected[expr.op],
                                                        "__eq__" if expr.op == "!=" else expected[expr.op])
                                 and not rb.is_reverse
                                 and rb.left_wrapper == "{expr}" and rb.right_wrapper == "{expr}",
                                 "unsupported comparison dispatch")
                writing = self.writes[expr.left] or self.writes[expr.right]
            case th.THIRValueSelect():
                _plain(expr, {"lhs", "rhs", "op", "lhs_temp_cpp"})
                _require(expr, expr.op in ("&&", "||") and typ == BOOL
                         and self.expr(expr.lhs) == BOOL and self.expr(expr.rhs) == BOOL,
                         "unsupported value select")
                # A pure bool select never needs a representation-changing temp.
                _require(expr, expr.lhs_temp_cpp in (None, "auto&&"), "unsupported select temporary")
                writing = self.writes[expr.lhs] or self.writes[expr.rhs]
            case th.THIRIfExpr():
                _plain(expr, {"cond", "then", "orelse"})
                _require(expr, self.expr(expr.cond) == BOOL, "condition requires bool")
                _require(expr, self.expr(expr.then) == typ and self.expr(expr.orelse) == typ,
                         "conditional arm type mismatch")
                writing = any(self.writes[e] for e in (expr.cond, expr.then, expr.orelse))
            case _:
                raise MIRUnsupported(expr, "unsupported expression")
        self.writes[expr] = writing
        return typ

    def stmt(self, stmt: th.THIRStmt, loops: int) -> None:
        match stmt:
            case th.THIRNarrowAlias():
                _plain(stmt, {"alias", "variant_cpp", "member_cpp", "is_ptr_variant", "const_ref", "union_extraction"})
                member = self.union_extraction(stmt, stmt.union_extraction)
                reference = isinstance(member, th.THIRBorrowedRecord)
                _require(stmt, stmt.is_ptr_variant is reference, "union alias form mismatch")
                self.bindings[stmt.alias] = member.type if reference else member
                if reference:
                    self.references[stmt.alias] = member
                else:
                    self.payload_aliases.add(stmt.alias)
            case th.THIRAssign() if stmt.optional_record_assignment is not None:
                _plain(stmt, {"target", "value", "optional_record_assignment"})
                _require(stmt, isinstance(stmt.target, th.THIRName), "optional record assignment needs local")
                _plain(stmt.target, {"name"})
                fact = stmt.optional_record_assignment
                name = stmt.target.name
                _require(stmt, self.optional_record_storage.get(name) == fact
                         and self.bindings.get(name) == stmt.target.result_type == fact.type,
                         "optional record backing mismatch")
                _require(stmt, isinstance(stmt.value, th.THIRCtorCall), "optional record assignment needs constructor")
                self.record_value(stmt.value, fact.type)
                _require(stmt, self.records[fact.type].layout.movable, "optional backing needs movable record")
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.union_layout is not None:
                _require(stmt, isinstance(stmt, th.THIRAssign) and isinstance(stmt.target, th.THIRName),
                         "unsupported union reseat")
                _plain(stmt, {"target", "value", "union_layout", "union_literal"})
                _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                name = stmt.target.name
                _require(stmt, name in self.unions and name not in self.parameters
                         and stmt.union_layout == self.unions[name], "union destination mismatch")
                self.union_source(stmt.value, stmt.union_layout, stmt.union_literal)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.optional_layout is not None:
                if isinstance(stmt, th.THIRAssign):
                    _plain(stmt, {"target", "value", "optional_layout", "rebind_storage", "slot_cpp"})
                    _require(stmt, isinstance(stmt.target, th.THIRName), "optional assignment needs local")
                    _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                    name = stmt.target.name
                else:
                    _require(stmt, stmt.kind is th.PtrSlotKind.OPT_NONE, "unsupported optional reseat")
                    _plain(stmt, {"name", "kind", "optional_layout"})
                    name = stmt.name
                _require(stmt, name in self.optionals and name not in self.parameters
                         and stmt.optional_layout == self.optionals[name], "optional destination mismatch")
                if isinstance(stmt, th.THIRAssign):
                    member = stmt.optional_layout.payload
                    typ = member.type if isinstance(member, th.THIRBorrowedRecord) else member
                    actual = unwrap_readonly(unwrap_ref_type(stmt.target.result_type))
                    _require(stmt, actual in (typ, self.bindings[name]) or isinstance(actual, NoneType),
                             "optional destination type mismatch")
                if isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is not None:
                    member = stmt.optional_layout.payload
                    _require(stmt, isinstance(member, th.THIRBorrowedRecord)
                             and stmt.rebind_storage in (RebindStorage.OWN, RebindStorage.IN_PLACE),
                             "unsupported optional replacement storage")
                    _require(stmt, loops == 0 or stmt.rebind_storage is RebindStorage.OWN, "owning operation in loop")
                    _require(stmt, stmt.rebind_storage is not RebindStorage.IN_PLACE or not member.readonly,
                             "readonly in-place replacement")
                    _require(stmt, isinstance(stmt.value, th.THIRCtorCall), "replacement needs constructor")
                    self.record_value(stmt.value, member.type)
                    _require(stmt, self.records[member.type].layout.movable, "replacement needs movable record")
                else:
                    _require(stmt, not isinstance(stmt, th.THIRAssign) or stmt.slot_cpp is None,
                             "optional slot without replacement storage")
                    self.optional_source(stmt.value, stmt.optional_layout)
            case th.THIRAssign() if stmt.rebind_storage is not None:
                self.replacement(stmt, loops)
            case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                self.borrow_binding(stmt)
            case th.THIRNoOpStmt():
                _plain(stmt, set())
            case th.THIRAssign():
                _plain(stmt, {"target", "value"})
                if isinstance(stmt.target, th.THIRName) and stmt.target.global_binding is not None:
                    self.global_binding(stmt.target, write=True)
                if isinstance(stmt.target, th.THIRName) and stmt.target.name in self.tuples:
                    _require(stmt, stmt.target.name not in self.parameters, "tuple parameter reseat")
                    _plain(stmt.target, {"name", "is_last_use", "is_movable"})
                    _require(stmt, stmt.target.result_type == self.bindings[stmt.target.name],
                             "tuple destination type mismatch")
                    self.tuple_compatible(stmt, self.tuple_expr(stmt.value), self.tuples[stmt.target.name])
                    return
                _require(stmt, isinstance(stmt.target, (th.THIRName, th.THIRFieldAccess)),
                         "assignment needs local or field target")
                _require(stmt, not isinstance(stmt.target, th.THIRName) or stmt.target.name not in self.payload_aliases,
                         "write through scalar payload alias")
                target_type = (self.field(stmt.target, write=True) if isinstance(stmt.target, th.THIRFieldAccess)
                               else self.expr(stmt.target))
                _require(stmt, target_type == self.expr(stmt.value), "assignment type mismatch")
            case th.THIRExprStmt():
                _plain(stmt, {"expr", "void_cast"})
                self.expr(stmt.expr)
            case th.THIRReturn():
                _plain(stmt, {"value"})
                if stmt.value is None:
                    _require(stmt, isinstance(self.fn.return_type, VoidType), "missing return value")
                else:
                    _require(stmt, self.expr(stmt.value) == self.fn.return_type, "return type mismatch")
            case th.THIRIf() | th.THIRWhile():
                allowed = {"condition", "then_body", "else_body", "else_is_nested"} if isinstance(
                    stmt, th.THIRIf) else {"condition", "body", "orelse"}
                allowed |= {"hoist_decls", "hoisted_bindings"}
                _plain(stmt, allowed)
                self.hoists(stmt)
                _require(stmt, self.expr(stmt.condition) == BOOL, "condition requires bool")
                if isinstance(stmt, th.THIRIf):
                    for arm in (stmt.then_body, stmt.else_body):
                        self.scoped(arm, loops)
                else:
                    self.scoped(stmt.body, loops + 1)
                    self.scoped(stmt.orelse, loops)
            case th.THIRBreak() | th.THIRContinue():
                _plain(stmt, set())
                _require(stmt, loops > 0, "loop control outside loop")
            case _:
                raise MIRUnsupported(stmt, "unsupported statement")

    def hoists(self, stmt: th.THIRIf | th.THIRWhile) -> None:
        facts = stmt.hoisted_bindings
        _require(stmt, all(isinstance(f, th.THIRHoistedBinding) for f in facts)
                 and tuple(f.name for f in facts) == tuple(name for name, _ in stmt.hoist_decls),
                 "missing or inconsistent hoisted binding facts")
        for fact in facts:
            _require(stmt, fact.name not in self.bindings and fact.initially_assigned is False
                     and fact.placement is th.THIRStoragePlacement.SCOPE, "invalid hoisted binding placement or availability")
            layouts = (fact.borrowed_record, fact.optional_layout, fact.tuple_layout, fact.union_layout,
                       fact.optional_record_storage)
            _require(stmt, sum(f is not None for f in layouts) <= 1, "conflicting hoisted binding facts")
            if fact.optional_record_storage is not None:
                reference = fact.optional_record_storage
                self.reference(stmt, reference, fact.type)
                _require(stmt, not reference.readonly, "optional record backing must be mutable")
                definition = self.definitions.get(stmt, reference.type)
                _require(stmt, definition.layout.movable, "optional backing needs movable record")
                self.records[reference.type] = definition
                self.references[fact.name] = reference
                self.optional_record_storage[fact.name] = reference
                typ = reference.type
            elif fact.borrowed_record is not None:
                self.reference(stmt, fact.borrowed_record, fact.type)
                self.references[fact.name] = fact.borrowed_record
                typ = fact.borrowed_record.type
            elif fact.optional_layout is not None:
                self.optional_layout(stmt, fact.optional_layout, fact.type)
                self.optionals[fact.name] = fact.optional_layout
                typ = fact.type
            elif fact.union_layout is not None:
                self.union_layout(stmt, fact.union_layout, fact.type)
                _require(stmt, all(m is None or m in (BOOL, INT32) for m in fact.union_layout.elements),
                         "unsupported hoisted union storage")
                self.unions[fact.name] = fact.union_layout
                typ = fact.type
            elif fact.tuple_layout is not None:
                self.tuple_layout(stmt, fact.tuple_layout, fact.type)
                self.tuples[fact.name] = fact.tuple_layout
                typ = fact.type
            else:
                _require(stmt, fact.type in (BOOL, INT32), "unsupported hoisted value")
                typ = fact.type
            wrapper = (fact.union_layout is not None or fact.optional_layout is not None
                       and fact.optional_layout.payload in (BOOL, INT32))
            if wrapper:
                default = fact.physical_default
                first = fact.union_layout.elements[0] if fact.union_layout is not None else None
                expected = None if first is None else False if first == BOOL else 0
                _require(stmt, isinstance(default, th.THIRWrapperDefault)
                         and type(default.alternative) is int and default.alternative == 0
                         and type(default.value) is type(expected) and default.value == expected,
                         "missing or inconsistent physical wrapper default")
            else:
                _require(stmt, fact.physical_default is None, "physical default on non-wrapper hoist")
            self.bindings[fact.name] = typ

    def scoped(self, stmts: tuple[th.THIRStmt, ...], loops: int) -> None:
        saved = (self.bindings.copy(), self.references.copy(), self.payload_aliases.copy(),
                 self.tuples.copy(), self.optionals.copy(), self.unions.copy(), self.fixed_owned.copy(),
                 self.optional_record_storage.copy())
        self.declarations(stmts, loops)
        retained_fixed = saved[-2] & self.fixed_owned
        (self.bindings, self.references, self.payload_aliases,
         self.tuples, self.optionals, self.unions, self.fixed_owned, self.optional_record_storage) = saved
        self.fixed_owned = retained_fixed


@dataclass
class _Block:
    id: MIRBlockId
    region: MIRRegionId
    statements: list[MIRStatement] = field(default_factory=list)
    terminator: MIRTerminator | None = None


class _Builder:
    def __init__(self, body: MIRBodyId, fn: th.THIRFunction, coverage: _Coverage) -> None:
        self.body = body
        self.fn = fn
        self.slots: list[MIRSlot] = []
        self.constant_bools: dict[MIRSlotId, bool] = {}
        self.bindings: dict[str, MIRSlotId] = {}
        self.blocks: list[_Block] = []
        self.region = MIRRegionId(body, 0)
        self.regions = [MIRRegion(self.region, None, MIRBlockId(body, 0))]
        self.current: _Block | None = self.block()
        self.loops: list[tuple[MIRBlockId, MIRBlockId]] = []
        self.records = coverage.records
        self.tuple_exprs = coverage.tuple_exprs
        self.storage: dict[str, MIRSlotId] = {}
        self.global_facts = coverage.globals
        self.globals: dict[MIRGlobalId, MIRSlotId] = {}

    def block(self) -> _Block:
        block = _Block(MIRBlockId(self.body, len(self.blocks)), self.region)
        self.blocks.append(block)
        return block

    def slot(self, typ: TpyType, kind: MIRSlotKind = MIRSlotKind.TEMPORARY,
             name: str | None = None, reference: th.THIRBorrowedRecord | None = None,
             *, storage: bool = False, tuple_layout: th.THIRTupleLayout | None = None,
             optional_layout: th.THIROptionalLayout | None = None,
             union_layout: th.THIRUnionLayout | None = None,
             alias_source: MIRPlace | None = None,
             global_binding: th.THIRGlobalBinding | None = None,
             storage_duration: MIRStorageDuration | MIRRegionId | None = None,
             record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT) -> MIRSlotId:
        sid = MIRSlotId(self.body, len(self.slots))
        self.slots.append(MIRSlot(sid, reference.type if reference else (
            unwrap_ref_type(unwrap_readonly(unwrap_ref_type(typ)))
            if optional_layout is not None or union_layout is not None or tuple_layout is not None else typ), kind, name,
                                  form=(th.Form.STORAGE if storage or tuple_layout is not None and tuple_layout.owns_records
                                        else th.Form.BORROW if reference or alias_source else th.Form.VALUE),
                                  value_kind=(MIRValueKind.RECORD_STORAGE if storage else
                                              MIRValueKind.BORROWED_RECORD if reference else
                                              MIRValueKind.TUPLE if tuple_layout is not None else
                                              MIRValueKind.UNION if union_layout is not None else
                                              MIRValueKind.PAYLOAD_ALIAS if alias_source is not None else
                                              MIRValueKind.OPTIONAL if optional_layout is not None else MIRValueKind.SCALAR),
                                  readonly=(not global_binding.writable if global_binding is not None else
                                            reference.readonly if reference else alias_source is not None),
                                  tuple_layout=self.layout(tuple_layout) if tuple_layout is not None else None,
                                  optional_layout=self.optional_layout(optional_layout)
                                  if optional_layout is not None else None,
                                  union_layout=MIRUnionLayout(tuple(
                                      None if member is None else self.payload(member)
                                      for member in union_layout.elements)) if union_layout is not None else None,
                                  alias_source=alias_source,
                                  global_id=MIRGlobalId(global_binding.module, global_binding.name)
                                  if global_binding is not None else None,
                                  storage_duration=storage_duration,
                                  record_storage=record_storage,
                                  residence=(self.regions[0].id if storage_duration is MIRStorageDuration.BODY else
                                             storage_duration if storage and isinstance(storage_duration, MIRRegionId)
                                             else self.region) if kind in (MIRSlotKind.LOCAL, MIRSlotKind.TEMPORARY)
                                  else None))
        return sid

    @staticmethod
    def optional_layout(layout: th.THIROptionalLayout) -> MIROptionalLayout:
        member = layout.payload
        return (MIROptionalLayout(member.type, MIRValueKind.BORROWED_RECORD, member.readonly)
                if isinstance(member, th.THIRBorrowedRecord) else MIROptionalLayout(member))

    def optional_value(self, expr: th.THIRExpr | None) -> MIRRvalue:
        if expr is None or isinstance(expr, th.THIRLiteral) and expr.value is None:
            return MIROptionalConstruct()
        if _literal(expr):
            literal = expr.expr if isinstance(expr, th.THIRCoerce) else expr
            typ = BOOL if type(literal.value) is bool else INT32
            return MIROptionalConstruct(self.result(typ, MIRConstant(literal.value), expr.loc))
        if isinstance(expr, th.THIRName) and expr.optional_read is not None and not expr.optional_read.extract:
            return MIROptionalCopy(self.bindings[expr.name])
        if isinstance(expr, th.THIRName) and expr.result_type not in (BOOL, INT32):
            return MIROptionalConstruct(self.bindings[expr.name])
        return MIROptionalConstruct(self.expr(expr))

    @staticmethod
    def payload(member: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> MIRTupleElement:
        match member:
            case th.THIRBorrowedRecord():
                return MIRTupleElement(member.type, MIRValueKind.BORROWED_RECORD, member.readonly)
            case th.THIROwnedRecord():
                return MIRTupleElement(member.type, MIRValueKind.RECORD_STORAGE, member.readonly)
            case _:
                return MIRTupleElement(member)

    def layout(self, layout: th.THIRTupleLayout) -> MIRTupleLayout:
        return MIRTupleLayout(tuple(self.payload(member) for member in layout.elements))

    def union_value(self, expr: th.THIRExpr, layout: th.THIRUnionLayout,
                    literal: th.THIRUnionLiteral | None = None) -> MIRRvalue:
        if literal is not None:
            member = layout.elements[literal.alternative]
            source = None if member is None else self.result(member, MIRConstant(literal.value), expr.loc)
            return MIRUnionConstruct(literal.alternative, source)
        if isinstance(expr, th.THIRName) and expr.union_read is not None:
            return MIRUnionCopy(self.bindings[expr.name])
        if isinstance(expr, th.THIRLiteral) and expr.value is None:
            return MIRUnionConstruct(layout.elements.index(None))
        for index, member in enumerate(layout.elements):
            if isinstance(member, th.THIRBorrowedRecord) and isinstance(expr, th.THIRName):
                source = self.bindings[expr.name]
                if self.slots[source.index].type == member.type:
                    return MIRUnionConstruct(index, source)
            elif member == (INT32 if isinstance(expr.result_type, IntLiteralType) else expr.result_type):
                return MIRUnionConstruct(index, self.expr(expr))
        raise AssertionError("coverage and union construction disagree")

    def union_place(self, fact: th.THIRUnionExtraction) -> MIRPlace:
        return MIRPlace(self.bindings[fact.source], (MIRUnionPayload(fact.alternative),))

    def tuple_expr(self, expr: th.THIRExpr) -> MIRSlotId:
        dest = self.slot(expr.result_type, tuple_layout=self.tuple_exprs[expr])
        if isinstance(expr, th.THIRName):
            value = MIRTupleCopy(self.bindings[expr.name])
        else:
            value = MIRTupleConstruct(tuple(
                self.place(e).root if isinstance(member, th.THIRBorrowedRecord) else self.expr(e)
                for e, member in zip(expr.elements, self.tuple_exprs[expr].elements)))
        self.write(dest, value, expr.loc)
        return dest

    def write(self, dest: MIRSlotId | MIRPlace, value: MIRRvalue, loc: SourceLocation | None,
              storage_write: MIRRecordWrite | MIRPayloadWrite | MIRTupleInitialization | None = None) -> None:
        assert self.current is not None
        target = MIRPlace(dest) if isinstance(dest, MIRSlotId) else dest
        self.current.statements.append(MIRAssign(target, value, loc, storage_write))

    def payload_write(self, dest: MIRSlotId, mode: MIRPayloadWriteMode) -> MIRPayloadWrite | None:
        if mode is MIRPayloadWriteMode.INITIALIZE and self.region.index != 0:
            mode = MIRPayloadWriteMode.INITIALIZE_REGION
        return MIRPayloadWrite(mode) if scalar_wrapper(self.slots[dest.index]) else None

    def place(self, expr: th.THIRExpr) -> MIRPlace:
        if isinstance(expr, (th.THIRName, th.THIRModuleVar)) and expr.global_binding is not None:
            fact = expr.global_binding
            return MIRPlace(self.globals[MIRGlobalId(fact.module, fact.name)])
        match expr:
            case th.THIRSelf():
                return MIRPlace(self.bindings["self"])
            case th.THIRNarrowedRead():
                return self.union_place(expr.union_extraction)
            case th.THIRName():
                if expr.optional_read is not None and expr.optional_read.extract:
                    return MIRPlace(self.bindings[expr.name], (MIROptionalPayload(),))
                return MIRPlace(self.bindings[expr.name])
            case th.THIRSubscript():
                root = (self.bindings[expr.receiver.name] if isinstance(expr.receiver, th.THIRName)
                        else self.tuple_expr(expr.receiver))
                return MIRPlace(root, (MIRTupleIndex(expr.tuple_index),))
            case _:
                assert isinstance(expr, th.THIRFieldAccess) and expr.field_identity is not None
                member = expr.field_identity
                base = self.place(expr.receiver)
                inline = isinstance(expr.receiver, th.THIRFieldAccess) or (
                    isinstance(expr.receiver, th.THIRSubscript) and isinstance(
                        self.tuple_exprs[expr.receiver.receiver].elements[expr.receiver.tuple_index], th.THIROwnedRecord))
                deref = () if inline else (MIRDeref(),)
                return MIRPlace(base.root, base.projections + deref + (
                    MIRField(MIRFieldId(member.owner, member.name), member.type),))

    def end(self, term: MIRTerminator) -> None:
        assert self.current is not None and self.current.terminator is None
        self.current.terminator = term
        self.current = None

    def result(self, typ: TpyType, value: MIRRvalue, loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        self.write(dest, value, loc)
        # Only these expression temporaries have one definition; locals and select results can change.
        if typ == BOOL:
            match value:
                case MIRConstant(value=constant) if type(constant) is bool:
                    self.constant_bools[dest] = constant
                case MIRNot(operand=operand) if operand in self.constant_bools:
                    self.constant_bools[dest] = not self.constant_bools[operand]
        return dest

    def branch(self, cond: MIRSlotId, yes: MIRBlockId, no: MIRBlockId,
               loc: SourceLocation | None) -> None:
        if cond in self.constant_bools:
            self.end(MIRGoto(yes if self.constant_bools[cond] else no, loc))
        else:
            self.end(MIRBranch(cond, yes, no, loc))

    def select(self, cond: MIRSlotId, then: th.THIRExpr | MIRSlotId,
               otherwise: th.THIRExpr | MIRSlotId, typ: TpyType,
               loc: SourceLocation | None) -> MIRSlotId:
        dest = self.slot(typ)
        yes, no, join = self.block(), self.block(), self.block()
        self.branch(cond, yes.id, no.id, loc)
        for block, arm in ((yes, then), (no, otherwise)):
            self.current = block
            value = arm if isinstance(arm, MIRSlotId) else self.expr(arm)
            self.write(dest, MIRRead(MIRPlace(value)), loc)
            self.end(MIRGoto(join.id, loc))
        self.current = join
        return dest

    def expr(self, expr: th.THIRExpr) -> MIRSlotId:
        typ = INT32 if isinstance(expr.result_type, IntLiteralType) else expr.result_type
        loc = expr.loc
        match expr:
            case th.THIRLiteral():
                return self.result(typ, MIRConstant(expr.value), loc)
            case th.THIRCoerce():
                return self.result(INT32, MIRConstant(expr.expr.value), loc)
            case th.THIRName() | th.THIRModuleVar() | th.THIRFieldAccess() | th.THIRSubscript() | th.THIRNarrowedRead():
                return self.result(typ, MIRRead(self.place(expr)), loc)
            case th.THIRIsinstance():
                fact = expr.union_test
                return self.result(BOOL, MIRIsAlternative(self.bindings[fact.source], fact.alternatives), loc)
            case th.THIRWalrus():
                value = self.expr(expr.value)
                fact = expr.global_binding
                target = (self.globals[MIRGlobalId(fact.module, fact.name)] if fact is not None
                          else self.bindings[expr.name])
                self.write(target, MIRRead(MIRPlace(value)), loc)
                return value
            case th.THIRUnaryNot():
                return self.result(BOOL, MIRNot(self.expr(expr.operand)), loc)
            case th.THIRIsNone():
                if expr.union_monostate:
                    fact = expr.operand.union_read
                    absent = self.result(BOOL, MIRIsAlternative(self.bindings[expr.operand.name],
                                                              (fact.elements.index(None),)), loc)
                    return self.result(BOOL, MIRNot(absent), loc) if expr.negate else absent
                present = self.result(BOOL, MIRIsPresent(self.bindings[expr.operand.name]), loc)
                return present if expr.negate else self.result(BOOL, MIRNot(present), loc)
            case th.THIRBinOp():
                left = self.expr(expr.left)
                if expr.op in ("&&", "||"):
                    return self.select(left, expr.right if expr.op == "&&" else left,
                                       left if expr.op == "&&" else expr.right, BOOL, loc)
                right = self.expr(expr.right)
                return self.result(BOOL, MIRCompare(expr.op, left, right), loc)
            case th.THIRValueSelect():
                left = self.expr(expr.lhs)
                return self.select(left, expr.rhs if expr.op == "&&" else left,
                                   left if expr.op == "&&" else expr.rhs, BOOL, loc)
            case th.THIRIfExpr():
                return self.select(self.expr(expr.cond), expr.then, expr.orelse, typ, loc)
            case _:
                raise AssertionError("coverage and expression lowering disagree")

    def record_value(self, expr: th.THIRExpr) -> MIRRvalue:
        match expr:
            case th.THIRCtorCall():
                definition = self.records[expr.result_type]
                args = {p.name: self.expr(arg) for p, arg in zip(definition.constructor.params, expr.args)}
                members = {}
                for mil in definition.constructor.mil_inits:
                    value = mil.value
                    members[mil.field_identity.name] = (args[value.name] if isinstance(value, th.THIRName)
                                                         else self.expr(value))
                return MIRConstruct(tuple(members[f.id.name] for f in definition.layout.fields))
            case th.THIRCopy():
                return MIRCopy(MIRPlace(self.place(expr.value).root, (MIRDeref(),)))
            case _:
                assert isinstance(expr, th.THIRMove)
                return MIRMove(self.storage[expr.value.name])

    def initial_mode(self) -> MIRRecordWriteMode:
        return (MIRRecordWriteMode.INITIALIZE_ONCE if self.region.index == 0
                else MIRRecordWriteMode.INITIALIZE_REGION)

    def placement(self, stmt: th.THIRVarDecl | th.THIRPtrLocalDecl) -> MIRRegionId | MIRStorageDuration:
        if (isinstance(stmt, th.THIRPtrLocalDecl) and stmt.kind is th.PtrSlotKind.RECORD_HOISTED
                and stmt.storage_placement is th.THIRStoragePlacement.BODY):
            return MIRStorageDuration.BODY
        _require(stmt, stmt.storage_placement is th.THIRStoragePlacement.SCOPE,
                 "missing direct storage placement")
        return self.region if self.region.index else MIRStorageDuration.BODY

    def optional_record(self, expr: th.THIRCtorCall, fact: th.THIRBorrowedRecord,
                        mode: MIRRecordWriteMode) -> MIRRvalue:
        storage = self.slot(fact.type, storage=True, storage_duration=(self.region
                            if mode is MIRRecordWriteMode.INITIALIZE_REGION else MIRStorageDuration.BODY))
        self.write(storage, self.record_value(expr), expr.loc, MIRRecordWrite(mode))
        reference = self.slot(fact.type, reference=fact)
        self.write(reference, MIRBorrow(MIRPlace(storage)), expr.loc)
        return MIROptionalConstruct(reference)

    def stmts(self, stmts: tuple[th.THIRStmt, ...]) -> None:
        for stmt in stmts:
            if self.current is None:
                # Coverage already inspected every retained unreachable node.
                break
            loc = stmt.loc
            match stmt:
                case th.THIRNoOpStmt():
                    continue
                case th.THIRNarrowAlias():
                    fact = stmt.union_extraction
                    member = fact.layout.elements[fact.alternative]
                    source = self.union_place(fact)
                    reference = member if isinstance(member, th.THIRBorrowedRecord) else None
                    dest = self.slot(reference.type if reference else member, MIRSlotKind.LOCAL,
                                     stmt.alias, reference, alias_source=None if reference else source)
                    self.write(dest, MIRUnionExtract(source), loc)
                    self.bindings[stmt.alias] = dest
                case th.THIRVarDecl() if stmt.union_layout is not None:
                    dest = self.slot(stmt.union_layout.type, MIRSlotKind.LOCAL, stmt.name,
                                     union_layout=stmt.union_layout, storage_duration=(self.placement(stmt)
                                         if all(m is None or m in (BOOL, INT32)
                                                for m in stmt.union_layout.elements) else None))
                    self.write(dest, self.union_value(stmt.init, stmt.union_layout, stmt.union_literal), loc,
                               self.payload_write(dest, MIRPayloadWriteMode.INITIALIZE))
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() if stmt.union_layout is not None:
                    dest = self.bindings[stmt.target.name]
                    self.write(dest, self.union_value(stmt.value, stmt.union_layout, stmt.union_literal), loc,
                               self.payload_write(dest, MIRPayloadWriteMode.ASSIGN))
                case th.THIRVarDecl() | th.THIRPtrLocalDecl() if stmt.optional_layout is not None:
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name,
                                     optional_layout=stmt.optional_layout, storage_duration=(self.placement(stmt)
                                         if stmt.optional_layout.payload in (BOOL, INT32) else None))
                    if stmt.owned_storage is not None:
                        self.placement(stmt)
                    value = (self.optional_record(stmt.init, stmt.owned_storage, self.initial_mode())
                             if stmt.owned_storage is not None else self.optional_value(stmt.init))
                    self.write(dest, value, loc, self.payload_write(dest, MIRPayloadWriteMode.INITIALIZE))
                    self.bindings[stmt.name] = dest
                case th.THIRAssign() | th.THIRPtrLocalRebind() if stmt.optional_layout is not None:
                    name = stmt.target.name if isinstance(stmt, th.THIRAssign) else stmt.name
                    dest = self.bindings[name]
                    if isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is RebindStorage.OWN:
                        self.write(dest, self.optional_record(stmt.value, stmt.optional_layout.payload,
                                                             MIRRecordWriteMode.OWN_SITE), loc)
                    elif isinstance(stmt, th.THIRAssign) and stmt.rebind_storage is RebindStorage.IN_PLACE:
                        self.write(MIRPlace(dest, (MIROptionalPayload(), MIRDeref())),
                                   self.record_value(stmt.value), loc, MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, dest))
                    else:
                        self.write(dest, self.optional_value(stmt.value), loc,
                                   self.payload_write(dest, MIRPayloadWriteMode.ASSIGN))
                case th.THIRVarDecl() | th.THIRPtrLocalDecl() if stmt.owned_storage is not None:
                    fact = stmt.owned_storage
                    storage = self.slot(fact.type, storage=True, storage_duration=self.placement(stmt))
                    mode = (MIRRecordWriteMode.OWN_SITE if isinstance(stmt, th.THIRPtrLocalDecl)
                            and stmt.kind is th.PtrSlotKind.RECORD_HOISTED else self.initial_mode())
                    self.write(storage, self.record_value(stmt.init), loc, MIRRecordWrite(mode))
                    holder = self.slot(fact.type, MIRSlotKind.LOCAL, stmt.name, fact)
                    self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                    self.storage[stmt.name] = storage
                    self.bindings[stmt.name] = holder
                case th.THIRAssign() if stmt.optional_record_assignment is not None:
                    name = stmt.target.name
                    storage = self.storage[name]
                    self.write(storage, self.record_value(stmt.value), loc,
                               MIRRecordWrite(MIRRecordWriteMode.OPTIONAL_ASSIGN))
                    self.write(self.bindings[name], MIRBorrow(MIRPlace(storage)), loc)
                case th.THIRAssign() if stmt.rebind_storage is not None:
                    holder = self.bindings[stmt.target.name]
                    value = self.record_value(stmt.value)
                    if stmt.rebind_storage is RebindStorage.OWN:
                        storage = self.slot(self.slots[holder.index].type, storage=True,
                                            storage_duration=MIRStorageDuration.BODY)
                        self.write(storage, value, loc, MIRRecordWrite(MIRRecordWriteMode.OWN_SITE))
                        self.write(holder, MIRBorrow(MIRPlace(storage)), loc)
                    else:
                        self.write(MIRPlace(holder, (MIRDeref(),)), value, loc,
                                   MIRRecordWrite(MIRRecordWriteMode.IN_PLACE, holder))
                case (th.THIRVarDecl() | th.THIRPtrLocalDecl() | th.THIRAssign()
                      | th.THIRPtrLocalRebind()) if stmt.alias_binding is not None or stmt.storage_borrow is not None:
                    declaration = isinstance(stmt, (th.THIRVarDecl, th.THIRPtrLocalDecl))
                    if stmt.storage_borrow is not None:
                        fact = stmt.storage_borrow
                        source = stmt.init if declaration else stmt.value
                        if isinstance(source, th.THIRFormConvert):
                            source = source.value
                        value = MIRBorrow(self.place(source))
                        if isinstance(source, th.THIRSubscript):
                            value = MIRBorrow(MIRPlace(value.source.root, (*value.source.projections, MIRDeref())))
                    else:
                        fact = stmt.alias_binding.reference
                        value = MIRAlias(self.bindings[stmt.alias_binding.source])
                    name = stmt.target.name if isinstance(stmt, th.THIRAssign) else stmt.name
                    if declaration:
                        self.bindings[name] = self.slot(fact.type, MIRSlotKind.LOCAL, name, fact)
                    self.write(self.bindings[name], value, loc)
                case th.THIRVarDecl() if stmt.tuple_layout is not None:
                    owning = stmt.tuple_layout.owns_records
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name,
                                     tuple_layout=stmt.tuple_layout,
                                     storage_duration=self.placement(stmt) if owning else None)
                    if owning:
                        value = MIRTupleConstruct(tuple(self.record_value(e) if isinstance(m, th.THIROwnedRecord)
                                                        else self.expr(e)
                                                        for e, m in zip(stmt.init.elements, stmt.tuple_layout.elements)))
                        self.write(dest, value, loc, MIRTupleInitialization())
                    else:
                        self.write(dest, MIRTupleCopy(self.tuple_expr(stmt.init)), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRVarDecl():
                    dest = self.slot(stmt.resolved_type, MIRSlotKind.LOCAL, stmt.name)
                    if stmt.init is not None:
                        self.write(dest, MIRRead(MIRPlace(self.expr(stmt.init))), loc)
                    self.bindings[stmt.name] = dest
                case th.THIRAssign():
                    if isinstance(stmt.target.result_type, TupleType):
                        self.write(self.place(stmt.target), MIRTupleCopy(self.tuple_expr(stmt.value)), loc)
                    else:
                        self.write(self.place(stmt.target), MIRRead(MIRPlace(self.expr(stmt.value))), loc)
                case th.THIRExprStmt():
                    self.expr(stmt.expr)
                case th.THIRReturn():
                    self.end(MIRReturn(self.expr(stmt.value) if stmt.value is not None else None, loc))
                case th.THIRIf():
                    self.hoists(stmt)
                    cond = self.expr(stmt.condition)
                    yes, no = self.block(), self.block()
                    self.branch(cond, yes.id, no.id, loc)
                    exits = []
                    for block, arm in ((yes, stmt.then_body), (no, stmt.else_body)):
                        self.current = block
                        self.scoped(arm)
                        if self.current is not None:
                            exits.append(self.current)
                    if exits:
                        join = self.block()
                        for block in exits:
                            self.current = block
                            self.end(MIRGoto(join.id, loc))
                        self.current = join
                    else:
                        self.current = None
                case th.THIRWhile():
                    self.hoists(stmt)
                    cond_block, body, normal, after = self.block(), self.block(), self.block(), self.block()
                    self.end(MIRGoto(cond_block.id, loc))
                    self.current = cond_block
                    self.branch(self.expr(stmt.condition), body.id, normal.id, loc)
                    self.current = body
                    self.loops.append((cond_block.id, after.id))
                    self.scoped(stmt.body)
                    self.loops.pop()
                    if self.current is not None:
                        self.end(MIRGoto(cond_block.id, loc))
                    self.current = normal
                    self.scoped(stmt.orelse)
                    if self.current is not None:
                        self.end(MIRGoto(after.id, loc))
                    self.current = after
                case th.THIRBreak():
                    self.end(MIRGoto(self.loops[-1][1], loc))
                case th.THIRContinue():
                    self.end(MIRGoto(self.loops[-1][0], loc))
                case _:
                    raise AssertionError("coverage and statement lowering disagree")

    def hoists(self, stmt: th.THIRIf | th.THIRWhile) -> None:
        for fact in stmt.hoisted_bindings:
            if fact.optional_record_storage is not None:
                reference = fact.optional_record_storage
                storage = self.slot(reference.type, MIRSlotKind.LOCAL, storage=True,
                                    storage_duration=self.region if self.region.index else MIRStorageDuration.BODY,
                                    record_storage=MIRRecordStorageKind.OPTIONAL)
                self.current.statements.append(MIRRecordStorageInit(MIRPlace(storage), stmt.loc))
                self.storage[fact.name] = storage
                self.bindings[fact.name] = self.slot(reference.type, MIRSlotKind.LOCAL, fact.name, reference)
                continue
            default = fact.physical_default
            dest = self.slot(
                fact.type, MIRSlotKind.LOCAL, fact.name, fact.borrowed_record,
                optional_layout=fact.optional_layout, tuple_layout=fact.tuple_layout,
                union_layout=fact.union_layout, storage_duration=(
                    self.region if self.region.index else MIRStorageDuration.BODY) if default is not None else None)
            self.bindings[fact.name] = dest
            if default is not None:
                self.current.statements.append(MIRStorageInit(MIRPlace(dest), default.alternative,
                                                              MIRConstant(default.value), stmt.loc))

    def scoped(self, stmts: tuple[th.THIRStmt, ...]) -> None:
        bindings, storage, region = self.bindings.copy(), self.storage.copy(), self.region
        assert self.current is not None and not self.current.statements
        self.region = MIRRegionId(self.body, len(self.regions))
        self.regions.append(MIRRegion(self.region, region, self.current.id))
        self.current.region = self.region
        self.stmts(stmts)
        self.bindings, self.storage, self.region = bindings, storage, region

    def build(self, initialization: MIRConstructorDefinition | None = None) -> MIRFunction:
        if self.fn.receiver is not None:
            self.bindings["self"] = self.slot(self.fn.receiver.type, MIRSlotKind.PARAMETER,
                                              "self", self.fn.receiver)
        for p in self.fn.params:
            self.bindings[p.name] = self.slot(p.type, MIRSlotKind.PARAMETER, p.name,
                                               p.borrowed_record, optional_layout=p.optional_layout,
                                               union_layout=p.union_layout, tuple_layout=p.tuple_layout,
                                               storage_duration=MIRStorageDuration.CALLER
                                               if p.union_layout is not None and all(
                                                   member is None or member in (BOOL, INT32)
                                                   for member in p.union_layout.elements) else None)
        for identity, fact in self.global_facts.items():
            self.globals[identity] = self.slot(fact.type, MIRSlotKind.GLOBAL, fact.name, global_binding=fact)
        receiver_init = None
        if initialization is not None:
            values: dict[str, MIRSlotId | MIRConstant] = {}
            for mil in initialization.constructor.mil_inits:
                value = mil.value.expr if isinstance(mil.value, th.THIRCoerce) else mil.value
                values[mil.field_identity.name] = (self.bindings[value.name] if isinstance(value, th.THIRName)
                                                   else MIRConstant(value.value))
            receiver_init = MIRReceiverInit(self.bindings["self"], tuple(
                values[f.id.name] for f in initialization.layout.fields))
        self.stmts(self.fn.body)
        reachable: set[MIRBlockId] = set()
        pending = [self.blocks[0].id]
        while pending:
            bid = pending.pop()
            if bid in reachable:
                continue
            reachable.add(bid)
            term = self.blocks[bid.index].terminator
            if term is not None:
                pending.extend(successors(term))
        if self.current is not None and self.current.id in reachable:
            _require(self.fn, isinstance(self.fn.return_type, VoidType), "non-void fallthrough")
            self.end(MIRReturn())
        blocks = []
        for b in self.blocks:
            if b.id not in reachable:
                continue
            assert b.terminator is not None
            blocks.append(MIRBlock(b.id, tuple(b.statements), b.terminator, b.region))
        kind = (MIRBodyKind.CONSTRUCTOR if initialization is not None else
                MIRBodyKind.METHOD if self.fn.receiver is not None else MIRBodyKind.FREE_FUNCTION)
        fn = MIRFunction(self.body, self.fn.return_type, self.reachable_slots(blocks, receiver_init), tuple(blocks),
                         self.blocks[0].id, tuple(d.layout for d in self.records.values()), receiver_init, kind,
                         tuple(r for r in self.regions if r.entry in reachable))
        validate_function(fn)
        return fn

    def reachable_slots(self, blocks: list[MIRBlock], receiver: MIRReceiverInit | None) -> tuple[MIRSlot, ...]:
        retained = {s.id for s in self.slots if s.kind in (MIRSlotKind.PARAMETER, MIRSlotKind.GLOBAL)}
        if receiver is not None:
            retained.add(receiver.receiver)
            retained.update(v for v in receiver.fields if isinstance(v, MIRSlotId))
        for block in blocks:
            for stmt in block.statements:
                retained.add(stmt.target.root)
                retained.update(statement_reads(stmt))
                if (isinstance(stmt, MIRAssign) and isinstance(stmt.storage_write, MIRRecordWrite)
                        and stmt.storage_write.rebind_owner is not None):
                    retained.add(stmt.storage_write.rebind_owner)
            match block.terminator:
                case MIRBranch(condition=condition):
                    retained.add(condition)
                case MIRReturn(value=value) if value is not None:
                    retained.add(value)
        pending = list(retained)
        while pending:
            source = self.slots[pending.pop().index].alias_source
            if source is not None and source.root not in retained:
                retained.add(source.root)
                pending.append(source.root)
        # Preserve IDs: consumers key by identity, and dead branches can leave gaps.
        return tuple(s for s in self.slots if s.id in retained)


def lower_function(fn: th.THIRFunction, body: MIRBodyId, *,
                   kind: MIRBodyKind, definitions: MIRDefinitions | None = None) -> MIRFunction | MIRNotCovered:
    """The caller supplies declaration kind; eligible methods also carry a receiver."""
    try:
        _require(fn, kind in (MIRBodyKind.FREE_FUNCTION, MIRBodyKind.METHOD), "unsupported body kind")
        _require(fn, (kind is MIRBodyKind.METHOD) == (fn.receiver is not None),
                 "body kind and receiver mismatch")
        coverage = _Coverage(fn, definitions if definitions is not None else MIRDefinitions())
        coverage.check()
        try:
            return _Builder(body, fn, coverage).build()
        except (MIRPresenceError, MIRDefiniteAssignmentError) as failure:
            raise MIRUnsupported(fn, str(failure)) from failure
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))


def lower_constructor(ctor: th.THIRConstructor, body: MIRBodyId, *,
                      definitions: MIRDefinitions | None = None) -> MIRFunction | MIRNotCovered:
    """Lower complete pure initialization before a supported constructor tail."""
    try:
        initialization = constructor_initialization(ctor)
        fn = th.THIRFunction(
            f"{ctor.record_name}.__init__", ctor.params, VoidType(), ctor.body, th.THIRFunctionLayout(),
            receiver=th.THIRBorrowedRecord(initialization.layout.type, False))
        coverage = _Coverage(fn, definitions if definitions is not None else MIRDefinitions())
        coverage.check()
        coverage.records[initialization.layout.type] = initialization
        try:
            return _Builder(body, fn, coverage).build(initialization)
        except (MIRPresenceError, MIRDefiniteAssignmentError) as failure:
            raise MIRUnsupported(ctor, str(failure)) from failure
    except MIRUnsupported as failure:
        return MIRNotCovered(body, type(failure.node).__name__, failure.reason,
                             getattr(failure.node, "loc", None))
